#!/usr/bin/env python3
"""页面资源内联模块"""
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

from core.fetcher import fetch_resource, normalize_url, decode_html, data_url
import config


def _looks_like_css(text: str) -> bool:
    """判断解码后的内容是否像合法 CSS（排除加密/二进制响应）。"""
    if not text:
        return False
    # 至少包含一些 CSS 关键符号
    if "{" not in text or "}" not in text:
        return False
    # 可打印 ASCII（含常见空白、标点、中文）占比应较高
    printable = sum(1 for c in text if c.isprintable() or c in " \t\n\r")
    if printable / max(len(text), 1) < 0.85:
        return False
    return True


def _collect_css_urls(css: str, css_url: str) -> list[tuple[str, str]]:
    """提取 CSS 中所有 url(...) 的原始值与绝对 URL，供并发下载。

    跳过字体文件（woff/ttf/eot 等）——它们体积大且对首屏视觉影响有限，
    保持外链可显著减少内联耗时与输出体积。
    """
    urls: list[tuple[str, str]] = []
    seen: set[str] = set()
    font_extensions = (".woff", ".woff2", ".ttf", ".otf", ".eot")
    for m in re.finditer(r"url\(([^)]+)\)", css):
        raw = m.group(1).strip("\"'\t ")
        abs_url = normalize_url(raw, css_url)
        if not abs_url:
            continue
        # 跳过已内联的 data URL，避免重复处理
        if abs_url.startswith("data:"):
            continue
        # 跳过字体文件
        lower = abs_url.lower()
        if any(lower.endswith(ext) for ext in font_extensions):
            continue
        if abs_url not in seen:
            seen.add(abs_url)
            urls.append((raw, abs_url))
    return urls


def _inline_css_urls(css: str, css_url: str, cancel_event=None) -> str:
    """把 CSS 中的 url(...) 相对路径转成绝对路径或 data URL（并发下载）。"""
    if cancel_event is not None and cancel_event.is_set():
        return css

    url_entries = _collect_css_urls(css, css_url)
    if not url_entries:
        return css

    # 并发下载所有 CSS 中引用的资源
    results: dict[str, tuple[bytes | None, str | None]] = {}
    with ThreadPoolExecutor(max_workers=config.CONCURRENCY) as executor:
        future_to_url = {
            executor.submit(fetch_resource, abs_url): abs_url
            for _, abs_url in url_entries
        }
        for future in as_completed(future_to_url):
            if cancel_event is not None and cancel_event.is_set():
                # 取消时不再等待剩余任务，直接返回原 CSS
                return css
            abs_url = future_to_url[future]
            try:
                results[abs_url] = future.result()
            except Exception as e:
                print(f"[WARN] 下载 CSS 资源失败: {abs_url} -> {e}")
                results[abs_url] = (None, None)

    # 按原始 CSS 中的出现顺序替换
    def repl(match):
        if cancel_event is not None and cancel_event.is_set():
            return match.group(0)
        raw = match.group(1).strip("\"'\t ")
        abs_url = normalize_url(raw, css_url)
        if not abs_url:
            return match.group(0)
        if abs_url.startswith("data:"):
            return match.group(0)

        data, ct = results.get(abs_url, (None, None))
        if data is None:
            return f'url("{abs_url}")'
        if len(data) > config.MAX_RESOURCE_BYTES:
            return f'url("{abs_url}")'
        du = data_url(data, ct)
        return f'url("{du}")' if du else match.group(0)

    return re.sub(r"url\(([^)]+)\)", repl, css)


