#!/usr/bin/env python3
"""用 Headless Chromium 渲染页面，获取 JS 执行后的 DOM。"""
import os
import shutil

from playwright.sync_api import sync_playwright


def _find_chrome() -> str | None:
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    return None


def render_html(url: str, wait_ms: int = 6000) -> str:
    """在 Headless 浏览器中打开 url，返回渲染后的完整 HTML。"""
    chrome = _find_chrome()
    if not chrome:
        raise RuntimeError("未找到 Chrome/Chromium 可执行文件")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=chrome,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(wait_ms)
            html = page.content()
            return html
        finally:
            browser.close()
