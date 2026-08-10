#!/usr/bin/env python3
"""重新对视觉对比失败的页面执行视觉对比。

读取 meta.json，找出 diff_ratio 为 null 的页面，
重新渲染源页面并截图，然后与复刻页面做视觉对比。
更新 meta.json 中的对比结果。
"""
import io
import json
import re
import sys
import time
from pathlib import Path

from PIL import Image

import config
from core.fetcher import fetch_url, decode_html
from core.renderer import render_and_capture, get_thread_browser, close_thread_browser
from compare import visual
from storage import data_store


def _is_comparable(url: str) -> bool:
    """判断 URL 是否适合重新视觉对比（排除 live detail 标签页等已单独处理的页面）。"""
    # 排除 live detail 的标签页（由 live_tabs 模块单独处理）
    if "live.titan007.com" in url:
        return False
    # 排除 corner 页面（通常无内容或需要特殊处理）
    if "Corner.aspx" in url:
        return False
    return True


def recompare_page(url: str, rel_path: str, date: str) -> dict | None:
    """对单个页面重新执行视觉对比，返回新的对比结果或 None。"""
    # rel_path 已包含日期前缀（如 2026-07-21/team/TeamNews/247.html），
    # 因此直接拼接 OUTPUT_DIR，不要再加上 date
    replica_path = config.OUTPUT_DIR / rel_path
    if not replica_path.exists():
        replica_path = config.BASE_DIR / "docs" / rel_path
    if not replica_path.exists():
        print(f"  [SKIP] 复刻文件不存在: {rel_path}")
        return None

    # 找到对比文件路径
    compare_path = replica_path.with_suffix(".compare.html")

    try:
        # 渲染源页面并截图
        print(f"  [RENDER] 渲染源页面: {url}")
        rendered_html, source_png = render_and_capture(url, wait_ms=3000)
        source_img = Image.open(io.BytesIO(source_png))

        # 执行视觉对比
        compare_result = visual.compare_with_source_image(
            source_img,
            url,
            compare_path if compare_path.exists() else replica_path,
            output_dir=config.OUTPUT_DIR / date / "diff",
            source_html=rendered_html,
        )

        diff_ratio = compare_result.get("diff_ratio")
        status = compare_result.get("status")
        message = compare_result.get("message")

        print(f"  [RESULT] diff={diff_ratio}, status={status}")
        if diff_ratio is not None:
            print(f"          message: {message}")

        return {
            "diff_ratio": diff_ratio,
            "status": status,
            "message": message,
            "dom_diff_ratio": compare_result.get("dom_diff_ratio"),
            "dom_status": compare_result.get("dom_status"),
            "dom_source_count": compare_result.get("dom_source_count"),
            "dom_replica_count": compare_result.get("dom_replica_count"),
            "ext_resource_count": compare_result.get("external_resources", {}).get("external_count", 0),
            "ext_status": compare_result.get("ext_status"),
            "console_error_count": compare_result.get("console_error_count", 0),
            "console_status": compare_result.get("console_status"),
        }

    except Exception as e:
        print(f"  [ERROR] 重新对比失败: {e}")
        return None


