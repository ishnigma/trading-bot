# gunicorn.conf.py - Production settings for the Trading Bot

import multiprocessing

# Worker settings
workers = multiprocessing.cpu_count() * 2 + 1          # Auto-scale based on CPU cores
worker_class = "uvicorn.workers.UvicornWorker"         # Async worker for FastAPI
worker_connections = 1000
timeout = 120
keepalive = 5
max_requests = 1000
max_requests_jitter = 50

# Binding
bind = "0.0.0.0:8000"

# Logging
accesslog = "-"                     # Log to stdout (captured by systemd journal)
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process naming
proc_name = "tv_trading_bot"

# Graceful timeout
graceful_timeout = 30

# Preload app for better performance (optional - comment out if you have issues)
# preload_app = True

# Limit request line size (helps with large webhook payloads)
limit_request_line = 8190
limit_request_fields = 100
limit_request_field_size = 8190
