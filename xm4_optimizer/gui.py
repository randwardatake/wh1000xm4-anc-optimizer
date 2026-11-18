"""Small desktop UI for everyday use: headset status, ANC mode, firmware, EQ preset and backup.

Run with `python -m xm4_optimizer.gui` or the `xm4-optimizer-gui` launcher.
Firmware installs are limited to the official images in `firmware.py` and need at
least 50% battery. The experimental ANC write paths are not exposed here.
"""

from __future__ import annotations

import contextlib
import datetime
import queue
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from xm4_optimizer import backup_check, eqapo, firmware_store
from xm4_optimizer.transport.rfcomm_socket import find_connected_xm4_mac, is_link_connected
from xm4_optimizer.transport.sony_control import (
    AmbientSoundState,
    AncMode,
    SonyControlClient,
    SonyControlError,
)

from xm4_optimizer.paths import BACKUP_DIR, REPO_ROOT

MIN_FLASH_BATTERY = 50

ANC_CHOICES = [
    (AncMode.NOISE_CANCELLING, "Noise cancelling"),
    (AncMode.AMBIENT, "Ambient sound"),
    (AncMode.WIND, "Wind noise reduction"),
    (AncMode.OFF, "Off"),
]


def describe_anc(state: AmbientSoundState) -> str:
    text = dict(ANC_CHOICES)[state.mode]
    if state.mode is AncMode.AMBIENT:
        text += f" (level {state.ambient_level}, voice {'on' if state.focus_on_voice else 'off'})"
    return text


