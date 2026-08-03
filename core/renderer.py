#!/usr/bin/env python3
"""用 Headless Chromium 渲染页面，获取 JS 执行后的 DOM。"""
import json
import re
import shutil
import subprocess
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright


# Playwright 自带 headless-shell 的固定缓存目录名（按当前安装版本）
_PLAYWRIGHT_HEADLESS_SHELL = Path.home() / ".cache" / "ms-playwright" / "chromium_headless_shell-1234" / "chrome-headless-shell-linux64" / "chrome-headless-shell"


# 每个线程持有独立的 Playwright 浏览器实例，避免同步 API 跨线程问题
_thread_local = threading.local()


def _find_chrome() -> str | None:
    """查找可用的系统 Chrome/Chromium；排除 snap 包装脚本，最后回退到 Playwright headless-shell。"""
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(name)
        if not path:
            continue
        # 排除 Ubuntu 的 snap 包装脚本
        try:
            if b"requires the chromium snap" in Path(path).read_bytes()[:2048]:
                continue
        except Exception:
            pass
        # 确保该二进制能启动
        try:
            result = subprocess.run(
                [path, "--version"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                return path
        except Exception:
            continue

    # 没有系统 Chrome 时，回退到 Playwright 已安装的 headless-shell
    if _PLAYWRIGHT_HEADLESS_SHELL.exists():
        try:
            result = subprocess.run(
                [str(_PLAYWRIGHT_HEADLESS_SHELL), "--version"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                return str(_PLAYWRIGHT_HEADLESS_SHELL)
        except Exception:
            pass
    return None


def _launch_kwargs() -> dict:
    """构造 Chromium 启动参数；优先使用系统 Chrome/已安装 headless-shell，否则让 Playwright 使用自带浏览器。"""
    kwargs: dict = {"args": ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]}
    chrome = _find_chrome()
    if chrome:
        kwargs["executable_path"] = chrome
    return kwargs


def get_thread_browser():
    """获取当前线程的浏览器实例（懒加载）。"""
    browser = getattr(_thread_local, "browser", None)
    if browser is None or not hasattr(browser, "is_connected") or not browser.is_connected():
        p = sync_playwright().start()
        browser = p.chromium.launch(**_launch_kwargs())
        _thread_local.playwright = p
        _thread_local.browser = browser
    return browser


def close_thread_browser():
    """关闭当前线程的浏览器实例。"""
    browser = getattr(_thread_local, "browser", None)
    if browser is not None:
        try:
            browser.close()
        except Exception:
            pass
        _thread_local.browser = None
    p = getattr(_thread_local, "playwright", None)
    if p is not None:
        try:
            p.stop()
        except Exception:
            pass
        _thread_local.playwright = None


def _new_page(browser, viewport_width: int = 1440, viewport_height: int = 900):
    """在共享浏览器上创建新上下文与页面，保持各页面隔离。"""
    context = browser.new_context(
        viewport={"width": viewport_width, "height": viewport_height},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    )
    context.set_extra_http_headers({"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
    return context, context.new_page()


def render_html(url: str, wait_ms: int = 4000) -> str:
    """在 Headless 浏览器中打开 url，返回渲染后的完整 HTML。"""
    return render_and_capture(url, wait_ms=wait_ms)[0]


def render_and_capture(
    url: str,
    wait_ms: int = 4000,
    viewport_width: int = 1440,
    viewport_height: int = 900,
) -> tuple[str, bytes]:
    """在 Headless 浏览器中打开 url，同时返回渲染后的完整 HTML 和首屏截图（PNG）。

    复用当前线程的浏览器实例，避免每页都重新启动 Chromium。
    """
    browser = get_thread_browser()
    context, page = _new_page(browser, viewport_width, viewport_height)
    try:
        # 用 domcontentloaded 替代 networkidle，避免慢资源阻塞页面渲染；
        # 之后固定等待 wait_ms 让 JS 数据加载完成。
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(wait_ms)
        html = page.content()
        png = page.screenshot(full_page=False, type="png")
        return html, png
    finally:
        context.close()


def render_league_page(url: str, wait_ms: int = 4000) -> str:
    """
    渲染联赛/赛事类型页，并在浏览器中依次点击所有轮次，
    把每轮赛程合并到同一张表中，确保静态化后所有比赛行都可见。
    """
    return render_league_with_tabs(url, wait_ms=wait_ms)[0]


def render_league_with_tabs(
    url: str, wait_ms: int = 4000
) -> tuple[str, dict[int, str], bytes]:
    """
    渲染联赛/赛事类型页，并捕获每个 showHtml JS 标签页的 DOM。

    返回：
      - main_html: 主标签页（积分榜 / 赛程资料统计）渲染后的完整 HTML，已合并所有轮次
      - tab_htmls: dict，键为 showHtml 参数 2~11，值为对应标签页完整 HTML
      - main_png: 主标签页首屏截图（PNG 字节），用于和复刻结果做同源视觉对比
    """
    browser = get_thread_browser()
    context, page = _new_page(browser)
    try:
        def _merge_all_rounds(page):
            """点击所有轮次并把赛程行合并到 #Table3 tbody。"""
            seen_ids: set[str] = set()
            all_rows: list[str] = []

            def _collect_current_rows():
                return page.evaluate(
                    """() => {
                        const tbody = document.querySelector('#Table3 tbody');
                        if (!tbody) return [];
                        return Array.from(tbody.querySelectorAll('tr[id]')).map(r => ({
                            id: r.id,
                            html: r.outerHTML
                        }));
                    }"""
                )

            for row in _collect_current_rows():
                rid = str(row.get("id", "")).strip()
                html = row.get("html", "")
                if rid and html and rid not in seen_ids:
                    seen_ids.add(rid)
                    all_rows.append(html)

            round_cells = page.locator('td[onclick*="changeRound"]').all()
            for cell in round_cells:
                rnd_text = cell.text_content().strip()
                if not rnd_text.isdigit():
                    continue
                try:
                    cell.click()
                    page.wait_for_timeout(1200)
                except Exception:
                    continue
                for row in _collect_current_rows():
                    rid = str(row.get("id", "")).strip()
                    html = row.get("html", "")
                    if rid and html and rid not in seen_ids:
                        seen_ids.add(rid)
                        all_rows.append(html)

            if all_rows:
                combined = "".join(all_rows)
                page.evaluate(
                    """(combined) => {
                        const tbody = document.querySelector('#Table3 tbody');
                        if (tbody) tbody.innerHTML = combined;
                    }""",
                    combined,
                )
                page.wait_for_timeout(500)

        # 主标签页：打开页面、合并所有轮次、保持默认 showHtml(1) 状态
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(wait_ms)
        _merge_all_rounds(page)
        main_html = page.content()
        main_png = page.screenshot(full_page=False, type="png")

        # 判断是否存在 showHtml 标签导航；不存在则直接返回
        has_showhtml = "showHtml(" in main_html
        tab_htmls: dict[int, str] = {}
        if not has_showhtml:
            return main_html, tab_htmls, main_png

        # 在同一页内依次切换 showHtml 标签并捕获 DOM，避免重复打开页面
        for t in range(2, 12):
            try:
                page.evaluate(f"showHtml({t})")
                page.wait_for_timeout(1200)
                tab_htmls[t] = page.content()
                # 切回默认标签，减少后续标签依赖
                page.evaluate("showHtml(1)")
                page.wait_for_timeout(300)
            except Exception:
                continue

        return main_html, tab_htmls, main_png
    finally:
        context.close()
