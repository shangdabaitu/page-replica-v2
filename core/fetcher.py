#!/usr/bin/env python3
"""HTTP 抓取模块（带基础防封/抗压措施）"""
import random
import re
import base64
import time
import requests
from urllib.parse import urljoin, urlparse
from pathlib import Path

import config

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36 Edg/118.0.2088.46",
]


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://cp.titan007.com/buy/JingCai.aspx?typeID=101&oddstype=2",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-site",
        "Cache-Control": "max-age=0",
    })
    # 增大连接池，减少因连接复用导致的异常
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=10,
        pool_maxsize=20,
        max_retries=0,  # 我们在外层自己控制重试和退避
    )
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


_session = _make_session()
_resource_cache: dict[str, tuple[bytes, str | None]] = {}


def _rotate_headers():
    """轮换 User-Agent，降低单一指纹被封概率。"""
    _session.headers["User-Agent"] = random.choice(_USER_AGENTS)


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


def fetch_url(url: str, timeout: int = config.REQUEST_TIMEOUT, retries: int = config.MAX_RETRIES) -> tuple[bytes | None, str | None]:
    """抓取 URL，返回 (content_bytes, content_type)。带指数退避重试和 SSL 容错。

    对客户端错误（4xx）直接失败不重试，避免在 404 资源上浪费时间。
    """
    last_error = None
    for attempt in range(retries + 1):
        try:
            # 基础请求间隔 + 随机抖动，避免请求过于密集
            time.sleep(random.uniform(0.5, 2.0))
            _rotate_headers()
            r = _session.get(url, timeout=timeout, stream=False, verify=True)
            r.raise_for_status()
            return r.content, r.headers.get("content-type")
        except requests.exceptions.SSLError as e:
            last_error = e
            # 对 SSL 问题尝试忽略证书验证再抓一次
            try:
                time.sleep(random.uniform(0.5, 1.5))
                r = _session.get(url, timeout=timeout, stream=False, verify=False)
                r.raise_for_status()
                return r.content, r.headers.get("content-type")
            except Exception as e2:
                last_error = e2
        except requests.exceptions.HTTPError as e:
            last_error = e
            # 4xx 客户端错误不重试
            if e.response is not None and 400 <= e.response.status_code < 500:
                break
        except Exception as e:
            last_error = e

        if attempt < retries:
            wait = min(2 ** attempt + random.uniform(0, 1), 30)
            print(f"[WARN] 抓取失败，{wait:.1f}s 后重试 ({attempt + 1}/{retries}): {url}")
            time.sleep(wait)

    print(f"[WARN] 抓取失败: {url} -> {last_error}")
    return None, None


_CACHE_MISS_SENTINEL = object()


def fetch_resource(url: str, timeout: int = 5, retries: int = 0) -> tuple[bytes | None, str | None]:
    """抓取资源并缓存（失败结果也缓存，避免同一 404 资源被反复请求）。

    默认使用较短超时、不重试，避免资源内联阶段被慢资源拖垮整个任务。
    """
    cached = _resource_cache.get(url, _CACHE_MISS_SENTINEL)
    if cached is not _CACHE_MISS_SENTINEL:
        return cached
    data, ct = fetch_url(url, timeout=timeout, retries=retries)
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
