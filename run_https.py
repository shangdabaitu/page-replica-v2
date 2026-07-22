#!/usr/bin/env python3
"""启动带自签名 HTTPS 的 Flask 服务"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.replicate import app

CERT = os.environ.get("SSL_CERT", "/tmp/server.crt")
KEY = os.environ.get("SSL_KEY", "/tmp/server.key")

if __name__ == "__main__":
    ssl_context = None
    if os.path.exists(CERT) and os.path.exists(KEY):
        ssl_context = (CERT, KEY)
        print(f"HTTPS enabled with {CERT} / {KEY}")
    else:
        print("WARNING: cert/key not found, running HTTP")
    app.run(host="0.0.0.0", port=5000, threaded=True, debug=False, ssl_context=ssl_context)
