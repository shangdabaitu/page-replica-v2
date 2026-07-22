#!/usr/bin/env python3
"""启动 ngrok 隧道（必须在没有 http_proxy 的环境下运行）"""
import os
import sys

# 清除代理环境变量，否则 ngrok 会报需要付费
for key in ('http_proxy', 'https_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'no_proxy', 'NO_PROXY'):
    os.environ.pop(key, None)

from pyngrok import ngrok

NGROK_TOKEN = os.environ.get("NGROK_AUTHTOKEN", "")
if not NGROK_TOKEN:
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
tunnel = ngrok.connect(5000, "http")
print(tunnel.public_url)

# 保持进程
import time
try:
    while True:
        time.sleep(10)
except KeyboardInterrupt:
    ngrok.kill()
