"""Build a Debian .deb package for Backup Health Monitor (BM.service) on any OS."""

import io
import os
import tarfile
import time
from pathlib import Path

VERSION = "1.0.0"
PACKAGE_NAME = "backwatch"


def _ar_header(name: str, size: int, mode: str = "100644") -> bytes:
    """Create a 60-byte POSIX ar archive member header."""
    mtime = int(time.time())
    header = (
        f"{name:<16}"
        f"{mtime:<12}"
        f"{'0':<6}"
        f"{'0':<6}"
        f"{mode:<8}"
        f"{size:<10}"
        f"`\n"
    )
    encoded = header.encode("ascii")
    assert len(encoded) == 60, f"Invalid ar header length: {len(encoded)}"
    return encoded


def _add_tar_bytes(tf: tarfile.TarFile, arcname: str, data: bytes, mode: int = 0o644) -> None:
    """Add an in-memory byte payload to a tarfile with root:root ownership."""
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = int(time.time())
    tf.addfile(info, io.BytesIO(data))


def _add_tar_dir(tf: tarfile.TarFile, arcname: str, mode: int = 0o755) -> None:
    """Add a directory entry to a tarfile with root:root ownership."""
    info = tarfile.TarInfo(name=arcname)
    info.type = tarfile.DIRTYPE
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = int(time.time())
    tf.addfile(info)


