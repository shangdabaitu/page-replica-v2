#!/usr/bin/env python3
"""视觉对比模块"""
import io
import math
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageChops, ImageFilter

import config


# 尝试导入 Playwright，未安装时提供友好的降级
_playwright_available = None


def _playwright_ok() -> bool:
    global _playwright_available
    if _playwright_available is None:
        try:
            from playwright.sync_api import sync_playwright
            _playwright_available = True
        except Exception:
            _playwright_available = False
    return _playwright_available


# ---- DOM 节点计数 ----

def count_dom_nodes(html: str) -> int:
    """统计 HTML 中的 DOM 元素节点数。"""
    if not html:
        return 0
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        return len(soup.find_all())
    except Exception:
        return 0


# ---- 外部资源残留检查 ----

def check_external_resources(html: str) -> dict:
    """检查复刻页面中是否仍有未内联的外部资源 URL。

    返回:
      - external_count: 外部资源总数
      - external_urls:  外部 URL 列表（最多 20 条）
      - css_count:      外部 CSS 数
      - js_count:       外部 JS 数
      - img_count:      外部图片数
      - iframe_count:   外部 iframe 数
    """
    result: dict = {
        "external_count": 0,
        "external_urls": [],
        "css_count": 0,
        "js_count": 0,
        "img_count": 0,
        "iframe_count": 0,
    }
    if not html:
        return result
    try:
        from bs4 import BeautifulSoup
        from urllib.parse import urlparse

        soup = BeautifulSoup(html, "lxml")
        allowed_hosts = {h.lower() for h in config.ALLOWED_HOSTS}
        external_urls: set[str] = set()

        def _is_external(url: str) -> bool:
            """判断 URL 是否指向源站（即应被内联但未内联的外部资源）。"""
            if not url or url.startswith(("data:", "about:", "javascript:", "blob:", "#")):
                return False
            parsed = urlparse(url)
            return parsed.netloc.lower() in allowed_hosts

        for tag in soup.find_all("link", rel="stylesheet", href=True):
            if _is_external(tag["href"]):
                external_urls.add(tag["href"])
                result["css_count"] += 1

        for tag in soup.find_all("script", src=True):
            if _is_external(tag["src"]):
                external_urls.add(tag["src"])
                result["js_count"] += 1

        for tag in soup.find_all("img", src=True):
            if _is_external(tag["src"]):
                external_urls.add(tag["src"])
                result["img_count"] += 1

        for tag in soup.find_all("iframe", src=True):
            if _is_external(tag["src"]):
                external_urls.add(tag["src"])
                result["iframe_count"] += 1

        result["external_urls"] = sorted(external_urls)[:20]
        result["external_count"] = len(external_urls)
    except Exception as e:
        print(f"[WARN] 外部资源检查失败: {e}")
    return result


# ---- 截图 + 控制台错误捕获 ----

