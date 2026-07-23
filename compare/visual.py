#!/usr/bin/env python3
"""视觉对比模块"""
import io
import math
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageChops

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


def _launch_browser(p):
    """启动 Chromium，优先使用系统已安装的 Chrome/Chromium。"""
    import shutil
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        exe = shutil.which(name)
        if exe:
            try:
                return p.chromium.launch(
                    executable_path=exe,
                    args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
                )
            except Exception as e:
                print(f"[WARN] 用 {name} 启动 Chromium 失败: {e}")
    print("[WARN] 未找到可用的系统 Chrome/Chromium")
    return None


def screenshot_page(url: str, width: int = 1440, height: int = 900) -> Image.Image | None:
    """对指定 URL 进行整页截图并返回 PIL Image。"""
    if not _playwright_ok():
        print("[WARN] Playwright 不可用，跳过截图")
        return None

    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    try:
        with sync_playwright() as p:
            browser = _launch_browser(p)
            if not browser:
                return None
            context = browser.new_context(
                viewport={"width": width, "height": height},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.set_extra_http_headers({
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
            except PWTimeout:
                # networkidle 超时仍继续截图
                pass
            # 等待页面主体渲染
            try:
                page.wait_for_selector("body", timeout=10000)
            except Exception:
                pass
            # 滚动到底部触发懒加载
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)
            png_bytes = page.screenshot(full_page=True, type="png")
            browser.close()
            return Image.open(io.BytesIO(png_bytes))
    except Exception as e:
        print(f"[WARN] 截图 {url} 失败: {e}")
        return None


def compute_diff(source_img: Image.Image, replica_img: Image.Image) -> Tuple[float, Image.Image | None]:
    """
    计算两张图片的差异比例，返回 (diff_ratio, diff_image)。
    diff_ratio 范围 [0, 1]，0 表示完全一致。
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

    diff = ImageChops.difference(a, b)
    # 转成灰度并统计非零像素
    gray = diff.convert("L")
    pixels = list(gray.getdata())
    total = len(pixels)
    if total == 0:
        return 0.0, None
    different = sum(1 for v in pixels if v > 10)
    ratio = different / total

    # 生成高亮差异图
    highlight = None
    if ratio > config.DIFF_THRESHOLD_IGNORE:
        highlight = ImageChops.multiply(diff, diff)
    return ratio, highlight


def compare_pages(source_url: str, replica_path: Path, output_dir: Path | None = None) -> dict:
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

    replica_img = screenshot_page(f"file://{replica_path.resolve()}")
    if replica_img is None:
        return {
            "source_url": source_url,
            "replica_path": str(replica_path),
            "diff_ratio": None,
            "status": "skipped",
            "message": "无法对复刻结果截图，跳过视觉对比",
        }
    ratio, diff_img = compute_diff(source_img, replica_img)

    diff_path = None
    if diff_img and output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        diff_path = output_dir / f"diff_{_safe_name(source_url)}.png"
        diff_img.save(diff_path)

    status = "ok"
    message = "差异在可忽略范围内"
    if ratio > config.DIFF_THRESHOLD_IGNORE:
        status = "needs_retry"
        message = f"差异 {ratio:.2%}，超过 {config.DIFF_THRESHOLD_IGNORE:.0%} 阈值，需要重试"
    if ratio > config.DIFF_THRESHOLD_RETRY:
        status = "needs_fix"
        message = f"差异 {ratio:.2%}，超过 {config.DIFF_THRESHOLD_RETRY:.0%} 阈值，需要人工修复"

    return {
        "source_url": source_url,
        "replica_path": str(replica_path),
        "diff_ratio": round(ratio, 6),
        "status": status,
        "message": message,
        "diff_image": str(diff_path) if diff_path else None,
    }


def _safe_name(url: str) -> str:
    """把 URL 转成适合文件名的字符串。"""
    import re
    s = re.sub(r"[^\w\-]+", "_", url)
    return s[:120]
