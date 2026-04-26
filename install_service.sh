#!/bin/bash
# install_service.sh - Production-ready installer for TradingView Alpaca Trading Bot
# Uses Gunicorn with external gunicorn.conf.py for better maintainability

set -e

PROJECT_DIR="$HOME/tv_bot"
SERVICE_FILE="/etc/systemd/system/tvbot.service"
VENV_PATH="$PROJECT_DIR/.venv"

echo "🚀 Setting up Trading Bot systemd service..."

# Check if project directory exists
if [ ! -d "$PROJECT_DIR" ]; then
  echo "❌ Error: Project directory $PROJECT_DIR not found!"
  echo "Please clone your GitHub repo to ~/tv_bot first."
  exit 1
fi

cd "$PROJECT_DIR"

# Setup virtual environment and install dependencies
echo "📦 Creating virtual environment and installing dependencies..."
python3 -m venv "$VENV_PATH"
source "$VENV_PATH/bin/activate"
pip install --upgrade pip
pip install -r requirements.txt

# Ensure gunicorn.conf.py exists
if [ ! -f "gunicorn.conf.py" ]; then
  echo "⚠️  gunicorn.conf.py not found. Creating a basic one..."
  cat > gunicorn.conf.py << 'EOF'
# gunicorn.conf.py - Basic production config
workers = 2
worker_class = "uvicorn.workers.UvicornWorker"
bind = "0.0.0.0:8000"
timeout = 120
keepalive = 5
loglevel = "info"
accesslog = "-"
errorlog = "-"
EOF
fi

# Create the systemd service file
echo "📝 Creating systemd service file..."
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
# EnvironmentFile=$PROJECT_DIR/.env   # Uncomment if you want to load .env directly

# Production Gunicorn command with external config
ExecStart=$VENV_PATH/bin/gunicorn app:app \
    -c $PROJECT_DIR/gunicorn.conf.py \
    --access-logfile - \
    --error-logfile -

Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
SERVICE

# Reload systemd, enable and restart the service
sudo systemctl daemon-reload
sudo systemctl enable tvbot.service
sudo systemctl restart tvbot.service

echo ""
echo "✅ Trading Bot service installed and started successfully!"
echo ""
echo "🔧 Useful commands:"
echo "   sudo systemctl status tvbot.service          # Check service status"
echo "   sudo journalctl -u tvbot.service -f         # View live logs"
echo "   sudo systemctl restart tvbot.service        # Restart the bot"
echo "   sudo systemctl stop tvbot.service           # Stop the bot"
echo "   sudo systemctl disable tvbot.service        # Disable auto-start"
echo ""
echo "🌐 Dashboard URL: http://YOUR_SERVER_IP:8000/dashboard"
echo ""
echo "⚠️  Remember:"
echo "   1. Copy .env.example to .env and configure it"
echo "   2. Start with TRADING_MODE=paper"
echo "   3. Test thoroughly before switching to live mode"
