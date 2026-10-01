#!/bin/bash
# ==============================================================================
# 🏛️ AETHELGARD 24/7 CUSTODIAN DEPLOYMENT SCRIPT
# Target: Google Cloud Platform (e2-micro VM) / Daytona / Linux VPS (Ubuntu/Debian)
# ==============================================================================

set -e

echo "============================================================"
echo "🚀 AETHELGARD 24/7 VAULT CUSTODIAN - 1-CLICK DEPLOYER"
echo "============================================================"

# Set non-interactive mode so purple dialog screens never block execution
export DEBIAN_FRONTEND=noninteractive
export NEEDRESTART_MODE=a
export NEEDRESTART_SUSPEND=1

# Ensure script is run with appropriate permissions
if [ "$EUID" -ne 0 ]; then
  SUDO="sudo"
else
  SUDO=""
fi

# Accelerate apt by forcing IPv4 (avoids slow IPv6 routing timeouts in Google Cloud)
echo 'Acquire::ForceIPv4 "true";' | $SUDO tee /etc/apt/apt.conf.d/99force-ipv4 >/dev/null 2>&1 || true
$SUDO sed -i 's/#$nrconf{restart} = .*/$nrconf{restart} = "a";/' /etc/needrestart/needrestart.conf 2>/dev/null || true

# Disable IPv6 system-wide to eliminate 30-second network timeouts on Google Cloud
$SUDO sysctl -w net.ipv6.conf.all.disable_ipv6=1 >/dev/null 2>&1 || true
$SUDO sysctl -w net.ipv6.conf.default.disable_ipv6=1 >/dev/null 2>&1 || true

# Configure 3GB Swap memory if not present (Crucial for 1GB RAM e2-micro VMs)
if [ $(free -m | awk '/^Swap:/ {print $2}') -lt 2048 ]; then
  echo "💾 [0/5] Configuring 3GB Swap memory to eliminate 1GB RAM bottlenecks..."
  $SUDO fallocate -l 3G /swapfile 2>/dev/null || $SUDO dd if=/dev/zero of=/swapfile bs=1M count=3072
  $SUDO chmod 600 /swapfile
  $SUDO mkswap /swapfile >/dev/null 2>&1
  $SUDO swapon /swapfile >/dev/null 2>&1
  echo "✅ 3GB Swap memory activated (Total effective RAM: 4 GB)."
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "📦 [1/5] Updating system packages and installing prerequisites..."
$SUDO apt-get update -y
$SUDO apt-get install -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" curl git ffmpeg psmisc pciutils lshw zstd python3 python3-pip python3-venv

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
  echo "⏳ Downloading and unpacking OmniRoute (this takes ~2-3 minutes)..."
  NODE_OPTIONS="--max-old-space-size=2048" $SUDO npm install -g omniroute --omit=dev --no-audit --no-fund --loglevel=info
fi
echo "✅ OmniRoute installed."

# Install Python 3.11 & Hermes requirements (hermes-agent requires Python >= 3.11)
echo "🐍 [4/5] Installing Python 3.11 and Hermes Agent..."
if ! command -v python3.11 >/dev/null 2>&1; then
  echo "📦 Adding Python 3.11 repository (deadsnakes)..."
  $SUDO add-apt-repository ppa:deadsnakes/ppa -y
  $SUDO apt-get update -y
  $SUDO apt-get install -y python3.11 python3.11-venv python3.11-dev
fi

VENV_DIR="$SCRIPT_DIR/venv"
if [ ! -d "$VENV_DIR" ]; then
  echo "📦 Creating Python 3.11 virtual environment in $VENV_DIR..."
  python3.11 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r requirements.txt
$SUDO ln -sf "$VENV_DIR/bin/hermes" /usr/local/bin/hermes
echo "✅ Python 3.11 and Hermes Agent installed."

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
ExecStart=$VENV_DIR/bin/python3 $SCRIPT_DIR/run.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1
Environment=PATH=$VENV_DIR/bin:/usr/local/bin:/usr/bin:/bin
Environment=NODE_OPTIONS="--dns-result-order=ipv4first --max-old-space-size=1536"

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
  nohup "$VENV_DIR/bin/python3" "$SCRIPT_DIR/run.py" > /tmp/aethelgard.log 2>&1 &
  echo "============================================================"
  echo "🎉 [SUCCESS] AETHELGARD DAEMON LAUNCHED IN BACKGROUND!"
  echo "============================================================"
  echo "📊 View live logs: tail -f /tmp/aethelgard.log"
  echo "============================================================"
fi
