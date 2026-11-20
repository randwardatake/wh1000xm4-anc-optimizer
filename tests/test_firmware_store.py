import io

import pytest

from xm4_optimizer import firmware_store as fs


class _FakeResp(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def test_versions_newest_first_and_all_downloadable():
    versions = fs.known_versions()
    assert versions == ["3.0.1", "2.9.0", "2.7.1", "2.6.0", "2.5.0"]
    assert set(fs.ARCHIVE_FILES) == set(versions)


def test_download_rejects_wrong_bytes_and_saves_nothing(tmp_path):
    seen = []
    with pytest.raises(ValueError, match="SHA-256"):
        fs.download("2.5.0", dest_dir=tmp_path, progress=lambda g, t: seen.append((g, t)),
                    opener=lambda req, timeout: _FakeResp(b"not firmware"))
    assert list(tmp_path.iterdir()) == []
    assert seen == [(12, 12)]


def test_unknown_version_and_empty_dirs(tmp_path):
    with pytest.raises(ValueError):
        fs.download("9.9.9", dest_dir=tmp_path)
    (tmp_path / "junk.bin").write_bytes(b"x" * 10)
    assert fs.find_local("2.5.0", [tmp_path, tmp_path / "missing"]) is None
