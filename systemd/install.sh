#!/usr/bin/env bash
# ==============================================================================
# Backup Health Monitor — Automated Systemd Installer (Bare-Metal / Linux VM)
#
# Supported distributions:
#   - Debian / Ubuntu / Linux Mint
#   - RHEL / CentOS / Rocky Linux / AlmaLinux / Fedora
#   - openSUSE / SLES
#
# Usage:
#   sudo bash install.sh          (defaults to service name: BM)
#   sudo bash install.sh BM       (custom service name, e.g. BM)
#   sudo SERVICE_NAME=BM bash install.sh
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Allow custom service name via first argument or environment variable (default: BM)
SERVICE_NAME="BM"
if [ $# -ge 1 ]; then
  if [[ "$1" == "--name" || "$1" == "-n" ]]; then
    SERVICE_NAME="${2:-BM}"
  else
    SERVICE_NAME="$1"
  fi
elif [ -n "${SERVICE_NAME:-}" ]; then
  SERVICE_NAME="${SERVICE_NAME}"
fi

INSTALL_DIR="/opt/backup-health-monitor"
CONFIG_DIR="/etc/backup-health-monitor"
STATE_DIR="/var/lib/backup-health-monitor"
SERVICE_USER="backupmon"
SERVICE_GROUP="backupmon"

echo -e "${BLUE}===================================================================${NC}"
echo -e "${BLUE}   Backup Health Monitor — Automated Systemd Installation          ${NC}"
echo -e "${BLUE}   Target Service Name: ${YELLOW}${SERVICE_NAME}.service${NC}"
echo -e "${BLUE}===================================================================${NC}"

# Check for root privileges
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Error: This script must be run as root (use sudo).${NC}"
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── 1. Detect Package Manager and Install Dependencies ───────────
echo -e "\n${YELLOW}[1/7] Detecting OS and installing runtime dependencies...${NC}"

if command -v apt-get >/dev/null 2>&1; then
  echo "Detected Debian/Ubuntu-based distribution."
  apt-get update -qq
  apt-get install -y -qq python3 python3-venv python3-pip gzip tar unzip curl
elif command -v dnf >/dev/null 2>&1; then
  echo "Detected RHEL/Fedora/Rocky/AlmaLinux distribution (dnf)."
  dnf install -y -q python3 python3-pip gzip tar unzip curl
elif command -v yum >/dev/null 2>&1; then
  echo "Detected RHEL/CentOS distribution (yum)."
  yum install -y -q python3 python3-pip gzip tar unzip curl
elif command -v zypper >/dev/null 2>&1; then
  echo "Detected SUSE distribution (zypper)."
  zypper install -y python3 python3-pip gzip tar unzip curl
else
  echo -e "${YELLOW}Warning: Unknown package manager. Please ensure python3, python3-venv, gzip, tar, unzip are installed.${NC}"
fi

# ── 2. Create System User and Group ──────────────────────────────
echo -e "\n${YELLOW}[2/7] Creating service user '${SERVICE_USER}'...${NC}"

if ! getent group "${SERVICE_GROUP}" >/dev/null; then
  groupadd -r "${SERVICE_GROUP}"
  echo "Created group ${SERVICE_GROUP}."
fi

if ! getent passwd "${SERVICE_USER}" >/dev/null; then
  useradd -r -g "${SERVICE_GROUP}" -d "${INSTALL_DIR}" -s /sbin/nologin \
    -c "Backup Health Monitor daemon" "${SERVICE_USER}"
  echo "Created user ${SERVICE_USER}."
fi

# ── 3. Create Directories ────────────────────────────────────────
echo -e "\n${YELLOW}[3/7] Creating application directories...${NC}"

mkdir -p "${INSTALL_DIR}"
mkdir -p "${CONFIG_DIR}"
mkdir -p "${STATE_DIR}"

chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_DIR}"
chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${STATE_DIR}"
chmod 750 "${INSTALL_DIR}"
chmod 750 "${STATE_DIR}"

# ── 4. Copy Application Files ───────────────────────────────────
echo -e "\n${YELLOW}[4/7] Installing application source files to ${INSTALL_DIR}...${NC}"

cp -r "${SCRIPT_DIR}/app" "${INSTALL_DIR}/"
cp "${SCRIPT_DIR}/requirements.txt" "${INSTALL_DIR}/"