def inline_page(html: str, base_url: str, cancel_event=None) -> str:
    """把页面中的外部 CSS/图片资源内联；脚本不内联，后续由 freeze 阶段删除。

    对疑似加密或二进制的 CSS 资源保持原 <link> 引用，避免内联垃圾数据破坏渲染。
    支持 cancel_event，在资源下载前后检查以便及时响应停止请求。
    并发下载页面级资源（CSS、图片），缩短内联阶段耗时。
    """
    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    soup = BeautifulSoup(html, "html.parser")

    # 1. 先并发收集并下载所有 <link rel="stylesheet"> 资源
    link_tags = []
    for tag in soup.find_all("link", rel="stylesheet", href=True):
        if _cancelled():
            break
        href = normalize_url(tag["href"], base_url)
        if not href:
            continue
        link_tags.append((tag, href))

    link_results: dict[str, tuple[bytes | None, str | None]] = {}
    if link_tags:
        with ThreadPoolExecutor(max_workers=config.CONCURRENCY) as executor:
            future_to_url = {
                executor.submit(fetch_resource, href): href
                for _, href in link_tags
            }
            for future in as_completed(future_to_url):
                if _cancelled():
                    break
                href = future_to_url[future]
                try:
                    link_results[href] = future.result()
                except Exception as e:
                    print(f"[WARN] 下载 CSS 失败: {href} -> {e}")
                    link_results[href] = (None, None)

    # 2. 处理 CSS 内联与 CSS url(...)
    for tag, href in link_tags:
        if _cancelled():
            break
        data, ct = link_results.get(href, (None, None))
        if data is None:
            continue
        css = decode_html(data, ct)
        # 跳过加密/二进制 CSS，保持外链让浏览器自行获取解密
        if not _looks_like_css(css):
            tag["href"] = href
            continue
        # 处理 CSS 中的相对 url(...)
        css = _inline_css_urls(css, href, cancel_event=cancel_event)
        style_tag = soup.new_tag("style")
        style_tag.string = css
        tag.replace_with(style_tag)

    # 3. 处理 HTML 内 <style> 标签中的 url(...)
    for tag in soup.find_all("style"):
        if tag.string:
            tag.string = _inline_css_urls(tag.string, base_url, cancel_event=cancel_event)

    # 4. 处理元素 style 属性中的 url(...)
    for tag in soup.find_all(style=True):
        tag["style"] = _inline_css_urls(tag["style"], base_url, cancel_event=cancel_event)

    # 5. 脚本不再内联，避免 </script> 截断导致脚本源码泄漏到正文。

    # 6. 并发收集并下载 <img src="...">
    img_tags = []
    for tag in soup.find_all("img", src=True):
        if _cancelled():
            break
        src = normalize_url(tag["src"], base_url)
        if not src:
            continue
        img_tags.append((tag, src))

    img_results: dict[str, tuple[bytes | None, str | None]] = {}
    if img_tags:
        with ThreadPoolExecutor(max_workers=config.CONCURRENCY) as executor:
            future_to_url = {
                executor.submit(fetch_resource, src): src
                for _, src in img_tags
            }
            for future in as_completed(future_to_url):
                if _cancelled():
                    break
                src = future_to_url[future]
                try:
                    img_results[src] = future.result()
                except Exception as e:
                    print(f"[WARN] 下载图片失败: {src} -> {e}")
                    img_results[src] = (None, None)

    for tag, src in img_tags:
        if _cancelled():
            break
        data, ct = img_results.get(src, (None, None))
        if data is None:
            continue
        if len(data) > config.MAX_RESOURCE_BYTES:
            tag["src"] = src
            continue
        du = data_url(data, ct)
        if du:
            tag["src"] = du

    # 7. 并发收集并下载其他使用 src 的标签（iframe 除外）
    other_tags = []
    for tag in soup.find_all(src=True):
        if _cancelled():
            break
        if tag.name in ("script", "img", "iframe"):
            continue
        src = normalize_url(tag["src"], base_url)
        if not src:
            continue
        other_tags.append((tag, src))

    other_results: dict[str, tuple[bytes | None, str | None]] = {}
    if other_tags:
        with ThreadPoolExecutor(max_workers=config.CONCURRENCY) as executor:
            future_to_url = {
                executor.submit(fetch_resource, src): src
                for _, src in other_tags
            }
            for future in as_completed(future_to_url):
                if _cancelled():
                    break
                src = future_to_url[future]
                try:
                    other_results[src] = future.result()
                except Exception as e:
                    print(f"[WARN] 下载资源失败: {src} -> {e}")
                    other_results[src] = (None, None)

    for tag, src in other_tags:
        if _cancelled():
            break
        data, ct = other_results.get(src, (None, None))
        if data is None:
            continue
        du = data_url(data, ct)
        if du:
            tag["src"] = du

    return str(soup)