def _screenshot_and_capture_console(
    url: str, width: int = 1440, height: int = 900
) -> tuple[Image.Image | None, list[str]]:
    """对指定 URL 截图并同时捕获浏览器控制台错误/警告及 JS 异常。

    返回 (PIL Image | None, console_errors)。
    """
    if not _playwright_ok():
        return None, []

    from playwright.sync_api import TimeoutError as PWTimeout
    from core.renderer import get_thread_browser

    console_messages: list[str] = []

    try:
        browser = get_thread_browser()
        context = browser.new_context(
            viewport={"width": width, "height": height},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        context.set_extra_http_headers({
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        page = context.new_page()

        def _on_console(msg):
            if msg.type in ("error", "warning"):
                console_messages.append(f"[console.{msg.type}] {msg.text}")

        def _on_pageerror(err):
            console_messages.append(f"[pageerror] {err}")

        page.on("console", _on_console)
        page.on("pageerror", _on_pageerror)

        try:
            try:
                page.goto(url, wait_until="networkidle", timeout=15000)
            except PWTimeout:
                pass
            try:
                page.wait_for_selector("body", timeout=10000)
            except Exception:
                pass
            png_bytes = page.screenshot(full_page=False, type="png")
            img = Image.open(io.BytesIO(png_bytes))
            Image.MAX_IMAGE_PIXELS = max(
                Image.MAX_IMAGE_PIXELS, img.width * img.height * 2
            )
            return img, console_messages
        finally:
            context.close()
    except Exception as e:
        print(f"[WARN] 截图+控制台捕获 {url} 失败: {e}")
        return None, console_messages


def screenshot_page(url: str, width: int = 1440, height: int = 900) -> Image.Image | None:
    """对指定 URL 进行截图并返回 PIL Image。只截取可视区域，避免长页面占用过大内存。

    复用当前线程的浏览器实例，避免每次截图都重新启动 Chromium。
    """
    if not _playwright_ok():
        print("[WARN] Playwright 不可用，跳过截图")
        return None

    from playwright.sync_api import TimeoutError as PWTimeout
    from core.renderer import get_thread_browser

    try:
        browser = get_thread_browser()
        context = browser.new_context(
            viewport={"width": width, "height": height},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        context.set_extra_http_headers({
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        })
        page = context.new_page()
        try:
            try:
                # 本地复刻页通常已内联资源，用较短 networkidle 超时避免阻塞
                page.goto(url, wait_until="networkidle", timeout=15000)
            except PWTimeout:
                pass
            # 等待页面主体渲染
            try:
                page.wait_for_selector("body", timeout=10000)
            except Exception:
                pass
            png_bytes = page.screenshot(full_page=False, type="png")
            img = Image.open(io.BytesIO(png_bytes))
            # 避免 PIL 反压缩炸弹警告阻塞流程
            Image.MAX_IMAGE_PIXELS = max(Image.MAX_IMAGE_PIXELS, img.width * img.height * 2)
            return img
        finally:
            context.close()
    except Exception as e:
        print(f"[WARN] 截图 {url} 失败: {e}")
        return None


def compute_diff(source_img: Image.Image, replica_img: Image.Image) -> Tuple[float, Image.Image | None]:
    """
    计算两张图片的差异比例，返回 (diff_ratio, diff_image)。
    diff_ratio 范围 [0, 1]，0 表示完全一致。

    为了降低不同 Chromium 实例在字体抗锯齿、子像素渲染上的微小噪声，
    先对两张图做轻微高斯模糊，再以较高阈值统计真正结构性差异。
    """
    if source_img is None or replica_img is None:
        return 1.0, None

    # 统一尺寸：以较大画布为准，居中放置
    w = max(source_img.width, replica_img.width)
    h = max(source_img.height, replica_img.height)

    def pad(img: Image.Image) -> Image.Image:
        bg = Image.new("RGB", (w, h), (255, 255, 255))
        bg.paste(img.convert("RGB"))
        return bg

    a = pad(source_img)
    b = pad(replica_img)

    # 轻微模糊消除抗锯齿噪声，保留文字/区块级别的真实差异
    a = a.filter(ImageFilter.GaussianBlur(radius=1))
    b = b.filter(ImageFilter.GaussianBlur(radius=1))

    diff = ImageChops.difference(a, b)
    # 转成灰度并统计非零像素
    gray = diff.convert("L")
    pixels = list(gray.getdata())
    total = len(pixels)
    if total == 0:
        return 0.0, None
    # 阈值 20：忽略由字体渲染、压缩、颜色取整带来的微小差异
    different = sum(1 for v in pixels if v > 20)
    ratio = different / total

    # 生成高亮差异图：增强对比度让差异区域更明显
    highlight = None
    if ratio > config.DIFF_THRESHOLD_IGNORE:
        enhanced = ImageChops.multiply(diff, diff)
        highlight = enhanced.point(lambda p: min(255, p * 4))
    return ratio, highlight


def compare_pages(
    source_url: str,
    replica_path: Path,
    output_dir: Path | None = None,
    source_html: str | None = None,
) -> dict:
    """对比数据源页面和复刻页面，返回结果字典。"""
    if not _playwright_ok():
        return {
            "source_url": source_url,
            "replica_path": str(replica_path),
            "diff_ratio": None,
            "status": "skipped",
            "message": "Playwright 未安装，跳过视觉对比",
        }

    if not replica_path.exists():
        return {
            "source_url": source_url,
            "replica_path": str(replica_path),
            "diff_ratio": None,
            "status": "replica_not_found",
            "message": "复刻文件不存在",
        }

    source_img = screenshot_page(source_url)
    if source_img is None:
        return {
            "source_url": source_url,
            "replica_path": str(replica_path),
            "diff_ratio": None,
            "status": "skipped",
            "message": "无法启动浏览器，跳过视觉对比",
        }

    return _compare_with_source_image(
        source_img,
        source_url,
        replica_path,
        output_dir=output_dir,
        source_html=source_html,
    )


def compare_with_source_image(
    source_img: Image.Image,
    source_display_url: str,
    replica_path: Path,
    output_dir: Path | None = None,
    source_html: str | None = None,
) -> dict:
    """用已经准备好的源截图与复刻页面做视觉对比，返回结果字典。"""
    if not _playwright_ok():
        return {
            "source_url": source_display_url,
            "replica_path": str(replica_path),
            "diff_ratio": None,
            "status": "skipped",
            "message": "Playwright 未安装，跳过视觉对比",
        }

    if not replica_path.exists():
        return {
            "source_url": source_display_url,
            "replica_path": str(replica_path),
            "diff_ratio": None,
            "status": "replica_not_found",
            "message": "复刻文件不存在",
        }

    return _compare_with_source_image(
        source_img,
        source_display_url,
        replica_path,
        output_dir=output_dir,
        source_html=source_html,
    )


def _compare_with_source_image(
    source_img: Image.Image,
    source_display_url: str,
    replica_path: Path,
    output_dir: Path | None = None,
    source_html: str | None = None,
) -> dict:
    """内部：用源截图与复刻页面做视觉对比。

    包含四个维度的检查：
      1. 像素级截图差异（diff_ratio）
      2. DOM 节点数对比（dom_diff_ratio）
      3. 外部资源残留检查（external_resources）
      4. 浏览器控制台 JS 错误（console_errors）
    """
    # 截图复刻页面并捕获控制台错误
    replica_img, console_errors = _screenshot_and_capture_console(
        f"file://{replica_path.resolve()}"
    )
    if replica_img is None:
        # 截图失败时仍尝试做不需要浏览器的检查
        replica_html = ""
        try:
            replica_html = replica_path.read_text(encoding="utf-8")
        except Exception:
            pass
        ext_result = check_external_resources(replica_html)
        return {
            "source_url": source_display_url,
            "replica_path": str(replica_path),
            "diff_ratio": None,
            "status": "skipped",
            "message": "无法对复刻结果截图，跳过视觉对比",
            "diff_image": None,
            "dom_source_count": count_dom_nodes(source_html) if source_html else 0,
            "dom_replica_count": count_dom_nodes(replica_html),
            "dom_diff_ratio": None,
            "dom_status": "skipped",
            "external_resources": ext_result,
            "ext_status": "warning" if ext_result["external_count"] > 0 else "ok",
            "console_errors": console_errors[:20],
            "console_error_count": len([e for e in console_errors if "[pageerror]" in e or "[console.error]" in e]),
            "console_status": "skipped",
        }

    # 1. 像素级差异
    ratio, diff_img = compute_diff(source_img, replica_img)

    diff_path = None
    if diff_img and output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        diff_path = output_dir / f"diff_{_safe_name(source_display_url)}.png"
        diff_img.save(diff_path)

    # 读取复刻页面 HTML 用于 DOM 和外部资源检查
    replica_html = ""
    try:
        replica_html = replica_path.read_text(encoding="utf-8")
    except Exception:
        pass

    # 2. DOM 节点数对比
    dom_source_count = count_dom_nodes(source_html) if source_html else 0
    dom_replica_count = count_dom_nodes(replica_html)
    dom_diff_ratio: float | None = None
    dom_status = "skipped"
    dom_message = ""

    if dom_source_count > 0 and dom_replica_count > 0:
        dom_diff_ratio = abs(dom_source_count - dom_replica_count) / max(
            dom_source_count, dom_replica_count
        )
        if dom_diff_ratio <= 0.20:
            dom_status = "ok"
            dom_message = (
                f"DOM 节点数差异 {dom_diff_ratio:.1%}"
                f"（源 {dom_source_count} / 复刻 {dom_replica_count}）"
            )
        elif dom_diff_ratio <= 0.50:
            dom_status = "warning"
            dom_message = (
                f"DOM 节点数差异 {dom_diff_ratio:.1%}"
                f"（源 {dom_source_count} / 复刻 {dom_replica_count}），需检查"
            )
        else:
            dom_status = "error"
            dom_message = (
                f"DOM 节点数差异 {dom_diff_ratio:.1%}"
                f"（源 {dom_source_count} / 复刻 {dom_replica_count}），差异过大"
            )
    elif dom_replica_count > 0:
        dom_status = "ok"
        dom_message = f"复刻 DOM 节点数 {dom_replica_count}（无源 HTML 对比）"

    # 3. 外部资源残留检查
    ext_result = check_external_resources(replica_html)
    ext_status = "ok"
    ext_message = ""
    if ext_result["external_count"] > 0:
        if ext_result["external_count"] <= 3:
            ext_status = "warning"
            ext_message = f"发现 {ext_result['external_count']} 个未内联外部资源"
        else:
            ext_status = "error"
            ext_message = f"发现 {ext_result['external_count']} 个未内联外部资源，需补抓"

    # 4. 控制台错误检查
    console_error_count = len(
        [e for e in console_errors if "[pageerror]" in e or "[console.error]" in e]
    )
    console_warning_count = len(
        [e for e in console_errors if "[console.warning]" in e]
    )
    console_status = "ok"
    console_message = ""
    if console_error_count > 0:
        if console_error_count <= 3:
            console_status = "warning"
            console_message = f"发现 {console_error_count} 个控制台错误"
        else:
            console_status = "error"
            console_message = f"发现 {console_error_count} 个控制台错误，需检查"
    elif console_warning_count > 0:
        console_status = "warning"
        console_message = f"发现 {console_warning_count} 个控制台警告"

    # 综合判断状态（以像素差异为主，其他维度可升级状态）
    status = "ok"
    message = "差异在可忽略范围内"

    if ratio > config.DIFF_THRESHOLD_IGNORE:
        status = "needs_retry"
        message = (
            f"差异 {ratio:.2%}，"
            f"超过 {config.DIFF_THRESHOLD_IGNORE:.0%} 阈值，需要重试"
        )
    if ratio > config.DIFF_THRESHOLD_RETRY:
        status = "needs_fix"
        message = (
            f"差异 {ratio:.2%}，"
            f"超过 {config.DIFF_THRESHOLD_RETRY:.0%} 阈值，需要人工修复"
        )

    # DOM 差异过大时升级状态
    if dom_status == "error" and status == "ok":
        status = "needs_retry"
        message = dom_message

    # 外部资源过多时升级状态
    if ext_status == "error" and status == "ok":
        status = "needs_retry"
        message = ext_message

    # 控制台错误过多时升级状态
    if console_status == "error" and status == "ok":
        status = "needs_retry"
        message = console_message

    # 拼接附加信息到 message
    extra_msgs: list[str] = []
    if dom_status not in ("ok", "skipped"):
        extra_msgs.append(dom_message)
    if ext_status != "ok":
        extra_msgs.append(ext_message)
    if console_status not in ("ok",):
        extra_msgs.append(console_message)
    if extra_msgs:
        message += "；" + "；".join(extra_msgs)

    return {
        "source_url": source_display_url,
        "replica_path": str(replica_path),
        "diff_ratio": round(ratio, 6),
        "status": status,
        "message": message,
        "diff_image": str(diff_path) if diff_path else None,
        "dom_source_count": dom_source_count,
        "dom_replica_count": dom_replica_count,
        "dom_diff_ratio": round(dom_diff_ratio, 4) if dom_diff_ratio is not None else None,
        "dom_status": dom_status,
        "external_resources": ext_result,
        "ext_status": ext_status,
        "console_errors": console_errors[:20],
        "console_error_count": console_error_count,
        "console_warning_count": console_warning_count,
        "console_status": console_status,
    }


def _safe_name(url: str) -> str:
    """把 URL 转成适合文件名的字符串。"""
    import re
    s = re.sub(r"[^\w\-]+", "_", url)
    return s[:120]
