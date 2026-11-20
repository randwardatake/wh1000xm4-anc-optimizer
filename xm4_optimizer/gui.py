"""Unified Tkinter desktop app for the Sony WH-1000XM4.

Run with ``python -m xm4_optimizer.gui`` or the ``xm4-optimizer-gui`` launcher.

The everyday panels (status, ANC, firmware, EQ APO, backup) are adapted from the
desktop app contributed by abdulhajiyev (PR #4, see CREDITS.md). This version also
exposes the MDR settings, the persistent NVDM item write path, coefficient
injection, the experimental spatial pipeline and the zero-byte dry run. Every
write needs an explicit button click and a confirmation dialog; nothing is
written automatically. Long Bluetooth calls run on background threads.

Firmware installs are limited to the official images in ``firmware.py`` and need
at least 50% battery. The headset is never written to from this module directly:
all protocol work goes through the existing public APIs in ``cli``, ``tools``,
and ``xm4_optimizer.transport``.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:  # pragma: no cover - minimal Python without Tk installed
    tk = None
    filedialog = messagebox = ttk = None

from xm4_optimizer import backup_check, cli, eqapo, firmware_store
from xm4_optimizer import gui_helpers as helpers
from xm4_optimizer.paths import BACKUP_DIR, REPO_ROOT
from xm4_optimizer.transport.rfcomm_socket import find_connected_xm4_mac
from xm4_optimizer.transport.sony_control import (
    AmbientSoundState,
    AncMode,
    SonyControlClient,
    SonyControlError,
)

try:  # the link check was added with the cross-platform transport work
    from xm4_optimizer.transport.rfcomm_socket import is_link_connected
except ImportError:  # pragma: no cover - older transports
    is_link_connected = None

from tools.backup_nvdm import dump_nvdm_bytes
from tools.mdr_nvdm_write import send_and_listen

MIN_FLASH_BATTERY = helpers.MIN_FLASH_BATTERY
ANC_CHOICES = helpers.ANC_CHOICES
describe_anc = helpers.describe_anc


class _QueueWriter:
    """File-like object that forwards printed text to the log queue.

    Lines starting with a carriage return are in-place progress updates; they go
    to the progress label instead of the log so they do not flood it.
    """

    def __init__(
        self, q: "queue.Queue[str]", progress: Optional[Callable[[str], None]] = None
    ) -> None:
        self._q = q
        self._progress = progress

    def write(self, s: str) -> int:
        if s.startswith("\r") and self._progress is not None:
            self._progress(s.strip())
        elif s:
            self._q.put(s.replace("\r", ""))
        return len(s)

    def flush(self) -> None:
        pass


_TkBase = tk.Tk if tk is not None else object


class App(_TkBase):
    def __init__(self) -> None:
        super().__init__()
        self.title("XM4 Control - Sony WH-1000XM4")
        self.resizable(True, True)
        self.mac: Optional[str] = None
        self.firmware: Optional[str] = None
        self.battery: Optional[int] = None
        self._busy = False
        self._log_q: "queue.Queue[str]" = queue.Queue()
        self._buttons: list = []
        self._chosen_files: dict[str, Path] = {}
        self._flashing = False

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._load_eq_state()
        self.after(100, self._drain_log)
        self.after(200, self.refresh_status)

    # ---------- layout ----------

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 6}
        root = ttk.Frame(self, padding=8)
        root.grid(sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)

        tabs = ttk.Notebook(root)
        tabs.grid(row=0, column=0, sticky="nsew")
        headset = ttk.Frame(tabs, padding=8)
        settings = ttk.Frame(tabs, padding=8)
        firmware = ttk.Frame(tabs, padding=8)
        nvdm = ttk.Frame(tabs, padding=8)
        coeff = ttk.Frame(tabs, padding=8)
        spatial = ttk.Frame(tabs, padding=8)
        for frame, label in (
            (headset, "Headset"), (settings, "Settings"), (firmware, "Firmware"),
            (nvdm, "Backup & NVDM"), (coeff, "ANC coefficients"),
            (spatial, "Spatial (experimental)"),
        ):
            tabs.add(frame, text=label)

        self._build_headset(headset, pad)
        self._build_settings(settings, pad)
        self._build_firmware(firmware, pad)
        self._build_nvdm(nvdm, pad)
        self._build_coefficients(coeff, pad)
        self._build_spatial(spatial, pad)
        self._build_log(root)

    def _build_headset(self, tab: "ttk.Frame", pad: dict) -> None:
        box = ttk.LabelFrame(tab, text="Headset", padding=8)
        box.grid(row=0, column=0, sticky="ew", **pad)
        ttk.Label(box, text="MAC (blank = auto-detect):").grid(row=0, column=0, sticky="w")
        self.mac_var = tk.StringVar(value="")
        ttk.Entry(box, textvariable=self.mac_var, width=22).grid(row=0, column=1, sticky="w", padx=(6, 0))
        self._button(box, "Refresh", self.refresh_status).grid(row=0, column=2, sticky="e", padx=(10, 0))
        self.status_vars = {
            k: tk.StringVar(value="…") for k in ("Device", "Firmware", "Battery", "ANC")
        }
        for i, (name, var) in enumerate(self.status_vars.items(), start=1):
            ttk.Label(box, text=f"{name}:").grid(row=i, column=0, sticky="w")
            ttk.Label(box, textvariable=var, width=42).grid(row=i, column=1, columnspan=2, sticky="w", padx=(6, 0))

        box = ttk.LabelFrame(tab, text="Noise control", padding=8)
        box.grid(row=1, column=0, sticky="ew", **pad)
        self.anc_mode = tk.StringVar(value=AncMode.NOISE_CANCELLING.value)
        for i, (mode, label) in enumerate(ANC_CHOICES):
            ttk.Radiobutton(box, text=label, value=mode.value, variable=self.anc_mode,
                            command=self._sync_ambient_controls).grid(row=0, column=i, sticky="w", padx=(0, 8))
        ttk.Label(box, text="Ambient level:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.ambient_level = tk.IntVar(value=10)
        self.level_scale = ttk.Scale(box, from_=1, to=20, orient="horizontal", length=160,
                                     command=lambda v: self.ambient_level.set(round(float(v))))
        self.level_scale.set(10)
        self.level_scale.grid(row=1, column=1, sticky="w", pady=(8, 0))
        ttk.Label(box, textvariable=self.ambient_level, width=3).grid(row=1, column=2, sticky="e", pady=(8, 0))
        self.voice = tk.BooleanVar(value=False)
        self.voice_check = ttk.Checkbutton(box, text="Focus on voice", variable=self.voice)
        self.voice_check.grid(row=1, column=3, sticky="w", pady=(8, 0))
        self._button(box, "Apply", self.apply_anc).grid(row=2, column=3, sticky="e", pady=(8, 0))
        self._sync_ambient_controls()

        note = ttk.Label(tab, text="Status and ANC are read-only until you press Apply. "
                                   "Connect the headset in your OS Bluetooth settings first.",
                         foreground="#555", wraplength=560, justify="left")
        note.grid(row=2, column=0, sticky="w", **pad)

    def _build_settings(self, tab: "ttk.Frame", pad: dict) -> None:
        box = ttk.LabelFrame(tab, text="MDR headset settings", padding=8)
        box.grid(row=0, column=0, sticky="ew", **pad)
        ttk.Label(box, text="Setting:").grid(row=0, column=0, sticky="w")
        self.setting_key = tk.StringVar(value="dsee")
        ttk.Combobox(box, textvariable=self.setting_key, values=helpers.MDR_SETTING_KEYS,
                     state="readonly", width=22).grid(row=0, column=1, sticky="w", padx=(6, 0))
        ttk.Label(box, text="Value (set):").grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.setting_value = tk.StringVar(value="")
        ttk.Entry(box, textvariable=self.setting_value, width=16).grid(row=0, column=3, sticky="w", padx=(6, 0))
        self._button(box, "Read", self.read_setting).grid(row=1, column=0, sticky="w", pady=(8, 0))
        self._button(box, "Read all", self.read_all_settings).grid(row=1, column=1, sticky="w", pady=(8, 0))
        self._button(box, "Write...", self.write_setting).grid(row=1, column=3, sticky="e", pady=(8, 0))
        ttk.Label(box, text="Values are raw hex replies. EQ bands are six dB values, e.g. 0,0,0,2,6,10. "
                           "The headset persists these settings itself.",
                  foreground="#555", wraplength=560, justify="left").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

        # Equalizer APO (Windows only; hidden when it is not installed).
        self.has_eqapo = eqapo.read_state() is not None
        box = ttk.LabelFrame(tab, text="Equalizer (Equalizer APO)", padding=8)
        if self.has_eqapo:
            box.grid(row=1, column=0, sticky="ew", **pad)
        presets = eqapo.list_presets()
        ttk.Label(box, text="Preset:").grid(row=0, column=0, sticky="w")
        self.eq_preset = tk.StringVar(value=presets[0] if presets else "")
        ttk.Combobox(box, textvariable=self.eq_preset, values=presets, state="readonly", width=48).grid(
            row=0, column=1, columnspan=2, sticky="w", padx=(6, 0))
        ttk.Label(box, text="Apply to:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.eq_target = tk.StringVar(value=next(iter(eqapo.EQ_TARGETS)))
        ttk.Combobox(box, textvariable=self.eq_target, values=list(eqapo.EQ_TARGETS),
                     state="readonly", width=28).grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        self.eq_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="EQ on", variable=self.eq_enabled).grid(
            row=1, column=2, sticky="w", pady=(6, 0))
        self.eq_status = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.eq_status, foreground="#555").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._button(box, "Apply", self.apply_eq).grid(row=2, column=2, sticky="e", pady=(6, 0))

    def _build_firmware(self, tab: "ttk.Frame", pad: dict) -> None:
        box = ttk.LabelFrame(tab, text="Official firmware", padding=8)
        box.grid(row=0, column=0, sticky="ew", **pad)
        ttk.Label(box, text="Version:").grid(row=0, column=0, sticky="w")
        self.fw_choice = tk.StringVar()
        self.fw_combo = ttk.Combobox(box, textvariable=self.fw_choice, state="readonly", width=24)
        self.fw_combo.grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.fw_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_fw_file_status())
        self.fw_file_status = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.fw_file_status, foreground="#555", width=34).grid(
            row=0, column=2, columnspan=2, sticky="w", padx=(10, 0))
        self._button(box, "Choose file...", self.choose_firmware_file).grid(
            row=1, column=2, sticky="e", pady=(8, 0))
        self._button(box, "Install...", self.install_firmware).grid(
            row=1, column=3, sticky="e", padx=(6, 0), pady=(8, 0))
        self.progress = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.progress, foreground="#555").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(box, text=f"Only SHA-256-verified official images. Requires at least "
                            f"{MIN_FLASH_BATTERY}% battery and a verified settings backup first.",
                  foreground="#555", wraplength=560, justify="left").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self._fill_fw_versions()

    def _build_nvdm(self, tab: "ttk.Frame", pad: dict) -> None:
        box = ttk.LabelFrame(tab, text="NVDM backup and parse", padding=8)
        box.grid(row=0, column=0, sticky="ew", **pad)
        ttk.Label(box, text="Method:").grid(row=0, column=0, sticky="w")
        self.backup_method = tk.StringVar(value="Verified dump")
        ttk.Combobox(box, textvariable=self.backup_method,
                     values=["Verified dump", "Library API"], state="readonly", width=18).grid(
            row=0, column=1, sticky="w", padx=(6, 0))
        self._button(box, "Back up...", self.backup).grid(row=0, column=2, sticky="w", padx=(10, 0))
        self._button(box, "Raw dump...", self.raw_dump).grid(row=0, column=3, sticky="w", padx=(6, 0))
        self._button(box, "Parse file...", self.parse_backup).grid(row=0, column=4, sticky="w", padx=(6, 0))
        ttk.Label(box, text="Verified dump takes about a minute and checks size, magic and parsed items. "
                           "The library API path uses create_nvdm_backup(). Raw dump only reads pages; "
                           "it skips the handshake and verification. Backups contain pairing records - keep them private.",
                  foreground="#555", wraplength=620, justify="left").grid(
            row=1, column=0, columnspan=5, sticky="w", pady=(6, 0))

        box = ttk.LabelFrame(tab, text="NVDM item write (MDR 0xFF/6)", padding=8)
        box.grid(row=1, column=0, sticky="ew", **pad)
        ttk.Label(box, text="Group:").grid(row=0, column=0, sticky="w")
        self.nvdm_group = tk.StringVar(value="config")
        ttk.Entry(box, textvariable=self.nvdm_group, width=14).grid(row=0, column=1, sticky="w", padx=(6, 0))
        ttk.Label(box, text="Item:").grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.nvdm_item = tk.StringVar(value="test_vp_disable")
        ttk.Entry(box, textvariable=self.nvdm_item, width=22).grid(row=0, column=3, sticky="w", padx=(6, 0))
        ttk.Label(box, text="Value:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.nvdm_value = tk.StringVar(value="1")
        ttk.Entry(box, textvariable=self.nvdm_value, width=14).grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        self.nvdm_hex = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="Hex value", variable=self.nvdm_hex).grid(
            row=1, column=2, sticky="w", pady=(6, 0))
        self._button(box, "Preview payload", self.preview_nvdm_payload).grid(
            row=1, column=3, sticky="w", padx=(6, 0), pady=(6, 0))
        self._button(box, "Read item", self.read_nvdm_item).grid(
            row=2, column=0, sticky="w", pady=(8, 0))
        self._button(box, "Write item...", self.write_nvdm_item).grid(
            row=2, column=3, sticky="e", pady=(8, 0))
        ttk.Label(box, text="PREVIEW sends nothing. Write is a persistent NVDM change: the firmware only "
                           "accepts whitelisted items. A bad item can break your settings.",
                  foreground="#8b0000", wraplength=620, justify="left").grid(
            row=3, column=0, columnspan=4, sticky="w", pady=(6, 0))

    def _build_coefficients(self, tab: "ttk.Frame", pad: dict) -> None:
        ttk.Label(tab, text="EXPERIMENTAL / WRITE PATH. The DSP transforms the supplied bias, so the "
                            "words stored in NVDM differ from the ones sent. Always check for howling "
                            "or instability at low volume, power-cycle and re-dump to confirm persistence.",
                  foreground="#8b0000", wraplength=620, justify="left").grid(
            row=0, column=0, sticky="w", **pad)
        box = ttk.LabelFrame(tab, text="Dry run (zero bytes written)", padding=8)
        box.grid(row=1, column=0, sticky="ew", **pad)
        ttk.Label(box, text="Target gain (dB):").grid(row=0, column=0, sticky="w")
        self.dry_gain = tk.StringVar(value="7.5")
        ttk.Entry(box, textvariable=self.dry_gain, width=8).grid(row=0, column=1, sticky="w", padx=(6, 0))
        self._button(box, "Dry run from file...", self.dry_run_from_file).grid(
            row=0, column=2, sticky="w", padx=(10, 0))
        self._button(box, "Dry run from headset", self.dry_run_from_headset).grid(
            row=0, column=3, sticky="w", padx=(6, 0))
        ttk.Label(box, text="Read-only guard: the report shows the coefficient diff and response curve; "
                           "no write command is ever sent.", foreground="#555").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))

        box = ttk.LabelFrame(tab, text="Persistent coefficient injection", padding=8)
        box.grid(row=2, column=0, sticky="ew", **pad)
        ttk.Label(box, text="Gain (dB):").grid(row=0, column=0, sticky="w")
        self.inject_gain = tk.StringVar(value="7.5")
        ttk.Entry(box, textvariable=self.inject_gain, width=8).grid(row=0, column=1, sticky="w", padx=(6, 0))
        self._button(box, "Preview words", self.preview_coeff_words).grid(
            row=0, column=2, sticky="w", padx=(10, 0))
        ttk.Label(box, text="Custom words (10 x 32-bit, blank = synthesize):").grid(
            row=1, column=0, sticky="w", pady=(6, 0))
        self.inject_words = tk.StringVar(value="")
        ttk.Entry(box, textvariable=self.inject_words, width=60).grid(
            row=1, column=1, columnspan=3, sticky="w", padx=(6, 0), pady=(6, 0))
        self._button(box, "Inject...", self.inject_coefficients).grid(
            row=2, column=3, sticky="e", pady=(8, 0))

    def _build_spatial(self, tab: "ttk.Frame", pad: dict) -> None:
        ttk.Label(tab, text="EXPERIMENTAL PROTOTYPE: Linux PipeWire + EasyEffects + AeroSpatial. "
                            "It is stereo virtualization, not Sony 360 Reality Audio or Dolby Atmos "
                            "decoding, and it failed its first listening test.",
                  foreground="#8b0000", wraplength=620, justify="left").grid(
            row=0, column=0, sticky="w", **pad)
        box = ttk.LabelFrame(tab, text="AeroSpatial pipeline", padding=8)
        box.grid(row=1, column=0, sticky="ew", **pad)
        ttk.Label(box, text="App node:").grid(row=0, column=0, sticky="w")
        self.spatial_app = tk.StringVar(value="spotify")
        ttk.Entry(box, textvariable=self.spatial_app, width=18).grid(row=0, column=1, sticky="w", padx=(6, 0))
        ttk.Label(box, text="Azimuth:").grid(row=0, column=2, sticky="w", padx=(10, 0))
        self.spatial_azimuth = tk.StringVar(value="60")
        ttk.Entry(box, textvariable=self.spatial_azimuth, width=8).grid(row=0, column=3, sticky="w", padx=(6, 0))
        ttk.Label(box, text="Gain:").grid(row=0, column=4, sticky="w", padx=(10, 0))
        self.spatial_gain = tk.StringVar(value="1.0")
        ttk.Entry(box, textvariable=self.spatial_gain, width=8).grid(row=0, column=5, sticky="w", padx=(6, 0))
        self._button(box, "Status", lambda: self.spatial_action("status")).grid(
            row=1, column=0, sticky="w", pady=(8, 0))
        self._button(box, "Start", lambda: self.spatial_action("start")).grid(
            row=1, column=1, sticky="w", pady=(8, 0))
        self._button(box, "Stop", lambda: self.spatial_action("stop")).grid(
            row=1, column=2, sticky="w", pady=(8, 0))

    def _build_log(self, root: "ttk.Frame") -> None:
        box = ttk.LabelFrame(root, text="Log", padding=6)
        box.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self.log = tk.Text(box, height=9, width=92, state="disabled", wrap="word", font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(box, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log["yscrollcommand"] = scroll.set

    def _button(self, parent: "tk.Widget", text: str, cmd: Callable[[], None]) -> "ttk.Button":
        b = ttk.Button(parent, text=text, command=cmd)
        self._buttons.append(b)
        return b

    def _sync_ambient_controls(self) -> None:
        state = "normal" if self.anc_mode.get() == AncMode.AMBIENT.value else "disabled"
        self.level_scale.state(["!disabled"] if state == "normal" else ["disabled"])
        self.voice_check.state(["!disabled"] if state == "normal" else ["disabled"])

    # ---------- logging and background work ----------

    def write_log(self, text: str) -> None:
        self._log_q.put(text if text.endswith("\n") else text + "\n")

    def _drain_log(self) -> None:
        chunks = []
        while not self._log_q.empty():
            chunks.append(self._log_q.get_nowait())
        if chunks:
            self.log.configure(state="normal")
            self.log.insert("end", "".join(chunks))
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(100, self._drain_log)

    def run_task(
        self,
        label: str,
        work: Callable[[], object],
        on_done: Optional[Callable[[object], None]] = None,
        capture_output: bool = False,
    ) -> None:
        """Runs `work` on a background thread so Bluetooth calls do not freeze the window."""
        if self._busy:
            self.write_log("  An operation is already running; please wait.")
            return
        self._busy = True
        for b in self._buttons:
            b.state(["disabled"])
        self.config(cursor="watch")
        self.write_log(f"> {label}")

        def worker() -> None:
            try:
                if capture_output:
                    writer = _QueueWriter(self._log_q, progress=self.set_progress)
                    with contextlib.redirect_stdout(writer):
                        result = work()
                else:
                    result = work()
                error = None
            except Exception as e:  # noqa: BLE001 - every failure is shown to the user
                result, error = None, e
            self.after(0, lambda: self._finish(result, error, on_done))

        threading.Thread(target=worker, daemon=True).start()

    def set_progress(self, text: str) -> None:
        """Thread-safe update of the progress line."""
        self.after(0, lambda: self.progress.set(text))

    def _on_close(self) -> None:
        if self._flashing:
            messagebox.showwarning("XM4 Control", "A firmware install is running. Closing now would abort it.\n"
                                                  "Wait until the log says it has finished.")
            return
        if self._busy and not messagebox.askokcancel("XM4 Control", "An operation is still running. Close anyway?"):
            return
        self.destroy()

    def _finish(
        self,
        result: object,
        error: Optional[Exception],
        on_done: Optional[Callable[[object], None]],
    ) -> None:
        self._busy = False
        self._flashing = False
        self.progress.set("")
        for b in self._buttons:
            b.state(["!disabled"])
        self.config(cursor="")
        if error is not None:
            self.write_log(f"  ERROR: {error}")
        elif on_done is not None:
            on_done(result)

    # ---------- headset ----------

    def _resolve_mac(self) -> str:
        if self.mac is None:
            entered = self.mac_var.get().strip() if tk is not None else ""
            self.mac = entered or find_connected_xm4_mac()
        if self.mac is None:
            raise SonyControlError(
                "No connected WH-1000XM4 found. Connect it in your OS Bluetooth settings "
                "or type its MAC address above."
            )
        return self.mac

    def refresh_status(self) -> None:
        def work() -> dict:
            mac = self._resolve_mac()
            if is_link_connected is not None and is_link_connected(mac) is False:
                raise SonyControlError(f"{mac} is paired but not connected.")
            with SonyControlClient.connect(mac) as client:
                return {
                    "mac": mac,
                    "fw": client.get_firmware_version(),
                    "battery": client.get_battery(),
                    "anc": client.get_ambient_sound(),
                }

        def done(r: object) -> None:
            assert isinstance(r, dict)
            self.firmware, self.battery = r["fw"], r["battery"].level
            charging = " (charging)" if r["battery"].charging else ""
            self.status_vars["Device"].set(f"WH-1000XM4  {r['mac']}")
            self.status_vars["Firmware"].set(r["fw"])
            self.status_vars["Battery"].set(f"{r['battery'].level}%{charging}")
            self._show_anc(r["anc"])
            self._fill_fw_versions()
            self.write_log(f"  Firmware {r['fw']}, battery {r['battery'].level}%, {describe_anc(r['anc'])}")

        self.run_task("Reading headset status", work, done)

    def _show_anc(self, state: AmbientSoundState) -> None:
        self.status_vars["ANC"].set(describe_anc(state))
        self.anc_mode.set(state.mode.value)
        if state.mode is AncMode.AMBIENT:
            self.level_scale.set(max(1, state.ambient_level))
            self.ambient_level.set(max(1, state.ambient_level))
            self.voice.set(state.focus_on_voice)
        self._sync_ambient_controls()

    def apply_anc(self) -> None:
        mode = AncMode(self.anc_mode.get())
        level, voice = self.ambient_level.get(), self.voice.get()

        def work() -> AmbientSoundState:
            with SonyControlClient.connect(self._resolve_mac()) as client:
                current = client.get_ambient_sound()
                if mode is AncMode.AMBIENT:
                    target = AmbientSoundState(mode, voice, level)
                else:
                    target = AmbientSoundState(mode, current.focus_on_voice, current.ambient_level)
                return client.set_ambient_sound(target)

        def done(result: object) -> None:
            assert isinstance(result, AmbientSoundState)
            self._show_anc(result)
            ok = "" if result.mode is mode else "  (headset reported a different mode)"
            self.write_log(f"  Now: {describe_anc(result)}{ok}")

        self.run_task(f"Setting {dict(ANC_CHOICES)[mode]}", work, done)

    # ---------- MDR settings ----------

    def _setting_done(self, result: object) -> None:
        rc, text = result  # type: ignore[misc]
        self.write_log(text.rstrip() if text else "  (no output)")
        if rc:
            self.write_log(f"  Command reported an error (exit {rc}).")

    def read_setting(self) -> None:
        key = self.setting_key.get()
        self.run_task(
            f"Reading setting {key}",
            lambda: helpers.settings_command(self._resolve_mac(), "get", key),
            self._setting_done,
            capture_output=True,
        )

    def read_all_settings(self) -> None:
        self.run_task(
            "Reading all MDR settings",
            lambda: helpers.settings_command(self._resolve_mac(), "get", "all"),
            self._setting_done,
            capture_output=True,
        )

    def write_setting(self) -> None:
        key, value = self.setting_key.get().strip(), self.setting_value.get().strip()
        if not key or not value:
            messagebox.showerror("XM4 Control", "Choose a setting and enter a value first.")
            return
        if not messagebox.askokcancel(
                "Write headset setting",
                f"Set {key} to {value}?\n\n"
                "The headset persists this setting to NVDM itself. You can read it back "
                "with the Read button. Do not change settings you do not recognise.",
                icon="warning", default="cancel"):
            return
        self.run_task(
            f"Setting {key} = {value}",
            lambda: helpers.settings_command(self._resolve_mac(), "set", key, value),
            self._setting_done,
            capture_output=True,
        )

    # ---------- firmware ----------

    def _fw_label(self, version: str) -> str:
        tags = []
        if version == firmware_store.known_versions()[0]:
            tags.append("latest")
        if version == self.firmware:
            tags.append("installed")
        return f"{version} ({', '.join(tags)})" if tags else version

    def _selected_version(self) -> str:
        return self.fw_choice.get().split(" ")[0]

    def _fill_fw_versions(self) -> None:
        previous = self._selected_version() if self.fw_choice.get() else None
        self.fw_combo["values"] = [self._fw_label(v) for v in firmware_store.known_versions()]
        target = previous or firmware_store.known_versions()[0]
        self.fw_choice.set(self._fw_label(target))
        self._update_fw_file_status()

    def _firmware_file(self, version: str) -> Optional[Path]:
        chosen = self._chosen_files.get(version)
        return chosen if chosen and chosen.exists() else firmware_store.find_local(version)

    def _update_fw_file_status(self) -> None:
        version = self._selected_version()
        path = self._firmware_file(version)
        self.fw_file_status.set(
            "File on disk, checksum verified" if path else "Will be downloaded and verified"
        )

    def choose_firmware_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose an official WH-1000XM4 firmware file",
            filetypes=[("Firmware", "*.bin")],
        )
        if not path:
            return
        version = firmware_store.identify_path(Path(path))
        if version is None:
            messagebox.showerror(
                "XM4 Control",
                "This file is not one of the official WH-1000XM4 images "
                "(its SHA-256 does not match). It will not be used.",
            )
            return
        self._chosen_files[version] = Path(path)
        self.fw_choice.set(self._fw_label(version))
        self._update_fw_file_status()
        self.write_log(f"> Verified {Path(path).name}: official firmware {version}")

    def install_firmware(self) -> None:
        version = self._selected_version()
        if self.firmware is None or self.battery is None:
            messagebox.showinfo("XM4 Control", "Read the headset status first (Refresh), so the battery "
                                               "and current version are known.")
            return
        if version == self.firmware:
            messagebox.showinfo("XM4 Control", f"The headset is already on {version}.")
            return
        if not helpers.battery_ok_for_flash(self.battery):
            messagebox.showerror(
                "XM4 Control",
                f"Battery is at {self.battery}%. Charge to at least {MIN_FLASH_BATTERY}% "
                "before installing firmware, then press Refresh.",
            )
            return
        direction = ("Downgrade" if firmware_store.version_key(version) <
                     firmware_store.version_key(self.firmware) else "Upgrade")
        if not messagebox.askokcancel(
                "Install firmware",
                f"{direction} from {self.firmware} to {version}?\n\n"
                "A full settings backup is taken and checked first (about 1 minute). "
                "The firmware is only sent if the backup succeeds.\n\n"
                "- Close Sony Headphones Connect and pause audio.\n"
                "- Keep the headset within about 1 m of this PC.\n"
                "- Do not turn the headset off or close this window until it finishes.\n\n"
                "The headset checks the image before it switches over and then reboots. "
                "Turn it back on afterwards and press Refresh.",
                icon="warning", default="cancel"):
            return
        path = self._firmware_file(version)
        current = self.firmware

        def work() -> dict:
            fw_path = path
            if fw_path is None:
                print(f"Downloading firmware {version} from the community archive...")
                fw_path = firmware_store.download(
                    version,
                    progress=lambda got, total: self.set_progress(
                        f"Downloading {got // 1024} / {total // 1024} KB"
                        if total else f"Downloading {got // 1024} KB"
                    ),
                )
                print(f"Downloaded and verified {fw_path.name}")

            mac = self._resolve_mac()
            out = backup_check.pre_firmware_backup_path(version, current)
            print(f"Step 1 of 2: backing up settings to {out.name} ...")
            summary = backup_check.take_verified_backup(mac, out)
            print(f"Backup verified: {summary.describe()}")
            time.sleep(3)  # let the headset leave update mode before the flasher re-enters it

            print(f"Step 2 of 2: installing firmware {version} ...")
            if str(REPO_ROOT) not in sys.path:
                sys.path.insert(0, str(REPO_ROOT))
            from tools.flash_firmware import flash_firmware

            return {"ok": flash_firmware(mac, fw_path), "backup": summary.path}

        def done(r: object) -> None:
            assert isinstance(r, dict)
            self._update_fw_file_status()
            self.write_log(f"  Pre-install backup: {r['backup']}")
            if r["ok"]:
                self.write_log("  Install finished. Turn the headset on if it stays off, then press Refresh.")
                messagebox.showinfo(
                    "XM4 Control",
                    f"Firmware {version} was sent and committed.\n\n"
                    f"Settings backup saved as {Path(r['backup']).name}.\n\n"
                    "Turn the headset on if it stays off, reconnect it, then press Refresh.",
                )
            else:
                self.write_log("  Install did not start; see the messages above.")

        if self._busy:
            return
        self._flashing = True
        self.run_task(f"Installing firmware {version}", work, done, capture_output=True)

    # ---------- NVDM backup, parse and item writes ----------

    def _backup_path(self) -> Optional[Path]:
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
        fw = (self.firmware or "unknown").replace(".", "_")
        name = filedialog.asksaveasfilename(
            title="Save settings backup",
            initialdir=str(BACKUP_DIR),
            initialfile=f"xm4_nvdm_fw{fw}_{stamp}.bin",
            defaultextension=".bin",
            filetypes=[("Backup", "*.bin")],
        )
        return Path(name) if name else None

    def backup(self) -> None:
        if self.battery is not None and self.battery < MIN_FLASH_BATTERY:
            if not messagebox.askokcancel(
                    "XM4 Control",
                    f"Battery is at {self.battery}%. The README asks for at least "
                    f"{MIN_FLASH_BATTERY}% before long transfers.\n\n"
                    "The backup only reads from the headset. Continue anyway?"):
                return
        path = self._backup_path()
        if path is None:
            return

        if self.backup_method.get() == "Library API":
            def work() -> int:
                transport = cli._create_transport(mac=self._resolve_mac(), mock=False)
                try:
                    return cli._run_backup_workflow(transport, path)
                finally:
                    transport.close()

            self.run_task(f"Backing up settings via library API to {path.name}", work,
                          capture_output=True)
            return

        def work_verified() -> backup_check.BackupSummary:
            return backup_check.take_verified_backup(self._resolve_mac(), path)

        def done(summary: object) -> None:
            assert isinstance(summary, backup_check.BackupSummary)
            self.write_log(f"  Verified {summary.describe()}")
            self.write_log(f"  Saved {summary.path}. Keep it private: it contains your pairing records.")
            self.refresh_status()

        self.run_task("Backing up settings (do not turn the headset off)", work_verified,
                      done, capture_output=True)

    def raw_dump(self) -> None:
        if not messagebox.askokcancel(
                "Raw NVDM dump",
                "This reads the 512 KiB store over RACE without the update-mode handshake "
                "or verification. Use it only for diagnostics; prefer 'Verified dump' for "
                "a backup you intend to restore. Continue?"):
            return
        path = self._backup_path()
        if path is None:
            return

        def work() -> str:
            data = dump_nvdm_bytes(self._resolve_mac())
            path.write_bytes(data)
            sidecar = path.with_name(path.name + ".sha256")
            sidecar.write_text(f"{hashlib.sha256(data).hexdigest()}  {path.name}\n", encoding="utf-8")
            return f"  Raw dump: {len(data):,} bytes in {path}\n  Sidecar: {sidecar}"

        self.run_task(f"Raw NVDM dump to {path.name}", work,
                      lambda r: self.write_log(str(r)), capture_output=True)

    def parse_backup(self) -> None:
        path = filedialog.askopenfilename(title="Parse a local NVDM backup",
                                          filetypes=[("Backup", "*.bin")])
        if not path:
            return
        self.run_task(
            f"Parsing {Path(path).name}",
            lambda: helpers.describe_nvdm_image(Path(path)),
            lambda r: self.write_log(str(r)),
        )

    def _nvdm_value(self) -> bytes:
        return helpers.parse_value(self.nvdm_value.get(), self.nvdm_hex.get())

    def preview_nvdm_payload(self) -> None:
        try:
            payload_hex = helpers.nvdm_payload_preview(
                self.nvdm_group.get().strip(), self.nvdm_item.get().strip(),
                self._nvdm_value(), write=True,
            )
        except ValueError as e:
            messagebox.showerror("XM4 Control", str(e))
            return
        self.write_log(f"> NVDM write payload (nothing sent): {payload_hex}")

    def read_nvdm_item(self) -> None:
        group, item = self.nvdm_group.get().strip(), self.nvdm_item.get().strip()
        payload_hex = helpers.nvdm_payload_preview(group, item, b"", write=False)
        self.write_log(f"> NVDM read payload: {payload_hex}")

        def work() -> list[bytes]:
            from tools.mdr_nvdm_write import build_nvdm_read_payload
            return send_and_listen(self._resolve_mac(), build_nvdm_read_payload(group, item))

        self.run_task(f"Reading NVDM item {group}/{item}", work,
                      lambda r: self.write_log(helpers.format_replies(r)))

    def write_nvdm_item(self) -> None:
        group, item = self.nvdm_group.get().strip(), self.nvdm_item.get().strip()
        try:
            value = self._nvdm_value()
            payload_hex = helpers.nvdm_payload_preview(group, item, value, write=True)
        except ValueError as e:
            messagebox.showerror("XM4 Control", str(e))
            return
        if not messagebox.askokcancel(
                "Persistent NVDM write",
                f"Write {group}/{item} = {value.hex()} to NVDM?\n\n"
                f"Frame: {payload_hex}\n\n"
                "This is a persistent write to the headset settings store. The firmware "
                "accepts only whitelisted items, and a wrong value can break your settings. "
                "Take a verified backup first. Continue?",
                icon="warning", default="cancel"):
            return

        def work() -> list[bytes]:
            from tools.mdr_nvdm_write import build_nvdm_write_payload
            return send_and_listen(self._resolve_mac(), build_nvdm_write_payload(group, item, value))

        self.run_task(f"Writing NVDM item {group}/{item}", work,
                      lambda r: self.write_log(helpers.format_replies(r)))

    # ---------- ANC coefficients ----------

    def _number(self, var: tk.StringVar) -> float:
        try:
            return float(var.get())
        except ValueError as exc:
            raise ValueError("Enter a decimal number in the field") from exc

    def preview_coeff_words(self) -> None:
        try:
            words = helpers.synthesize_words(self._number(self.inject_gain))
        except ValueError as e:
            messagebox.showerror("XM4 Control", str(e))
            return
        self.write_log(f"> Bias words for {self.inject_gain.get()} dB: {helpers.format_words(words)}")

    def inject_coefficients(self) -> None:
        custom = self.inject_words.get().strip()
        try:
            if custom:
                words = helpers.parse_words(custom)
            else:
                words = helpers.synthesize_words(self._number(self.inject_gain))
        except (ValueError, IndexError) as e:
            messagebox.showerror("XM4 Control", str(e))
            return
        if not messagebox.askokcancel(
                "Inject ANC coefficients",
                "Send these words via NCOPTSET, start the NC Optimizer and dump NVDM?\n\n"
                f"{helpers.format_words(words)}\n\n"
                "EXPERIMENTAL WRITE: the DSP transforms the bias, so the stored words will "
                "differ. Check for howling at low volume and power-cycle to verify that "
                "the value was persisted. Take a verified backup first. Continue?",
                icon="warning", default="cancel"):
            return
        out_dir = BACKUP_DIR / "anc_inject"

        def work() -> int:
            from tools.anc_coeff_inject import inject
            return inject(self._resolve_mac(), words, out_dir, timeout_s=60.0)

        def done(rc: object) -> None:
            self.write_log(f"  Injection workflow finished (exit {rc}). "
                           f"Dump and readback are in {out_dir}.")

        self.run_task("Injecting ANC coefficients (NCOPTSET + optimizer)",
                      work, done, capture_output=True)

    def dry_run_from_file(self) -> None:
        path = filedialog.askopenfilename(title="Dry run from NVDM backup",
                                          filetypes=[("Backup", "*.bin")])
        if not path:
            return
        self.run_task(
            f"Dry run from {Path(path).name}",
            lambda: helpers.dry_run_command(
                target_gain=self._number(self.dry_gain), image_path=Path(path)
            ),
            lambda r: self._log_command_result(r),
        )

    def dry_run_from_headset(self) -> None:
        self.run_task(
            "Dry run from connected headset",
            lambda: helpers.dry_run_command(
                target_gain=self._number(self.dry_gain), mac=self._resolve_mac()
            ),
            lambda r: self._log_command_result(r),
        )

    def _log_command_result(self, result: object) -> None:
        rc, text = result  # type: ignore[misc]
        self.write_log(text.rstrip() if text else "  (no output)")
        self.write_log(f"  Command finished with exit {rc} (0 means success).")

    # ---------- spatial ----------

    def spatial_action(self, action: str) -> None:
        if action == "start" and not messagebox.askokcancel(
                "Experimental spatial audio",
                "Start the experimental AeroSpatial PipeWire pipeline for the chosen app?\n\n"
                "It changes audio routing on this PC and is not a Sony 360 Reality Audio "
                "decoder. It failed its first listening test. Continue?"):
            return

        def work() -> tuple[int, str]:
            return helpers.spatial_command(
                action,
                app_node=self.spatial_app.get().strip() or "spotify",
                azimuth=self._number(self.spatial_azimuth),
                gain=self._number(self.spatial_gain),
            )

        self.run_task(f"Spatial audio: {action}", work, lambda r: self._log_command_result(r))

    # ---------- equalizer ----------

    def _load_eq_state(self) -> None:
        state = eqapo.read_state() if self.has_eqapo else None
        if state is None:
            return
        if state.preset in eqapo.list_presets():
            self.eq_preset.set(state.preset)
        for label, pattern in eqapo.EQ_TARGETS.items():
            if state.device == pattern:
                self.eq_target.set(label)
        self.eq_enabled.set(state.enabled and state.preset in eqapo.list_presets())
        self._show_eq_status(state)

    def _show_eq_status(self, state: eqapo.EqState) -> None:
        if state.enabled and state.preset:
            self.eq_status.set(f"Active: {state.preset}")
        else:
            self.eq_status.set("EQ is off.")

    def apply_eq(self) -> None:
        preset = self.eq_preset.get()
        if not preset:
            messagebox.showerror("XM4 Control", "No Equalizer APO preset found in the presets folder.")
            return
        try:
            state = eqapo.apply(preset, eqapo.EQ_TARGETS[self.eq_target.get()], self.eq_enabled.get())
        except PermissionError:
            messagebox.showerror("XM4 Control", "Windows refused to write to the Equalizer APO folder.\n"
                                                "Run XM4 Control as administrator and try again.")
            return
        except OSError as e:
            messagebox.showerror("XM4 Control", str(e))
            return
        self._show_eq_status(state)
        self.write_log(f"> EQ {'on' if state.enabled else 'off'}: {preset} on {self.eq_target.get()}")


def main() -> None:
    if tk is None:  # pragma: no cover - only on builds without Tk
        raise SystemExit("Tkinter is not available. Install python3-tk (or the Tk component "
                         "of your Python distribution) to run the desktop app.")
    App().mainloop()


if __name__ == "__main__":
    main()
