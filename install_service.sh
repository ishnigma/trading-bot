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
