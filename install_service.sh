#!/bin/bash
set -e

PROJECT_DIR="$HOME/tv_bot"
SERVICE_FILE="/etc/systemd/system/tvbot.service"

if [ ! -d "$PROJECT_DIR" ]; then
  echo "Expected project at $PROJECT_DIR"
  echo "Move this folder to ~/tv_bot first."
  exit 1
fi

cd "$PROJECT_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

sudo tee "$SERVICE_FILE" > /dev/null <<SERVICE
[Unit]
Description=TradingView Alpaca Bot
After=network.target

[Service]
User=$USER
WorkingDirectory=$PROJECT_DIR
ExecStart=$PROJECT_DIR/.venv/bin/python -m uvicorn app:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

sudo systemctl daemon-reload
sudo systemctl enable tvbot.service
sudo systemctl restart tvbot.service
sudo systemctl status tvbot.service
