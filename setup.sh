#!/usr/bin/env bash
#
# setup.sh — Bootstrap yt-mute-bot on a fresh Ubuntu LXC/VM
#
# Usage:
#   curl -fsSL <this-file> | sudo bash
#   -- or --
#   sudo bash setup.sh
#
set -euo pipefail

APP_DIR="/opt/yt-mute-bot"
LOG_DIR="/var/log/yt-mute-bot"
VENV_DIR="${APP_DIR}/venv"
SVC_USER="ytmute"

echo "=== yt-mute-bot setup ==="

# ---- 1. System deps ------------------------------------------------------
echo "[1/6] Installing system dependencies..."
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-pip \
    libnss3 libatk-bridge2.0-0 libdrm2 libxcomposite1 \
    libxdamage1 libxrandr2 libgbm1 libpango-1.0-0 \
    libcairo2 libasound2t64 libxshmfence1 libxfixes3 \
    fonts-liberation xdg-utils \
    > /dev/null 2>&1

# For headed login (X11 forwarding or VNC):
apt-get install -y -qq xvfb > /dev/null 2>&1 || true

# ---- 2. Service user -----------------------------------------------------
echo "[2/6] Creating service user '${SVC_USER}'..."
if ! id "${SVC_USER}" &>/dev/null; then
    useradd -r -m -d /home/${SVC_USER} -s /bin/bash ${SVC_USER}
fi

# ---- 3. App directory -----------------------------------------------------
echo "[3/6] Setting up ${APP_DIR}..."
mkdir -p "${APP_DIR}" "${LOG_DIR}" "${LOG_DIR}/screenshots"
cp -f yt_mute_bot.py "${APP_DIR}/"
cp -f config.yaml "${APP_DIR}/"
chmod +x "${APP_DIR}/yt_mute_bot.py"

# ---- 4. Python venv + deps -----------------------------------------------
echo "[4/6] Creating Python virtualenv and installing deps..."
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --quiet --upgrade pip
"${VENV_DIR}/bin/pip" install --quiet playwright pyyaml
"${VENV_DIR}/bin/python" -m playwright install chromium
"${VENV_DIR}/bin/python" -m playwright install-deps chromium 2>/dev/null || true

# ---- 5. Permissions -------------------------------------------------------
echo "[5/6] Setting permissions..."
chown -R ${SVC_USER}:${SVC_USER} "${APP_DIR}" "${LOG_DIR}"

# ---- 6. systemd service + timer -------------------------------------------
echo "[6/6] Installing systemd units..."

cat > /etc/systemd/system/yt-mute-bot.service << 'UNIT'
[Unit]
Description=YouTube Studio Copyright Mute Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=ytmute
Group=ytmute
WorkingDirectory=/opt/yt-mute-bot
ExecStart=/opt/yt-mute-bot/venv/bin/python /opt/yt-mute-bot/yt_mute_bot.py -c /opt/yt-mute-bot/config.yaml
Environment=HOME=/home/ytmute
TimeoutStartSec=86400
# Allow up to 24h for long mute runs

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/yt-mute-bot.timer << 'TIMER'
[Unit]
Description=Run yt-mute-bot periodically

[Timer]
# Run every 30 minutes. The bot is idempotent — if nothing to do, it exits fast.
OnBootSec=5min
OnUnitActiveSec=30min
RandomizedDelaySec=60
Persistent=true

[Install]
WantedBy=timers.target
TIMER

systemctl daemon-reload
echo ""
echo "=== Setup complete ==="
echo ""
echo "NEXT STEPS:"
echo ""
echo "  1. INITIAL LOGIN (must be done once, interactively):"
echo ""
echo "     If you have a desktop/X11 session on this machine:"
echo "       sudo -u ${SVC_USER} ${VENV_DIR}/bin/python ${APP_DIR}/yt_mute_bot.py --login"
echo ""
echo "     If headless (SSH), use Xvfb + X11 forwarding:"
echo "       ssh -X user@this-host"
echo "       sudo -u ${SVC_USER} DISPLAY=:0 xvfb-run ${VENV_DIR}/bin/python ${APP_DIR}/yt_mute_bot.py --login"
echo ""
echo "  2. TEST a single run:"
echo "       sudo -u ${SVC_USER} ${VENV_DIR}/bin/python ${APP_DIR}/yt_mute_bot.py"
echo ""
echo "  3. ENABLE the timer for automatic runs:"
echo "       systemctl enable --now yt-mute-bot.timer"
echo ""
echo "  4. CHECK status:"
echo "       systemctl status yt-mute-bot.timer"
echo "       journalctl -u yt-mute-bot.service -f"
echo "       tail -f ${LOG_DIR}/yt-mute-bot.log"
echo ""
echo "  5. DISABLE / STOP:"
echo "       systemctl disable --now yt-mute-bot.timer"
echo ""
echo "  6. MANUAL single-video run:"
echo "       sudo -u ${SVC_USER} ${VENV_DIR}/bin/python ${APP_DIR}/yt_mute_bot.py --video VIDEO_ID"
echo ""
