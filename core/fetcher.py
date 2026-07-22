#!/usr/bin/env python3
"""HTTP 抓取模块"""
import re
import base64
import requests
from urllib.parse import urljoin, urlparse
from pathlib import Path

import config

_session = requests.Session()
_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://cp.titan007.com/buy/JingCai.aspx?typeID=101&oddstype=2",
})

_resource_cache: dict[str, tuple[bytes, str | None]] = {}


def decode_html(data: bytes, content_type: str | None) -> str:
    """尝试用正确的编码解码 HTML。"""
    enc = None
    if content_type:
        m = re.search(r"charset=([\w-]+)", content_type, re.I)
        if m:
            enc = m.group(1)
    if not enc and data:
        m = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", data, re.I)
        if m:
            enc = m.group(1).decode("ascii", "ignore")
    if not enc:
        enc = "utf-8"
    try:
        return data.decode(enc, "ignore")
    except Exception:
        return data.decode("gbk", "ignore")


def fetch_url(url: str, timeout: int = config.REQUEST_TIMEOUT) -> tuple[bytes | None, str | None]:
    """抓取 URL，返回 (content_bytes, content_type)。"""
    try:
        r = _session.get(url, timeout=timeout, stream=False)
        r.raise_for_status()
        return r.content, r.headers.get("content-type")
    except Exception as e:
        print(f"[WARN] 抓取失败: {url} -> {e}")
        return None, None


def fetch_resource(url: str) -> tuple[bytes | None, str | None]:
    """抓取资源并缓存。"""
    if url in _resource_cache:
        return _resource_cache[url]
    data, ct = fetch_url(url)
    if data is not None:
        _resource_cache[url] = (data, ct)
    return data, ct


def normalize_url(url: str, base_url: str) -> str | None:
    """把相对 URL 转成绝对 URL，并过滤掉非法协议。"""
    if not url:
        return None
    url = url.strip()
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith(("http://", "https://")):
        abs_url = url
    elif url.startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
        return None
    else:
        abs_url = urljoin(base_url, url)
    parsed = urlparse(abs_url)
    if parsed.scheme not in ("http", "https"):
        return None
    return abs_url


def data_url(data: bytes, content_type: str | None) -> str | None:
    """把二进制资源转成 data URL。"""
    if data is None:
        return None
    mime = "application/octet-stream"
    if content_type:
        mime = content_type.split(";")[0].strip()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"