def recompare_live_tabs(date: str) -> int:
    """对 live detail 标签页重新执行视觉对比。

    返回更新的页面数。
    """
    docs_dir = config.BASE_DIR / "docs" / date
    live_dir = docs_dir / "live" / "detail"
    if not live_dir.exists():
        live_dir = config.OUTPUT_DIR / date / "live" / "detail"
    if not live_dir.exists():
        print("[SKIP] live detail 目录不存在")
        return 0

    # 收集所有主页文件
    main_files = {}
    for f in live_dir.glob("*cn.htm"):
        if "_event_detail" in f.name or "_tech_same" in f.name or "_jsq_50" in f.name:
            continue
        if "_players" in f.name or "_text" in f.name or "_animation" in f.name or "_hd" in f.name:
            continue
        m = re.match(r"(\d+)cn\.htm$", f.name)
        if m:
            main_files[m.group(1)] = f

    updated = 0
    for match_id, main_file in sorted(main_files.items()):
        url = f"https://live.titan007.com/detail/{match_id}cn.htm"
        print(f"\n[LIVE] {match_id} - 渲染源页面...")

        try:
            rendered_html, source_png = render_and_capture(url, wait_ms=4000)
            source_img = Image.open(io.BytesIO(source_png))

            # 对主页面做视觉对比
            compare_path = main_file.with_suffix(".compare.html")
            compare_result = visual.compare_with_source_image(
                source_img,
                url,
                compare_path if compare_path.exists() else main_file,
                output_dir=live_dir.parent / "diff",
                source_html=rendered_html,
            )
            diff_ratio = compare_result.get("diff_ratio")
            print(f"  [MAIN] diff={diff_ratio}")

            if diff_ratio is not None:
                updated += 1

            # 对各标签页做视觉对比
            tab_suffixes = ["_event_detail", "_tech_same", "_jsq_50", "_players", "_text", "_animation", "_hd"]
            for suffix in tab_suffixes:
                tab_file = live_dir / f"{match_id}cn{suffix}.htm"
                if not tab_file.exists():
                    continue

                tab_compare_path = tab_file.with_suffix(".compare.html")
                try:
                    tab_result = visual.compare_with_source_image(
                        source_img,
                        f"{url}#{suffix}",
                        tab_compare_path if tab_compare_path.exists() else tab_file,
                        output_dir=live_dir.parent / "diff",
                        source_html=rendered_html,
                    )
                    tab_diff = tab_result.get("diff_ratio")
                    if tab_diff is not None:
                        updated += 1
                        print(f"  [TAB{suffix}] diff={tab_diff}")
                except Exception as e:
                    print(f"  [TAB{suffix}] 对比失败: {e}")

        except Exception as e:
            print(f"  [ERROR] {match_id}: {e}")

    return updated


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else "2026-07-21"
    print(f"=== 重新视觉对比 - {date} ===\n")

    # 优先从 output 目录加载，回退到 docs 目录
    meta = data_store.load_meta(date)
    if not meta.get("pages"):
        docs_meta = config.BASE_DIR / "docs" / date / "meta.json"
        if docs_meta.exists():
            meta = json.loads(docs_meta.read_text(encoding="utf-8"))
            print(f"[INFO] 从 docs 目录加载 meta.json: {docs_meta}")
            # 同步到 output 目录供后续使用
            output_meta = config.OUTPUT_DIR / date / "meta.json"
            output_meta.parent.mkdir(parents=True, exist_ok=True)
            output_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            print(f"[ERROR] 无法加载 meta.json: {date}")
            sys.exit(1)

    pages = meta.get("pages", [])
    failed_pages = [
        p for p in pages
        if p.get("diff_ratio") is None
        and p.get("rel_path")
        and _is_comparable(p.get("url", ""))
    ]

    print(f"总页面数: {len(pages)}")
    print(f"需要重新对比的页面: {len(failed_pages)}\n")

    # 重新对比失败的页面
    updated_count = 0
    for i, page in enumerate(failed_pages):
        url = page.get("url", "")
        rel_path = page.get("rel_path", "")
        print(f"[{i+1}/{len(failed_pages)}] {url}")

        result = recompare_page(url, rel_path, date)
        if result and result.get("diff_ratio") is not None:
            # 更新 meta.json 中的页面记录
            page["diff_ratio"] = result["diff_ratio"]
            page["status"] = result["status"]
            page["message"] = result["message"]
            page["dom_diff_ratio"] = result.get("dom_diff_ratio")
            page["dom_status"] = result.get("dom_status")
            page["dom_source_count"] = result.get("dom_source_count")
            page["dom_replica_count"] = result.get("dom_replica_count")
            page["ext_resource_count"] = result.get("ext_resource_count", 0)
            page["ext_status"] = result.get("ext_status")
            page["console_error_count"] = result.get("console_error_count", 0)
            page["console_status"] = result.get("console_status")
            updated_count += 1

        # 避免过快请求
        time.sleep(0.5)

    print(f"\n=== 页面对比完成: {updated_count}/{len(failed_pages)} 更新成功 ===\n")

    # 重新对比 live detail 标签页
    print("=== 重新对比 live detail 标签页 ===\n")
    live_updated = recompare_live_tabs(date)
    print(f"\n=== live detail 标签页对比完成: {live_updated} 个更新 ===\n")

    # 保存更新后的 meta.json
    meta_path = config.OUTPUT_DIR / date / "meta.json"
    if not meta_path.parent.exists():
        meta_path = config.BASE_DIR / "docs" / date / "meta.json"

    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"meta.json 已更新: {meta_path}")

    # 同步到 docs
    docs_meta = config.BASE_DIR / "docs" / date / "meta.json"
    if docs_meta != meta_path:
        docs_meta.parent.mkdir(parents=True, exist_ok=True)
        docs_meta.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"docs/meta.json 已同步: {docs_meta}")

    # 释放浏览器
    close_thread_browser()

    print(f"\n=== 全部完成 ===")
    print(f"页面重新对比: {updated_count}/{len(failed_pages)}")
    print(f"标签页重新对比: {live_updated}")


if __name__ == "__main__":
    main()
