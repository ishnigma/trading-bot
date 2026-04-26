#!/bin/bash
# install_service.sh - Production-ready installer for TradingView Alpaca Trading Bot
# Uses Gunicorn with external config file for better maintainability

set -e

PROJECT_DIR="$HOME/tv_bot"
SERVICE_FILE="/etc/systemd/system/tvbot.service"
VENV_PATH="$PROJECT_DIR/.venv"

echo "🚀 Setting up Trading Bot service..."

if [ ! -d "$PROJECT_DIR" ]; then
  echo "❌ Error: Project directory $PROJECT_DIR not found!"
  echo "Please clone your repo to ~/tv_bot first."
  exit 1
fi

cd "$PROJECT_DIR"

# Create virtual environment and install dependencies
python3 -m venv "$VENV_PATH"
source "$VENV_PATH/bin/activate"
pip install --upgrade pip
pip install -r requirements.txt

# Make sure gunicorn.conf.py exists
if [ ! -f "gunicorn.conf.py" ]; then
  echo "⚠️  gunicorn.conf.py not found. Creating a basic one..."
  cat > gunicorn.conf.py << 'EOF'
# Basic gunicorn config (auto-generated)
workers = 2
worker_class = "uvicorn.workers.UvicornWorker"
bind = "0.0.0.0:8000"
timeout = 120
keepalive = 5
loglevel = "info"
EOF
fi

# Create improved systemd service
sudo tee "$SERVICE_FILE" > /dev/null <<SERVICE
[Unit]
Description=TradingView Alpaca Trading Bot
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$PROJECT_DIR
Environment=PATH=$VENV_PATH/bin:/usr/local/bin:/usr/bin
# Uncomment the line below if you want to load variables from .env
# EnvironmentFile=$PROJECT_DIR/.env

# Production Gunicorn command using external config
ExecStart=$VENV_PATH/bin/gunicorn app:app \
    -c $PROJECT_DIR/gunicorn.conf.py \
    --access-logfile - \
    --error-logfile -

Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
# Increase file descriptor limit for better webhook handling
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
SERVICE

# Reload and restart the service
sudo systemctl daemon-reload
sudo systemctl enable tvbot.service
sudo systemctl restart tvbot.service

echo "✅ Installation completed successfully!"
echo ""
echo "Useful commands:"
echo "   sudo systemctl status tvbot.service     # Check if running"
echo "   sudo journalctl -u tvbot.service -f    # Live logs"
echo "   sudo systemctl restart tvbot.service   # Restart bot"
echo "   sudo systemctl stop tvbot.service      # Stop bot"
echo ""
echo "Dashboard URL: http://YOUR_SERVER_IP:8000/dashboard"
echo "Remember to set TRADING_MODE=paper in .env first!"
