#!/usr/bin/env python3
"""启动 Flask HTTP 服务并通过 ngrok 暴露公网 HTTPS 地址"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pyngrok import ngrok
from api.replicate import app

NGROK_TOKEN = os.environ.get("NGROK_AUTHTOKEN", "")
if not NGROK_TOKEN:
    # fallback read from .env
    try:
        with open(os.path.join(os.path.dirname(__file__), ".env")) as f:
            for line in f:
                if line.startswith("NGROK_AUTHTOKEN="):
                    NGROK_TOKEN = line.strip().split("=", 1)[1]
                    break
    except Exception:
        pass

if not NGROK_TOKEN:
    print("ERROR: NGROK_AUTHTOKEN not found")
    sys.exit(1)

ngrok.set_auth_token(NGROK_TOKEN)

# 在后台启动 Flask
port = 5000
server = threading.Thread(
    target=lambda: app.run(host="0.0.0.0", port=port, threaded=True, debug=False),
    daemon=True,
)
server.start()

# 等待服务启动
time.sleep(2)

# 启动 ngrok 隧道
tunnel = ngrok.connect(port, "http")
print(f"Public URL: {tunnel.public_url}")
print(f"Local URL: http://127.0.0.1:{port}")

# 保持运行
try:
    while True:
        time.sleep(10)
except KeyboardInterrupt:
    ngrok.kill()
