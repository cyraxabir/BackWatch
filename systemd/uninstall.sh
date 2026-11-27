#!/usr/bin/env bash
# ==============================================================================
# Backup Health Monitor — Automated Systemd Uninstaller
# ==============================================================================

# Usage:
#   sudo bash uninstall.sh        (defaults to BM)
#   sudo bash uninstall.sh BM     (or custom service name)
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

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

if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Error: This script must be run as root (use sudo).${NC}"
  exit 1
fi

echo -e "${YELLOW}Stopping and disabling ${SERVICE_NAME} (and backup-health-monitor)...${NC}"
systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
systemctl stop backup-health-monitor 2>/dev/null || true
systemctl disable backup-health-monitor 2>/dev/null || true

echo "Removing systemd service file..."
rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
rm -f "/etc/systemd/system/backup-health-monitor.service"
systemctl daemon-reload

echo "Removing application files from ${INSTALL_DIR}..."
rm -rf "${INSTALL_DIR}"

read -p "Do you want to delete configuration (${CONFIG_DIR}) and database state (${STATE_DIR})? [y/N]: " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
  rm -rf "${CONFIG_DIR}"
  rm -rf "${STATE_DIR}"
  echo "Deleted configuration and state directories."
else
  echo "Preserved ${CONFIG_DIR} and ${STATE_DIR}."
fi

read -p "Do you want to remove the system user '${SERVICE_USER}'? [y/N]: " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
  userdel "${SERVICE_USER}" 2>/dev/null || true
  groupdel "${SERVICE_USER}" 2>/dev/null || true
  echo "Removed user and group ${SERVICE_USER}."
fi

echo -e "${GREEN}Backup Health Monitor uninstalled successfully.${NC}"
