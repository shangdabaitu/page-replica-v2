#!/usr/bin/env python3
"""复刻数据持久化模块"""
import json
import time
from pathlib import Path
from datetime import datetime

import config


def _date_dir(date: str) -> Path:
    """获取某日期复刻结果的目录。"""
    d = config.OUTPUT_DIR / date
    d.mkdir(parents=True, exist_ok=True)
    return d


def _meta_path(date: str) -> Path:
    return _date_dir(date) / config.META_FILE


def _report_path(date: str) -> Path:
    return _date_dir(date) / config.REPORT_FILE


def load_meta(date: str) -> dict:
    """加载某日期的元数据。"""
    path = _meta_path(date)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] 读取 meta 失败: {e}")
    return {
        "date": date,
        "source_url": config.SOURCE_URL_TEMPLATE.format(date=date),
        "created_at": datetime.now().isoformat(),
        "pages": [],
        "matches": [],
    }


def save_meta(date: str, meta: dict):
    """保存某日期元数据。"""
    path = _meta_path(date)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def append_page(date: str, page_info: dict):
    """向元数据中追加一条页面记录。"""
    meta = load_meta(date)
    # 去重：以 url 为键，更新已有记录
    pages = {p["url"]: p for p in meta.get("pages", [])}
    pages[page_info["url"]] = page_info
    meta["pages"] = list(pages.values())
    save_meta(date, meta)


def save_matches(date: str, matches: list[dict]):
    """保存列表页解析出的比赛结构化数据。"""
    meta = load_meta(date)
    meta["matches"] = matches
    save_meta(date, meta)


def save_report(date: str, report: dict):
    """保存复刻报告。"""
    path = _report_path(date)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)


def load_report(date: str) -> dict | None:
    path = _report_path(date)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[WARN] 读取 report 失败: {e}")
    return None


def list_replicated_dates() -> list[str]:
    """列出已经复刻过至少列表页的日期。"""
    dates = []
    if not config.OUTPUT_DIR.exists():
        return dates
    for d in sorted(config.OUTPUT_DIR.iterdir()):
        if d.is_dir() and (d / config.META_FILE).exists():
            dates.append(d.name)
    return dates


def get_list_page_path(date: str) -> Path | None:
    """获取某日期列表页文件路径。"""
    path = _date_dir(date) / "index.html"
    if path.exists():
        return path
    return None