def build_deb(project_root: Path, output_deb: Path) -> Path:
    """Assemble backup-health-monitor_<version>_all.deb."""
    # 1. Build control.tar.gz
    control_txt = (
        f"Package: {PACKAGE_NAME}\n"
        f"Version: {VERSION}\n"
        "Section: admin\n"
        "Priority: optional\n"
        "Architecture: all\n"
        "Replaces: backup-health-monitor\n"
        "Provides: backup-health-monitor\n"
        "Depends: python3 (>= 3.10), python3-venv, python3-pip, gzip, tar, unzip\n"
        "Maintainer: Backwatch SRE <sre@localhost>\n"
        "Description: Backwatch — Automated Backup Health Monitor & Retention Service (BM.service)\n"
        " Production SRE backup monitoring with df -hT storage condition, 30-day\n"
        " capacity projection graph, categorized folder tree, Basic Auth, retention\n"
        " dry-run lifecycle management, Prometheus metrics, Discord alerts, and AI briefings.\n"
        " Homepage: https://github.com/cyraxabir/BackWatch\n"
    )

    conffiles_txt = (
        "/etc/backup-health-monitor/config.yml\n"
        "/etc/backup-health-monitor/backwatch.env\n"
    )

    postinst_sh = """#!/bin/bash
set -e

INSTALL_DIR="/opt/backup-health-monitor"
CONFIG_DIR="/etc/backup-health-monitor"
STATE_DIR="/var/lib/backup-health-monitor"
SERVICE_USER="backupmon"
SERVICE_GROUP="backupmon"

if ! getent group "${SERVICE_GROUP}" >/dev/null 2>&1; then
    groupadd -r "${SERVICE_GROUP}"
fi

if ! getent passwd "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd -r -g "${SERVICE_GROUP}" -d "${INSTALL_DIR}" -s /sbin/nologin \
        -c "Backup Health Monitor daemon" "${SERVICE_USER}"
fi

mkdir -p "${STATE_DIR}" "${CONFIG_DIR}"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_DIR}" "${STATE_DIR}"
chmod 750 "${INSTALL_DIR}" "${STATE_DIR}"

if [ -f "${CONFIG_DIR}/config.yml" ]; then
    chown root:"${SERVICE_GROUP}" "${CONFIG_DIR}/config.yml"
    chmod 640 "${CONFIG_DIR}/config.yml"
fi
if [ -f "${CONFIG_DIR}/backwatch.env" ]; then
    chown root:"${SERVICE_GROUP}" "${CONFIG_DIR}/backwatch.env"
    chmod 640 "${CONFIG_DIR}/backwatch.env"
fi

echo "Setting up Python virtual environment in ${INSTALL_DIR}/venv..."
if [ ! -d "${INSTALL_DIR}/venv" ]; then
    python3 -m venv "${INSTALL_DIR}/venv"
fi
"${INSTALL_DIR}/venv/bin/pip" install --quiet --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements.txt"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_DIR}/venv"

if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload || true
    systemctl enable BM.service || true
    systemctl restart BM.service || true
fi

echo ""
echo "==================================================================="
echo " Backwatch (BM.service) installed successfully!"
echo " Config:  ${CONFIG_DIR}/backwatch.env"
echo " Auth:    Set AUTH_USERNAME / AUTH_PASSWORD in the .env file above"
echo " Web UI:  http://<SERVER_IP>:9108"
echo " Status:  systemctl status BM"
echo " GitHub:  https://github.com/cyraxabir/BackWatch"
echo "==================================================================="
"""

    prerm_sh = """#!/bin/bash
set -e
if [ "$1" = "remove" ] && command -v systemctl >/dev/null 2>&1; then
    systemctl stop BM.service || true
    systemctl disable BM.service || true
fi
"""

    postrm_sh = """#!/bin/bash
set -e
if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload || true
fi
if [ "$1" = "purge" ]; then
    rm -rf /var/lib/backup-health-monitor /opt/backup-health-monitor/venv
fi
"""

    control_buf = io.BytesIO()
    with tarfile.open(fileobj=control_buf, mode="w:gz") as tf:
        _add_tar_dir(tf, "./")
        _add_tar_bytes(tf, "./control", control_txt.encode("utf-8"), 0o644)
        _add_tar_bytes(tf, "./conffiles", conffiles_txt.encode("utf-8"), 0o644)
        _add_tar_bytes(tf, "./postinst", postinst_sh.replace("\r\n", "\n").encode("utf-8"), 0o755)
        _add_tar_bytes(tf, "./prerm", prerm_sh.replace("\r\n", "\n").encode("utf-8"), 0o755)
        _add_tar_bytes(tf, "./postrm", postrm_sh.replace("\r\n", "\n").encode("utf-8"), 0o755)
    control_bytes = control_buf.getvalue()

    # 2. Build data.tar.gz
    data_buf = io.BytesIO()
    with tarfile.open(fileobj=data_buf, mode="w:gz") as tf:
        for d in [
            "./opt",
            "./opt/backup-health-monitor",
            "./opt/backup-health-monitor/app",
            "./etc",
            "./etc/backup-health-monitor",
            "./etc/systemd",
            "./etc/systemd/system",
        ]:
            _add_tar_dir(tf, d)

        # Copy app/ source tree (excluding __pycache__ and .pyc)
        app_dir = project_root / "app"
        for root, dirs, files in os.walk(app_dir):
            dirs[:] = [d for d in sorted(dirs) if d != "__pycache__"]
            rel_root = Path(root).relative_to(project_root)
            arc_root = "./opt/backup-health-monitor/" + rel_root.as_posix()
            if rel_root.as_posix() != "app":
                _add_tar_dir(tf, arc_root)
            for fname in sorted(files):
                if fname.endswith(".pyc"):
                    continue
                fpath = Path(root) / fname
                raw = fpath.read_bytes()
                _add_tar_bytes(tf, f"{arc_root}/{fname}", raw, 0o644)

        # requirements.txt
        req_bytes = (project_root / "requirements.txt").read_bytes()
        _add_tar_bytes(tf, "./opt/backup-health-monitor/requirements.txt", req_bytes, 0o644)

        # config/config.yml -> /etc/backup-health-monitor/config.yml (adjusted for bare-metal)
        cfg_text = (project_root / "config" / "config.yml").read_text(encoding="utf-8")
        cfg_text = cfg_text.replace("backup_root: /backups", "backup_root: /var/backups/db")
        cfg_text = cfg_text.replace("state_dir: /state", "state_dir: /var/lib/backup-health-monitor")
        cfg_text = cfg_text.replace("db_path: /state/history.db", "db_path: /var/lib/backup-health-monitor/history.db")
        _add_tar_bytes(tf, "./etc/backup-health-monitor/config.yml", cfg_text.replace("\r\n", "\n").encode("utf-8"), 0o640)

        # systemd/backwatch.env -> /etc/backup-health-monitor/backwatch.env
        env_text = (project_root / "systemd" / "backwatch.env").read_text(encoding="utf-8")
        _add_tar_bytes(tf, "./etc/backup-health-monitor/backwatch.env", env_text.replace("\r\n", "\n").encode("utf-8"), 0o640)

        # systemd/backup-health-monitor.service -> /etc/systemd/system/BM.service
        svc_text = (project_root / "systemd" / "backup-health-monitor.service").read_text(encoding="utf-8")
        svc_text = svc_text.replace("SyslogIdentifier=backup-health-monitor", "SyslogIdentifier=BM")
        _add_tar_bytes(tf, "./etc/systemd/system/BM.service", svc_text.replace("\r\n", "\n").encode("utf-8"), 0o644)

    data_bytes = data_buf.getvalue()

    # 3. Assemble ar archive (.deb)
    debian_binary = b"2.0\n"
    out_buf = io.BytesIO()
    out_buf.write(b"!<arch>\n")

    for member_name, payload in [
        ("debian-binary", debian_binary),
        ("control.tar.gz", control_bytes),
        ("data.tar.gz", data_bytes),
    ]:
        out_buf.write(_ar_header(member_name, len(payload)))
        out_buf.write(payload)
        if len(payload) % 2 == 1:
            out_buf.write(b"\n")

    output_deb.parent.mkdir(parents=True, exist_ok=True)
    output_deb.write_bytes(out_buf.getvalue())
    return output_deb


if __name__ == "__main__":
    proj = Path(__file__).resolve().parent.parent
    out = proj.parent / f"{PACKAGE_NAME}_{VERSION}_all.deb"
    built = build_deb(proj, out)
    print(f"Built {built} ({built.stat().st_size} bytes)")
