"""Tests for Debian .deb package generation."""

import io
import tarfile
from pathlib import Path

from scripts.build_deb import build_deb


def _parse_ar(raw: bytes):
    assert raw.startswith(b"!<arch>\n")
    pos = 8
    members = {}
    while pos < len(raw):
        hdr = raw[pos:pos + 60]
        assert len(hdr) == 60
        name = hdr[:16].decode("ascii").strip()
        size = int(hdr[48:58].decode("ascii").strip())
        assert hdr[58:60] == b"`\n"
        pos += 60
        payload = raw[pos:pos + size]
        assert len(payload) == size
        members[name] = payload
        pos += size
        if size % 2 == 1:
            pos += 1
    return members


def test_build_deb_valid_structure(tmp_path):
    project_root = Path(__file__).resolve().parent.parent
    out_deb = tmp_path / "backwatch_1.0.0_all.deb"
    built = build_deb(project_root, out_deb)
    assert built.exists()

    members = _parse_ar(built.read_bytes())
    assert list(members.keys()) == ["debian-binary", "control.tar.gz", "data.tar.gz"]
    assert members["debian-binary"] == b"2.0\n"

    # Verify control.tar.gz
    with tarfile.open(fileobj=io.BytesIO(members["control.tar.gz"]), mode="r:gz") as tf:
        names = tf.getnames()
        assert "./control" in names
        assert "./conffiles" in names
        assert "./postinst" in names
        assert "./prerm" in names
        assert "./postrm" in names

        postinst_info = tf.getmember("./postinst")
        assert (postinst_info.mode & 0o755) == 0o755
        postinst_bytes = tf.extractfile(postinst_info).read()
        assert b"\r\n" not in postinst_bytes

    # Verify data.tar.gz
    with tarfile.open(fileobj=io.BytesIO(members["data.tar.gz"]), mode="r:gz") as tf:
        names = tf.getnames()
        assert "./opt/backup-health-monitor/app/main.py" in names
        assert "./opt/backup-health-monitor/app/server.py" in names
        assert "./opt/backup-health-monitor/requirements.txt" in names
        assert "./etc/systemd/system/BM.service" in names
        assert "./etc/backup-health-monitor/config.yml" in names
        assert "./etc/backup-health-monitor/backwatch.env" in names
