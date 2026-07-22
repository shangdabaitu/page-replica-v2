#!/usr/bin/env python3
"""复刻引擎：按日期递归复刻页面并进行视觉对比"""
import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from bs4 import BeautifulSoup

import config
from core.fetcher import fetch_url, normalize_url, decode_html
from core.inliner import inline_page
from core.simplifier import simplify_html
from core.watermark import inject_watermark
from core.extractor import (
    extract_schedule_ids,
    extract_match_data,
    extract_links,
    get_detail_urls,
)
from storage import data_store
from compare import visual


# 默认最大递归层级：1 列表页 -> 2 详情页 -> 3 子页面
DEFAULT_MAX_LEVEL = 3


def _url_key(url: str) -> str:
    """生成 URL 的唯一键。"""
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def _url_to_relative_path(url: str) -> str:
    """把 URL 映射成本地相对文件路径。"""
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    query = parsed.query

    # 列表页（必须有日期参数，避免空参数覆盖首页）
    if parsed.path.lower() == "/buy/jingcai.aspx" and "typeid=101" in query.lower():
        qs = parse_qs(query)
        if qs.get("date", [""])[0].strip():
            return "index.html"
        return "JingCai/empty_date.html"

    # 详情页：按已知模式分类目录
    if path.endswith(".htm") or path.endswith(".html"):
        parts = Path(path).parts
        if len(parts) > 1:
            return str(Path(*parts))
        return path or f"page_{_url_key(url)[:8]}.html"

    if path.endswith(".aspx"):
        # 例如 AsianOdds_n.aspx?id=123 -> asian/123.html
        qs = parse_qs(query)
        if "id" in qs:
            return f"{Path(path).stem}/{qs['id'][0]}.html"
        return f"{Path(path).stem}/{_url_key(url)[:8]}.html"

    # 兜底：按 URL key 分目录，避免文件名过长
    return f"pages/{_url_key(url)[:16]}/index.html"


def _patch_analysis_opener(html: str) -> str:
    """把 openAnalysisPage 函数中的远端分析页地址替换成本地相对路径。"""
    # 只替换函数体中的 window.open(...analysis...)
    pattern = re.compile(
        r'(function\s+openAnalysisPage\s*\([^)]*\)\s*\{[^}]*?)'
        r'window\.open\s*\(\s*["\']//zq\.titan007\.com/analysis/["\']\s*\+\s*scheduleID\s*\+\s*suffix\s*\)\s*;',
        re.I | re.DOTALL,
    )
    html = pattern.sub(r'\1window.open("./analysis/" + scheduleID + ".htm");', html)
    return html


def _rewrite_links(html: str, base_url: str, source_path: Path, url_map: dict[str, str]) -> str:
    """把已复刻的链接替换成相对于当前文件的本地路径。"""
    soup = BeautifulSoup(html, "lxml")
    attrs = ["href", "src", "action"]
    source_dir = source_path.parent
    for tag in soup.find_all():
        for attr in attrs:
            val = tag.get(attr)
            if not val:
                continue
            abs_url = normalize_url(val, base_url)
            if not abs_url:
                continue
            if abs_url in url_map:
                target_rel = url_map[abs_url]
                target_path = config.OUTPUT_DIR / target_rel
                try:
                    rel = os.path.relpath(target_path, source_dir)
                except Exception:
                    rel = target_rel
                tag[attr] = rel
    return str(soup)


def _process_single_page(
    url: str,
    date: str,
    level: int,
    base_dir: Path,
    url_map: dict[str, str],
) -> dict:
    """抓取、内联、简体化、水印、保存并视觉对比一个页面。"""
    rel_path = _url_to_relative_path(url)
    output_path = base_dir / rel_path
    output_path.parent.mkdir(parents=True, exist_ok=True)

    event_base = {
        "url": url,
        "level": level,
        "rel_path": rel_path,
    }

    last_error = None
    best_html = None
    best_diff = None

    for attempt in range(config.MAX_RETRIES + 1):
        try:
            data, ct = fetch_url(url, timeout=config.REQUEST_TIMEOUT)
            if data is None:
                raise RuntimeError(f"第 {attempt + 1} 次抓取失败")
            raw_html = decode_html(data, ct)
            inlined = inline_page(raw_html, url)
            simplified = simplify_html(inlined)
            marked = inject_watermark(simplified)
            patched = _patch_analysis_opener(marked)
            final_html = _rewrite_links(patched, url, output_path, url_map)
            best_html = final_html

            output_path.write_text(final_html, encoding="utf-8")

            # 视觉对比
            compare_result = visual.compare_pages(
                url,
                output_path,
                output_dir=base_dir / "diff",
            )
            best_diff = compare_result

            # 视觉对比被跳过（如浏览器未安装）也视为成功
            if compare_result.get("status") == "skipped" or compare_result["diff_ratio"] is None:
                return {
                    **event_base,
                    "status": "ok",
                    "attempt": attempt + 1,
                    "diff_ratio": None,
                    "message": compare_result["message"],
                    "output_path": str(output_path.relative_to(config.OUTPUT_DIR)),
                }

            if compare_result["diff_ratio"] <= config.DIFF_THRESHOLD_IGNORE:
                return {
                    **event_base,
                    "status": "ok",
                    "attempt": attempt + 1,
                    "diff_ratio": compare_result["diff_ratio"],
                    "message": compare_result["message"],
                    "output_path": str(output_path.relative_to(config.OUTPUT_DIR)),
                }

            # 差异超过阈值但还有重试次数
            if attempt < config.MAX_RETRIES:
                time.sleep(1)
                continue

            # 重试耗尽
            final_status = "needs_fix" if compare_result["diff_ratio"] > config.DIFF_THRESHOLD_RETRY else "ok_with_diff"
            return {
                **event_base,
                "status": final_status,
                "attempt": attempt + 1,
                "diff_ratio": compare_result["diff_ratio"],
                "message": compare_result["message"],
                "output_path": str(output_path.relative_to(config.OUTPUT_DIR)),
            }

        except Exception as e:
            last_error = str(e)
            if attempt < config.MAX_RETRIES:
                time.sleep(1)
            else:
                break

    # 全部失败
    return {
        **event_base,
        "status": "error",
        "attempt": config.MAX_RETRIES + 1,
        "message": f"复刻失败: {last_error}",
        "output_path": None,
    }


