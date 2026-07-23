#!/usr/bin/env python3
"""手动把 output/ 内容同步到 docs/，并更新 dates.json。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import data_store


def main():
    docs_dir = data_store.sync_output_to_docs()
    print(f"已同步到: {docs_dir}")
    dates = data_store.build_dates_catalog()
    print(f"dates.json 已更新，共 {len(dates)} 个日期")
    for d in dates:
        print(f"  - {d['date']}: {len(d['matches'])} 场比赛，列表页 {d['list_page']}")


if __name__ == "__main__":
    main()