if [ -f "${SCRIPT_DIR}/systemd/backwatch.env" ]; then
  if [ ! -f "${CONFIG_DIR}/backwatch.env" ]; then
    cp "${SCRIPT_DIR}/systemd/backwatch.env" "${CONFIG_DIR}/backwatch.env"
    chmod 640 "${CONFIG_DIR}/backwatch.env"
    chown "root:${SERVICE_GROUP}" "${CONFIG_DIR}/backwatch.env"
  fi
fi

if [ -f "${SCRIPT_DIR}/config/config.yml" ]; then
  if [ ! -f "${CONFIG_DIR}/config.yml" ]; then
    cp "${SCRIPT_DIR}/config/config.yml" "${CONFIG_DIR}/config.yml"
    # Adjust paths for bare-metal systemd
    sed -i "s|backup_root: /backups|backup_root: /var/backups/db|g" "${CONFIG_DIR}/config.yml"
    sed -i "s|state_dir: /state|state_dir: /var/lib/backup-health-monitor|g" "${CONFIG_DIR}/config.yml"
    sed -i "s|db_path: /state/history.db|db_path: /var/lib/backup-health-monitor/history.db|g" "${CONFIG_DIR}/config.yml"
    chmod 644 "${CONFIG_DIR}/config.yml"
  fi
fi

chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_DIR}"

# ── 5. Set Up Python Virtual Environment ────────────────────────
echo -e "\n${YELLOW}[5/7] Creating Python virtual environment and installing dependencies...${NC}"

python3 -m venv "${INSTALL_DIR}/venv"
"${INSTALL_DIR}/venv/bin/pip" install --upgrade pip -q
"${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" -q

chown -R "${SERVICE_USER}:${SERVICE_GROUP}" "${INSTALL_DIR}/venv"

# ── 6. Install Systemd Service Unit ─────────────────────────────
echo -e "\n${YELLOW}[6/7] Installing systemd unit file as ${SERVICE_NAME}.service...${NC}"

# Generate customized service file with chosen service name and alias
sed -e "s|SyslogIdentifier=backup-health-monitor|SyslogIdentifier=${SERVICE_NAME}|g" \
    -e "s|Description=Backup Health Monitor and Retention Service|Description=Backup Health Monitor Service (${SERVICE_NAME})|g" \
    "${SCRIPT_DIR}/systemd/backup-health-monitor.service" > "/etc/systemd/system/${SERVICE_NAME}.service"

# Add Alias if service name is BM so both BM and backup-health-monitor resolve
if [ "${SERVICE_NAME}" != "backup-health-monitor" ]; then
  if ! grep -q "Alias=" "/etc/systemd/system/${SERVICE_NAME}.service"; then
    echo "Alias=backup-health-monitor.service" >> "/etc/systemd/system/${SERVICE_NAME}.service"
  fi
fi

chmod 644 "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload

# ── 7. Enable and Start Service ─────────────────────────────────
echo -e "\n${YELLOW}[7/7] Starting and enabling ${SERVICE_NAME}.service...${NC}"

systemctl enable "${SERVICE_NAME}"
systemctl restart "${SERVICE_NAME}"

# Verify service status
sleep 2
if systemctl is-active --quiet "${SERVICE_NAME}"; then
  echo -e "${GREEN}Service ${SERVICE_NAME} is active and running!${NC}"
else
  echo -e "${RED}Service failed to start. Showing status:${NC}"
  systemctl status "${SERVICE_NAME}" --no-pager || true
fi

echo -e "\n${GREEN}===================================================================${NC}"
echo -e "${GREEN}   Installation Complete!                                          ${NC}"
echo -e "${GREEN}===================================================================${NC}"
echo -e "
🌐 ${BLUE}Built-in Web UI & Dashboard:${NC}
   http://localhost:9108/
   (Or http://<server-ip>:9108/ from any browser - No Docker/Grafana needed!)

📊 ${BLUE}Prometheus Metrics Endpoint:${NC}
   http://localhost:9108/metrics

⚙️  ${BLUE}Configuration files:${NC}
   - Config file:  ${CONFIG_DIR}/config.yml
   - Environment:  ${CONFIG_DIR}/backwatch.env
   - State & DB:   ${STATE_DIR}/

📜 ${BLUE}Useful Commands:${NC}
   systemctl status ${SERVICE_NAME}
   journalctl -u ${SERVICE_NAME} -f
   systemctl restart ${SERVICE_NAME}
"