def replicate_date(date: str, max_level: int | None = None):
    """
    复刻某一日期的页面。
    这是一个生成器，每次 yield 一个进度事件字典。
    """
    if max_level is None:
        max_level = DEFAULT_MAX_LEVEL

    base_dir = config.OUTPUT_DIR / date
    base_dir.mkdir(parents=True, exist_ok=True)

    start_time = time.time()
    url_map: dict[str, str] = {}  # url -> relative path (based on OUTPUT_DIR)
    results: list[dict] = []
    visited: set[str] = set()      # 已处理完成的 URL
    queued: set[str] = set()       # 已在队列中的 URL
    report = {
        "date": date,
        "max_level": max_level,
        "pages_total": 0,
        "pages_ok": 0,
        "pages_retry": 0,
        "pages_fix": 0,
        "pages_error": 0,
        "details": [],
    }

    yield {"type": "start", "date": date, "max_level": max_level}

    list_url = config.SOURCE_URL_TEMPLATE.format(date=date)
    queue = [(list_url, 1, "list")]
    queued.add(list_url)

    # 先加入由 scheduleID 构造的详情页（level=2）
    try:
        list_data, list_ct = fetch_url(list_url)
        if list_data is not None:
            list_html = decode_html(list_data, list_ct)
            matches = extract_match_data(list_html, list_url)
            data_store.save_matches(date, matches)
            sids = extract_schedule_ids(list_html, list_url)
            for sid in sids:
                for detail in get_detail_urls(sid):
                    if detail["url"] not in queued:
                        queue.append((detail["url"], detail["level"], detail["type"]))
                        queued.add(detail["url"])
    except Exception as e:
        yield {"type": "warning", "message": f"解析列表页失败: {e}"}

    while queue:
        url, level, page_type = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        yield {"type": "progress", "url": url, "level": level, "status": "processing"}

        result = _process_single_page(url, date, level, base_dir, url_map)
        results.append(result)
        report["pages_total"] += 1

        if result.get("output_path"):
            url_map[url] = result["output_path"]
            data_store.append_page(date, {
                "url": url,
                "level": level,
                "type": page_type,
                "rel_path": result["output_path"],
                "status": result["status"],
                "diff_ratio": result.get("diff_ratio"),
                "attempt": result.get("attempt", 1),
                "message": result.get("message"),
            })

        if result["status"] == "ok":
            report["pages_ok"] += 1
        elif result["status"] == "ok_with_diff":
            report["pages_retry"] += 1
        elif result["status"] == "needs_fix":
            report["pages_fix"] += 1
        elif result["status"] == "error":
            report["pages_error"] += 1

        report["details"].append(result)
        yield {"type": "page_done", **result}

        # 继续提取下一层链接（仅当处理成功时）
        if result["status"] not in ("error",) and level < max_level:
            try:
                saved_path = base_dir / result["rel_path"]
                html = saved_path.read_text(encoding="utf-8")
                links = extract_links(html, url, level, max_level)
                for link in links:
                    if link["url"] not in queued and link["url"] not in visited:
                        queued.add(link["url"])
                        queue.append((link["url"], link["level"], link.get("type", "link")))
            except Exception as e:
                yield {"type": "warning", "url": url, "message": f"提取子链接失败: {e}"}

    # 最终统一重写所有已保存页面的内链，确保链接在本地可跳转
    yield {"type": "progress", "url": "__rewrite_links__", "level": 0, "status": "rewriting"}
    for src_url, src_rel in url_map.items():
        try:
            src_path = config.OUTPUT_DIR / src_rel
            if not src_path.exists():
                continue
            html = src_path.read_text(encoding="utf-8")
            html = _rewrite_links(html, src_url, src_path, url_map)
            src_path.write_text(html, encoding="utf-8")
        except Exception as e:
            yield {"type": "warning", "url": src_url, "message": f"链接重写失败: {e}"}

    report["elapsed_seconds"] = round(time.time() - start_time, 2)
    data_store.save_report(date, report)

    # 最终输出：列表页相对路径
    list_rel = url_map.get(list_url, f"{date}/index.html")
    yield {"type": "finish", "date": date, "list_page": list_rel, "report": report}


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else "2026-7-21"
    for ev in replicate_date(d):
        print(json.dumps(ev, ensure_ascii=False))
