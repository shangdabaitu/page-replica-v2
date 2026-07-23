#!/usr/bin/env python3
"""页面资源内联模块"""
import re
import base64
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from core.fetcher import fetch_resource, normalize_url, decode_html, data_url
import config


def inline_page(html: str, base_url: str) -> str:
    """把页面中的外部 CSS/图片资源内联；脚本不内联，后续由 freeze 阶段删除。"""
    soup = BeautifulSoup(html, "html.parser")

    # 1. 内联 <link rel="stylesheet">
    for tag in soup.find_all("link", rel="stylesheet", href=True):
        href = normalize_url(tag["href"], base_url)
        if not href:
            continue
        data, ct = fetch_resource(href)
        if data is None:
            continue
        css = decode_html(data, ct)
        # 处理 CSS 中的相对 url(...)
        css = _inline_css_urls(css, href)
        style_tag = soup.new_tag("style")
        style_tag.string = css
        tag.replace_with(style_tag)

    # 2. 脚本不再内联，避免 </script> 截断导致脚本源码泄漏到正文。

    # 3. 内联 <img src="...">
    for tag in soup.find_all("img", src=True):
        src = normalize_url(tag["src"], base_url)
        if not src:
            continue
        data, ct = fetch_resource(src)
        if data is None:
            continue
        if len(data) > config.MAX_RESOURCE_BYTES:
            tag["src"] = src
            continue
        du = data_url(data, ct)
        if du:
            tag["src"] = du

    # 4. 内联其他使用 src 的标签（iframe 除外）
    for tag in soup.find_all(src=True):
        if tag.name in ("script", "img", "iframe"):
            continue
        src = normalize_url(tag["src"], base_url)
        if not src:
            continue
        data, ct = fetch_resource(src)
        if data is None:
            continue
        du = data_url(data, ct)
        if du:
            tag["src"] = du

    return str(soup)


def _inline_css_urls(css: str, css_url: str) -> str:
    """把 CSS 中的 url(...) 相对路径转成绝对路径或 data URL。"""
    def repl(match):
        raw = match.group(1).strip("\"'\t ")
        abs_url = normalize_url(raw, css_url)
        if not abs_url:
            return match.group(0)
        data, ct = fetch_resource(abs_url)
        if data is None:
            return f'url("{abs_url}")'
        if len(data) > config.MAX_RESOURCE_BYTES:
            return f'url("{abs_url}")'
        du = data_url(data, ct)
        return f'url("{du}")' if du else match.group(0)

    return re.sub(r"url\(([^)]+)\)", repl, css)