class _QueueWriter:
    """File-like object that forwards printed text to the log queue.

    Lines starting with a carriage return are in-place progress updates; they go to
    `progress` instead of the log so they do not flood it.
    """

    def __init__(self, q: "queue.Queue[str]", progress: Optional[Callable[[str], None]] = None) -> None:
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


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("XM4 Control")
        self.resizable(False, False)
        self.mac: Optional[str] = None
        self.firmware: Optional[str] = None
        self.battery: Optional[int] = None
        self._busy = False
        self._log_q: "queue.Queue[str]" = queue.Queue()
        self._buttons: list[ttk.Button] = []
        self._chosen_files: dict[str, Path] = {}
        self._flashing = False

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._load_eq_state()
        self.after(100, self._drain_log)
        self.after(200, self.refresh_status)

    # ---------- layout ----------

    def _build(self) -> None:
        pad = {"padx": 10, "pady": 6}
        root = ttk.Frame(self, padding=10)
        root.grid(sticky="nsew")

        # Headset status
        box = ttk.LabelFrame(root, text="Headset", padding=8)
        box.grid(row=0, column=0, sticky="ew", **pad)
        self.status_vars = {k: tk.StringVar(value="…") for k in ("Device", "Firmware", "Battery", "ANC")}
        for i, (name, var) in enumerate(self.status_vars.items()):
            ttk.Label(box, text=f"{name}:").grid(row=i, column=0, sticky="w")
            ttk.Label(box, textvariable=var, width=42).grid(row=i, column=1, sticky="w", padx=(6, 0))
        self._button(box, "Refresh", self.refresh_status).grid(row=0, column=2, rowspan=2, padx=(10, 0))

        # Noise control
        box = ttk.LabelFrame(root, text="Noise control", padding=8)
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
        self.level_scale.grid(row=1, column=1, columnspan=2, sticky="w", pady=(8, 0))
        ttk.Label(box, textvariable=self.ambient_level, width=3).grid(row=1, column=2, sticky="e", pady=(8, 0))
        self.voice = tk.BooleanVar(value=False)
        self.voice_check = ttk.Checkbutton(box, text="Focus on voice", variable=self.voice)
        self.voice_check.grid(row=1, column=3, sticky="w", pady=(8, 0))
        self._button(box, "Apply", self.apply_anc).grid(row=2, column=3, sticky="e", pady=(8, 0))
        self._sync_ambient_controls()

        # Firmware
        box = ttk.LabelFrame(root, text="Firmware", padding=8)
        box.grid(row=2, column=0, sticky="ew", **pad)
        ttk.Label(box, text="Version:").grid(row=0, column=0, sticky="w")
        self.fw_choice = tk.StringVar()
        self.fw_combo = ttk.Combobox(box, textvariable=self.fw_choice, state="readonly", width=24)
        self.fw_combo.grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.fw_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_fw_file_status())
        self.fw_file_status = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.fw_file_status, foreground="#555", width=34).grid(
            row=0, column=2, columnspan=2, sticky="w", padx=(10, 0))
        self._button(box, "Choose file…", self.choose_firmware_file).grid(row=1, column=2, sticky="e", pady=(8, 0))
        self._button(box, "Install…", self.install_firmware).grid(row=1, column=3, sticky="e", padx=(6, 0), pady=(8, 0))
        self.progress = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.progress, foreground="#555").grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self._fill_fw_versions()

        # Equalizer (only shown when Equalizer APO is installed)
        self.has_eqapo = eqapo.read_state() is not None
        box = ttk.LabelFrame(root, text="Equalizer (Equalizer APO)", padding=8)
        if self.has_eqapo:
            box.grid(row=3, column=0, sticky="ew", **pad)
        presets = eqapo.list_presets()
        ttk.Label(box, text="Preset:").grid(row=0, column=0, sticky="w")
        self.eq_preset = tk.StringVar(value=presets[0] if presets else "")
        ttk.Combobox(box, textvariable=self.eq_preset, values=presets, state="readonly", width=48).grid(
            row=0, column=1, columnspan=2, sticky="w", padx=(6, 0))
        ttk.Label(box, text="Apply to:").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.eq_target = tk.StringVar(value=next(iter(eqapo.EQ_TARGETS)))
        ttk.Combobox(box, textvariable=self.eq_target, values=list(eqapo.EQ_TARGETS), state="readonly", width=28).grid(
            row=1, column=1, sticky="w", padx=(6, 0), pady=(6, 0))
        self.eq_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(box, text="EQ on", variable=self.eq_enabled).grid(row=1, column=2, sticky="w", pady=(6, 0))
        self.eq_status = tk.StringVar(value="")
        ttk.Label(box, textvariable=self.eq_status, foreground="#555").grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._button(box, "Apply", self.apply_eq).grid(row=2, column=2, sticky="e", pady=(6, 0))

        # Backup
        box = ttk.LabelFrame(root, text="Backup", padding=8)
        box.grid(row=4, column=0, sticky="ew", **pad)
        ttk.Label(box, text="Save a full copy of the headset's settings store (about 1 minute).").grid(row=0, column=0, sticky="w")
        self._button(box, "Back up…", self.backup).grid(row=0, column=1, sticky="e", padx=(10, 0))

        # Log
        box = ttk.LabelFrame(root, text="Log", padding=8)
        box.grid(row=5, column=0, sticky="ew", **pad)
        self.log = tk.Text(box, height=10, width=78, state="disabled", wrap="word", font=("Consolas", 9))
        self.log.grid(row=0, column=0)
        scroll = ttk.Scrollbar(box, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log["yscrollcommand"] = scroll.set

    def _button(self, parent: tk.Widget, text: str, cmd: Callable[[], None]) -> ttk.Button:
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

    def run_task(self, label: str, work: Callable[[], object],
                 on_done: Optional[Callable[[object], None]] = None, capture_output: bool = False) -> None:
        """Runs `work` on a background thread so Bluetooth calls do not freeze the window."""
        if self._busy:
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

    def _finish(self, result: object, error: Optional[Exception], on_done: Optional[Callable[[object], None]]) -> None:
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
            self.mac = find_connected_xm4_mac()
        if self.mac is None:
            raise SonyControlError("No connected WH-1000XM4 found. Connect it in Windows Bluetooth settings.")
        return self.mac

    def refresh_status(self) -> None:
        def work() -> dict:
            mac = self._resolve_mac()
            if is_link_connected(mac) is False:
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
        self.fw_file_status.set("File on disk, checksum verified" if path else "Will be downloaded and verified")

    def choose_firmware_file(self) -> None:
        path = filedialog.askopenfilename(title="Choose an official WH-1000XM4 firmware file",
                                          filetypes=[("Firmware", "*.bin")])
        if not path:
            return
        version = firmware_store.identify_path(Path(path))
        if version is None:
            messagebox.showerror("XM4 Control", "This file is not one of the official WH-1000XM4 images "
                                                "(its SHA-256 does not match). It will not be used.")
            return
        self._chosen_files[version] = Path(path)
        self.fw_choice.set(self._fw_label(version))
        self._update_fw_file_status()
        self.write_log(f"> Verified {Path(path).name}: official firmware {version}")

    def install_firmware(self) -> None:
        version = self._selected_version()
        if self.firmware is None or self.battery is None:
            messagebox.showinfo("XM4 Control", "Read the headset status first (Refresh), so the battery and current version are known.")
            return
        if version == self.firmware:
            messagebox.showinfo("XM4 Control", f"The headset is already on {version}.")
            return
        if self.battery < MIN_FLASH_BATTERY:
            messagebox.showerror("XM4 Control", f"Battery is at {self.battery}%. Charge to at least {MIN_FLASH_BATTERY}% "
                                                "before installing firmware, then press Refresh.")
            return
        direction = "Downgrade" if firmware_store.version_key(version) < firmware_store.version_key(self.firmware) else "Upgrade"
        if not messagebox.askokcancel(
                "Install firmware",
                f"{direction} from {self.firmware} to {version}?\n\n"
                "A full settings backup is taken and checked first (about 1 minute). "
                "The firmware is only sent if the backup succeeds.\n\n"
                "• Close Sony Headphones Connect and pause audio.\n"
                "• Keep the headset within about 1 m of this PC.\n"
                "• Do not turn the headset off or close this window until it finishes.\n\n"
                "The headset checks the image before it switches over and then reboots. "
                "Turn it back on afterwards and press Refresh.",
                icon="warning", default="cancel"):
            return
        path = self._firmware_file(version)
        current = self.firmware

        def work() -> dict:
            # 1. Get a verified firmware file (no headset traffic yet).
            fw_path = path
            if fw_path is None:
                print(f"Downloading firmware {version} from the community archive...")
                fw_path = firmware_store.download(
                    version, progress=lambda got, total: self.set_progress(
                        f"Downloading {got // 1024} / {total // 1024} KB" if total else f"Downloading {got // 1024} KB"))
                print(f"Downloaded and verified {fw_path.name}")

            # 2. Mandatory settings backup. Any failure here stops before flashing.
            mac = self._resolve_mac()
            out = backup_check.pre_firmware_backup_path(version, current)
            print(f"Step 1 of 2: backing up settings to {out.name} ...")
            summary = backup_check.take_verified_backup(mac, out)
            print(f"Backup verified: {summary.describe()}")
            time.sleep(3)  # let the headset leave update mode before the flasher re-enters it

            # 3. Flash.
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
                messagebox.showinfo("XM4 Control", f"Firmware {version} was sent and committed.\n\n"
                                                   f"Settings backup saved as {Path(r['backup']).name}.\n\n"
                                                   "Turn the headset on if it stays off, reconnect it, then press Refresh.")
            else:
                self.write_log("  Install did not start; see the messages above.")

        self._flashing = True
        self.run_task(f"Installing firmware {version}", work, done, capture_output=True)

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

    # ---------- backup ----------

    def backup(self) -> None:
        if self.battery is not None and self.battery < 50:
            if not messagebox.askokcancel(
                    "XM4 Control",
                    f"Battery is at {self.battery}%. The README asks for at least 50% before long transfers.\n\n"
                    "The backup only reads from the headset. Continue anyway?"):
                return
        BACKUP_DIR.mkdir(exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
        fw = (self.firmware or "unknown").replace(".", "_")
        path = filedialog.asksaveasfilename(
            title="Save settings backup", initialdir=str(BACKUP_DIR),
            initialfile=f"xm4_nvdm_fw{fw}_{stamp}.bin", defaultextension=".bin",
            filetypes=[("Backup", "*.bin")])
        if not path:
            return

        def work() -> backup_check.BackupSummary:
            return backup_check.take_verified_backup(self._resolve_mac(), Path(path))

        def done(summary: object) -> None:
            assert isinstance(summary, backup_check.BackupSummary)
            self.write_log(f"  Verified {summary.describe()}")
            self.write_log(f"  Saved {summary.path}. Keep it private: it contains your pairing records.")
            self.refresh_status()

        self.run_task("Backing up settings (do not turn the headset off)", work, done, capture_output=True)


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
