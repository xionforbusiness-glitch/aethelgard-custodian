#!/bin/bash
# ==============================================================================
# 🏛️ AETHELGARD 24/7 CUSTODIAN DEPLOYMENT SCRIPT
# Target: Google Cloud Platform (e2-micro VM) / Daytona / Linux VPS (Ubuntu/Debian)
# ==============================================================================

set -e

echo "============================================================"
echo "🚀 AETHELGARD 24/7 VAULT CUSTODIAN - 1-CLICK DEPLOYER"
echo "============================================================"

# Ensure script is run with appropriate permissions
if [ "$EUID" -ne 0 ]; then
  SUDO="sudo"
else
  SUDO=""
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "📦 [1/5] Updating system packages and installing prerequisites..."
$SUDO apt-get update -y
$SUDO apt-get install -y curl git ffmpeg psmisc pciutils lshw zstd python3 python3-pip python3-venv

# Install Node.js 22 LTS (Required for OmniRoute Antigravity router)
echo "📦 [2/5] Installing Node.js 22 LTS..."
if ! command -v node >/dev/null 2>&1 || ! node -v | grep -q "v22"; then
  $SUDO apt-get remove --purge -y libnode-dev libnode72 nodejs npm 2>/dev/null || true
  curl -fsSL https://deb.nodesource.com/setup_22.x | $SUDO bash -
  $SUDO apt-get install -y -o Dpkg::Options::="--force-overwrite" nodejs
fi
echo "✅ Node.js version: $(node -v)"

# Install OmniRoute globally
echo "📦 [3/5] Installing OmniRoute (Google Antigravity Pro Plan Router)..."
if ! command -v omniroute >/dev/null 2>&1; then
  $SUDO npm install -g omniroute --prefer-offline --no-audit --silent --no-fund
fi
echo "✅ OmniRoute installed."

# Install Python requirements
echo "🐍 [4/5] Installing Hermes Agent and Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt
echo "✅ Python libraries installed."

# Configure Systemd Service for 24/7 Autostart & Auto-Restart (if systemd available)
echo "⚙️ [5/5] Configuring 24/7 Always-On Background Daemon..."
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  SERVICE_FILE="/etc/systemd/system/aethelgard.service"
  echo "Writing systemd service file to $SERVICE_FILE..."
  
  $SUDO tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Aethelgard 24/7 Vault Custodian (Hermes + Antigravity Pro)
After=network.target network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$SCRIPT_DIR
ExecStart=/usr/bin/python3 $SCRIPT_DIR/run.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

  $SUDO systemctl daemon-reload
  $SUDO systemctl enable aethelgard.service
  $SUDO systemctl restart aethelgard.service

  echo "============================================================"
  echo "🎉 [SUCCESS] AETHELGARD 24/7 SERVICE IS RUNNING!"
  echo "============================================================"
  echo "📱 Your Telegram bot (@aethelgard_vault_bot) is active."
  echo "📊 View live logs: sudo journalctl -u aethelgard -f"
  echo "🛑 Stop service:   sudo systemctl stop aethelgard"
  echo "🔄 Restart:        sudo systemctl restart aethelgard"
  echo "============================================================"
else
  # Non-systemd environment (e.g. Daytona container or Docker sandbox)
  echo "💡 Systemd not detected (Running in container or sandbox mode)."
  echo "🚀 Launching in continuous background mode via nohup..."
  pkill -9 -f "run.py" 2>/dev/null || true
  nohup python3 "$SCRIPT_DIR/run.py" > /tmp/aethelgard.log 2>&1 &
  echo "============================================================"
  echo "🎉 [SUCCESS] AETHELGARD DAEMON LAUNCHED IN BACKGROUND!"
  echo "============================================================"
  echo "📊 View live logs: tail -f /tmp/aethelgard.log"
  echo "============================================================"
fi
