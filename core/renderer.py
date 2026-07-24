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
    return render_and_capture(url, wait_ms=wait_ms)[0]


def render_and_capture(
    url: str,
    wait_ms: int = 6000,
    viewport_width: int = 1440,
    viewport_height: int = 900,
) -> tuple[str, bytes]:
    """在 Headless 浏览器中打开 url，同时返回渲染后的完整 HTML 和首屏截图（PNG）。"""
    chrome = _find_chrome()
    if not chrome:
        raise RuntimeError("未找到 Chrome/Chromium 可执行文件")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=chrome,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        try:
            context = browser.new_context(
                viewport={"width": viewport_width, "height": viewport_height},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.set_extra_http_headers({"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
            page.goto(url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(wait_ms)
            html = page.content()
            png = page.screenshot(full_page=False, type="png")
            return html, png
        finally:
            browser.close()
