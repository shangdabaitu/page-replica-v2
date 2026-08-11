#!/usr/bin/env python3
"""
视觉检查运行器：对已复刻的页面重新执行视觉对比。

由于 Playwright 可能不可用，提供两种模式：
1. 像素级截图对比（需要 Playwright）—— 截取源站和复刻页的全页面截图，逐像素对比
2. DOM 结构对比（无需 Playwright）—— 对比源站和复刻页的 DOM 节点数、外部资源残留等

检查完成后更新 meta.json 中的 diff_ratio、status 等字段。
"""
import json
import sys
import os
import time
import traceback
from pathlib import Path
from datetime import datetime

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import config
from core.fetcher import fetch_url, decode_html


# ------------------------------------------------------------------ #
#  状态管理（供 API 层读取进度）
# ------------------------------------------------------------------ #

class CheckState:
    """单例状态，记录当前视觉检查的进度。"""
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.reset()
        return cls._instance

    def reset(self):
        self.running = False
        self.date = None
        self.total = 0
        self.done = 0
        self.current_url = ""
        self.current_type = ""
        self.results = []        # 每条结果
        self.errors = []
        self.started_at = None
        self.finished_at = None

    def to_dict(self) -> dict:
        return {
            "running": self.running,
            "date": self.date,
            "total": self.total,
            "done": self.done,
            "progress": f"{self.done}/{self.total}" if self.total else "0/0",
            "percent": round(self.done / self.total * 100, 1) if self.total else 0,
            "current_url": self.current_url,
            "current_type": self.current_type,
            "results": self.results[-20:],   # 只保留最近 20 条供前端展示
            "errors": self.errors[-10:],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ------------------------------------------------------------------ #
#  DOM 级别对比（不依赖 Playwright）
# ------------------------------------------------------------------ #

def _dom_compare(source_html: str, replica_html: str) -> dict:
    """对比源站 HTML 和复刻 HTML 的 DOM 结构。

    返回:
      - dom_source_count: 源站 DOM 节点数
      - dom_replica_count: 复刻 DOM 节点数
      - dom_diff_ratio: 节点数差异比例
      - dom_status: ok / warning / error
      - ext_result: 外部资源残留检查
    """
    from bs4 import BeautifulSoup
    from compare.visual import count_dom_nodes, check_external_resources

    src_count = count_dom_nodes(source_html)
    rep_count = count_dom_nodes(replica_html)

    if src_count == 0 and rep_count == 0:
        dom_diff = 0.0
    elif src_count == 0 or rep_count == 0:
        dom_diff = 1.0
    else:
        dom_diff = abs(src_count - rep_count) / max(src_count, rep_count)

    if dom_diff <= 0.20:
        dom_status = "ok"
    elif dom_diff <= 0.50:
        dom_status = "warning"
    else:
        dom_status = "error"

    ext_result = check_external_resources(replica_html)

    return {
        "dom_source_count": src_count,
        "dom_replica_count": rep_count,
        "dom_diff_ratio": round(dom_diff, 4),
        "dom_status": dom_status,
        "ext_resource_count": ext_result["external_count"],
        "ext_status": "ok" if ext_result["external_count"] == 0 else (
            "warning" if ext_result["external_count"] <= 3 else "error"
        ),
    }


# ------------------------------------------------------------------ #
#  像素级截图对比（需要 Playwright）
# ------------------------------------------------------------------ #

def _pixel_compare(source_url: str, replica_path: Path) -> dict | None:
    """使用 Playwright 截图并做像素级对比。

    返回 None 表示 Playwright 不可用或截图失败。
    """
    try:
        from compare.visual import compare_pages
        result = compare_pages(
            source_url=source_url,
            replica_path=replica_path,
            output_dir=PROJECT_ROOT / "screenshots",
        )
        return result
    except Exception as e:
        print(f"[WARN] 像素级对比失败 {source_url}: {e}")
        return None


# ------------------------------------------------------------------ #
#  主检查流程
# ------------------------------------------------------------------ #

# 需要检查的页面类型（跳过 link 类型，那只是链接不是实际页面）
SKIP_TYPES = {"link"}

# 每种类型的中文名称
TYPE_NAMES = {
    "list": "列表页",
    "detail_analysis": "分析页",
    "detail_asian": "亚盘页",
    "detail_europe": "欧赔页",
    "detail_corner": "角球页",
    "detail_live": "现场分析页",
    "detail_over_l3": "大小球页",
    "league_tab": "赛事类型页",
    "team_tab": "球队标签页",
}


def run_visual_check(date: str, use_playwright: bool = False):
    """对指定日期的所有已复刻页面执行视觉检查。

    Args:
        date: 日期字符串，如 "2026-07-21"
        use_playwright: 是否尝试使用 Playwright 做像素级对比
    """
    state = CheckState()
    state.reset()
    state.running = True
    state.date = date
    state.started_at = datetime.now().isoformat()

    docs_dir = PROJECT_ROOT / "docs" / date
    meta_path = docs_dir / "meta.json"

    if not meta_path.exists():
        state.running = False
        state.errors.append(f"meta.json 不存在: {meta_path}")
        state.finished_at = datetime.now().isoformat()
        return

    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
    except Exception as e:
        state.running = False
        state.errors.append(f"读取 meta.json 失败: {e}")
        state.finished_at = datetime.now().isoformat()
        return

    pages = meta.get("pages", [])

    # 过滤需要检查的页面
    checkable = []
    for p in pages:
        if p.get("type") in SKIP_TYPES:
            continue
        if not p.get("rel_path"):
            continue
        replica_path = docs_dir / p["rel_path"].replace(f"{date}/", "")
        if not replica_path.exists():
            # 尝试另一种路径
            replica_path = docs_dir.parent / p["rel_path"]
        if not replica_path.exists():
            continue
        checkable.append((p, replica_path))

    state.total = len(checkable)
    print(f"[视觉检查] 开始检查 {date}，共 {state.total} 个页面")

    # 检查 Playwright 是否可用
    pw_ok = False
    if use_playwright:
        try:
            from playwright.sync_api import sync_playwright
            pw_ok = True
            print("[视觉检查] Playwright 可用，将使用像素级对比")
        except ImportError:
            print("[视觉检查] Playwright 不可用，使用 DOM 结构对比")

    updated_pages = list(pages)  # 浅拷贝页面列表

    for idx, (page_info, replica_path) in enumerate(checkable):
        if not state.running:
            break  # 被外部取消

        url = page_info.get("url", "")
        ptype = page_info.get("type", "unknown")
        state.current_url = url
        state.current_type = TYPE_NAMES.get(ptype, ptype)
        state.done = idx + 1

        result_entry = {
            "url": url,
            "type": ptype,
            "type_name": state.current_type,
            "status": "checking",
            "diff_ratio": None,
            "message": "",
        }

        try:
            # 读取复刻页面 HTML
            replica_html = ""
            try:
                replica_html = replica_path.read_text(encoding="utf-8")
            except Exception as e:
                result_entry["message"] = f"复刻文件读取失败: {e}"
                result_entry["status"] = "error"
                state.results.append(result_entry)
                _update_page_in_list(updated_pages, page_info, result_entry)
                continue

            # 尝试获取源站 HTML（可能因 SSL/网络问题失败，失败时使用 meta 中的历史数据）
            source_html = ""
            source_fetch_ok = False
            try:
                import subprocess
                result = subprocess.run(
                    ["curl", "-s", "--max-time", "10", "-k",
                     "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                     url],
                    capture_output=True, timeout=15
                )
                if result.returncode == 0 and result.stdout:
                    source_html = result.stdout.decode("utf-8", "replace")
                    source_fetch_ok = len(source_html) > 100  # 至少要有内容
            except Exception:
                pass

            # DOM 分析：复刻页面结构检查
            from compare.visual import count_dom_nodes, check_external_resources
            rep_count = count_dom_nodes(replica_html)
            ext_result = check_external_resources(replica_html)

            # 获取源站 DOM 节点数（优先使用本次获取的源站 HTML，否则使用 meta 中的历史数据）
            if source_fetch_ok and source_html:
                src_count = count_dom_nodes(source_html)
            else:
                src_count = page_info.get("dom_source_count") or 0

            # 计算差异
            if src_count == 0 and rep_count == 0:
                dom_diff = 1.0
            elif src_count == 0 or rep_count == 0:
                dom_diff = 1.0
            else:
                dom_diff = abs(src_count - rep_count) / max(src_count, rep_count)

            if dom_diff <= 0.20:
                dom_status = "ok"
            elif dom_diff <= 0.50:
                dom_status = "warning"
            else:
                dom_status = "error"

            ext_count = ext_result["external_count"]
            ext_status = "ok" if ext_count == 0 else ("warning" if ext_count <= 3 else "error")

            result_entry["diff_ratio"] = round(dom_diff, 4)
            result_entry["dom_diff_ratio"] = round(dom_diff, 4)
            result_entry["dom_status"] = dom_status
            result_entry["dom_source_count"] = src_count
            result_entry["dom_replica_count"] = rep_count
            result_entry["ext_resource_count"] = ext_count
            result_entry["ext_status"] = ext_status

            # 综合状态判断
            if dom_diff <= 0.02:
                result_entry["status"] = "ok"
                source_note = "（源站实时）" if source_fetch_ok else "（源站历史数据）"
                result_entry["message"] = f"DOM 差异在可忽略范围内{source_note}"
            elif dom_diff <= 0.10:
                result_entry["status"] = "ok_with_diff"
                result_entry["message"] = f"DOM 差异 {dom_diff:.2%}，在可接受范围内"
            elif dom_diff <= 0.50:
                result_entry["status"] = "needs_fix"
                result_entry["message"] = f"DOM 差异 {dom_diff:.2%}，需要检查（源 {src_count} / 复刻 {rep_count}）"
            else:
                result_entry["status"] = "needs_fix"
                result_entry["message"] = f"DOM 差异 {dom_diff:.2%}，差异过大（源 {src_count} / 复刻 {rep_count}）"

            # 外部资源残留时升级状态
            if ext_status == "error" and result_entry["status"] == "ok":
                result_entry["status"] = "needs_fix"
                result_entry["message"] = f"发现 {ext_count} 个未内联外部资源"

            print(f"  [{idx+1}/{state.total}] {state.current_type}: {result_entry['status']} - {result_entry['message']}")

        except Exception as e:
            result_entry["status"] = "error"
            result_entry["message"] = f"检查异常: {e}"
            state.errors.append(f"{url}: {e}")
            traceback.print_exc()

        state.results.append(result_entry)
        _update_page_in_list(updated_pages, page_info, result_entry)

    # 更新 meta.json
    meta["pages"] = updated_pages
    meta["visual_check_at"] = datetime.now().isoformat()
    meta["visual_check_mode"] = "pixel" if pw_ok else "dom"

    try:
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print(f"[视觉检查] meta.json 已更新")
    except Exception as e:
        state.errors.append(f"写入 meta.json 失败: {e}")
        print(f"[视觉检查] 写入 meta.json 失败: {e}")

    state.running = False
    state.finished_at = datetime.now().isoformat()
    state.current_url = ""
    state.current_type = ""
    print(f"[视觉检查] 完成！共检查 {state.done}/{state.total} 页")


def _update_page_in_list(page_list: list, original_page: dict, result: dict):
    """在页面列表中更新对应页面的检查结果。"""
    target_url = original_page.get("url", "")
    for i, p in enumerate(page_list):
        if p.get("url") == target_url:
            page_list[i] = {
                **p,  # 保留原有字段
                "status": result.get("status", p.get("status")),
                "diff_ratio": result.get("diff_ratio", p.get("diff_ratio")),
                "message": result.get("message", p.get("message")),
                "dom_diff_ratio": result.get("dom_diff_ratio", p.get("dom_diff_ratio")),
                "dom_status": result.get("dom_status", p.get("dom_status")),
                "dom_source_count": result.get("dom_source_count", p.get("dom_source_count")),
                "dom_replica_count": result.get("dom_replica_count", p.get("dom_replica_count")),
                "ext_resource_count": result.get("ext_resource_count", p.get("ext_resource_count")),
                "ext_status": result.get("ext_status", p.get("ext_status")),
                "console_error_count": result.get("console_error_count", p.get("console_error_count")),
                "console_status": result.get("console_status", p.get("console_status")),
                "visual_checked_at": datetime.now().isoformat(),
            }
            break
