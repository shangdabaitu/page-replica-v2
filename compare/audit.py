#!/usr/bin/env python3
"""自动化审查模块：验证所有页面和标签页的视觉对比覆盖完整性。

对指定日期的复刻结果进行全面审查，检查：
1. 每种页面类型的标签页是否都已生成
2. 每个已生成页面是否都有视觉对比记录
3. 视觉对比是否覆盖了所有维度（像素、DOM、外部资源、控制台）
4. 标签页内容是否为空（iframe 未渲染等问题）

审查结果输出为结构化报告，便于定位遗漏。
"""
import json
import re
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum

import config


class CheckStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    ERROR = "error"
    MISSING = "missing"
    SKIPPED = "skipped"


@dataclass
class PageAudit:
    """单个页面的审查结果。"""
    url: str
    rel_path: str
    page_type: str
    status: CheckStatus = CheckStatus.OK
    has_visual_compare: bool = False
    diff_ratio: float | None = None
    dom_status: str = "skipped"
    ext_status: str = "ok"
    console_status: str = "ok"
    expected_tabs: list[str] = field(default_factory=list)
    found_tabs: list[str] = field(default_factory=list)
    missing_tabs: list[str] = field(default_factory=list)
    empty_tabs: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


@dataclass
class AuditReport:
    """整体审查报告。"""
    date: str
    total_pages: int = 0
    pages_with_compare: int = 0
    pages_without_compare: int = 0
    total_tabs_expected: int = 0
    total_tabs_found: int = 0
    total_tabs_missing: int = 0
    total_empty_tabs: int = 0
    pages: list[PageAudit] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "total_pages": self.total_pages,
            "pages_with_compare": self.pages_with_compare,
            "pages_without_compare": self.pages_without_compare,
            "total_tabs_expected": self.total_tabs_expected,
            "total_tabs_found": self.total_tabs_found,
            "total_tabs_missing": self.total_tabs_missing,
            "total_empty_tabs": self.total_empty_tabs,
            "pages": [
                {
                    "url": p.url,
                    "rel_path": p.rel_path,
                    "page_type": p.page_type,
                    "status": p.status.value,
                    "has_visual_compare": p.has_visual_compare,
                    "diff_ratio": p.diff_ratio,
                    "dom_status": p.dom_status,
                    "ext_status": p.ext_status,
                    "console_status": p.console_status,
                    "expected_tabs": p.expected_tabs,
                    "found_tabs": p.found_tabs,
                    "missing_tabs": p.missing_tabs,
                    "empty_tabs": p.empty_tabs,
                    "issues": p.issues,
                }
                for p in self.pages
            ],
            "summary": self.summary,
        }


# ---- 页面类型与预期标签页定义 ----

# Live detail 页面预期标签页后缀
LIVE_TAB_SUFFIXES = [
    "_event_detail",
    "_tech_same",
    "_jsq_50",
    "_players",
    "_text",
    "_animation",
    "_hd",
]

# 联赛页预期标签页编号范围（showHtml 2~11）
LEAGUE_TAB_RANGE = range(2, 12)

# 分析页预期标签页名称
ANALYSIS_TAB_NAMES = [
    "integral_1",
    "integral_2",
    "settype_1",
    "settype_2",
    "settype_3",
    "vs2_new",
]


def _detect_page_type_from_path(rel_path: str) -> str:
    """从相对路径判断页面类型。路径形如 2026-07-21/league/15.html。"""
    # 去掉日期前缀（第一段目录）
    parts = rel_path.split("/", 1)
    path = parts[1] if len(parts) > 1 else rel_path

    if path.startswith("live/detail/"):
        return "live_detail"
    if "/analysis/" in path or path.startswith("analysis/"):
        return "analysis"
    if path.startswith("league/"):
        return "league"
    if path.startswith("team/"):
        return "team"
    if path.startswith("AsianOdds_n/"):
        return "asianodds"
    if path.startswith("OverDown_n/"):
        return "overdown"
    if path.startswith("Corner/"):
        return "corner"
    if path.startswith("oddslist/"):
        return "oddslist"
    if path.endswith("index.html"):
        return "list"
    return "generic"


def _is_tab_file(filename: str) -> bool:
    """判断文件是否是某个主页面的标签页/子状态文件。

    支持的标签页命名模式：
      - _tab{N}        : 联赛页 showHtml 标签（如 15_tab2.html）
      - _tab{name}     : 分析页交互状态（如 2929663cn_tabintegral_1.htm）
      - _event_detail  : live detail 详细事件
      - _tech_same     : live detail 技统同主客
      - _jsq_50        : live detail 进失球近50场
      - _players       : live detail 球员统计
      - _text          : live detail 文字直播
      - _animation     : live detail 动画直播
      - _hd            : live detail 高清直播
    """
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    tab_patterns = [
        "_tab",
        "_event_detail",
        "_tech_same",
        "_jsq_50",
        "_players",
        "_text",
        "_animation",
        "_hd",
    ]
    return any(p in stem for p in tab_patterns)


def _get_expected_tabs(page_type: str, rel_path: str) -> list[str]:
    """根据页面类型返回预期的标签页文件名后缀。"""
    if page_type == "live_detail":
        # 形如 live/detail/2929663cn.htm
        # 预期标签页: 2929663cn_event_detail.htm, 2929663cn_players.htm 等
        base = Path(rel_path).stem  # 2929663cn
        return [f"{base}{suffix}.htm" for suffix in LIVE_TAB_SUFFIXES]
    elif page_type == "league":
        # 形如 league/15.html
        base = Path(rel_path).stem  # 15
        return [f"{base}_tab{t}.html" for t in LEAGUE_TAB_RANGE]
    elif page_type == "analysis":
        # 形如 analysis/2929663cn.htm
        base = Path(rel_path).stem  # 2929663cn
        return [f"{base}_tab{name}.htm" for name in ANALYSIS_TAB_NAMES]
    return []


def _check_file_not_empty(path: Path, min_size: int = 500) -> bool:
    """检查文件内容是否足够大（排除空壳页面）。"""
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
        # 去掉 HTML 标签后的纯文本长度
        text = re.sub(r"<[^>]+>", "", content).strip()
        return len(text) > min_size
    except Exception:
        return False


def _load_meta(date: str) -> dict:
    """加载 meta.json。优先从 output 目录读取，回退到 docs 目录。"""
    for base in (config.OUTPUT_DIR, config.BASE_DIR / "docs"):
        meta_path = base / date / "meta.json"
        if meta_path.exists():
            try:
                return json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def _load_report(date: str) -> dict:
    """加载 report.json。优先从 output 目录读取，回退到 docs 目录。"""
    for base in (config.OUTPUT_DIR, config.BASE_DIR / "docs"):
        report_path = base / date / "report.json"
        if report_path.exists():
            try:
                return json.loads(report_path.read_text(encoding="utf-8"))
            except Exception:
                pass
    return {}


def audit_date(date: str) -> AuditReport:
    """对指定日期的复刻结果进行全面审查。

    检查内容：
    1. meta.json 中记录的每个页面是否都有视觉对比结果
    2. 每种页面类型的标签页是否都已生成
    3. 标签页内容是否为空（iframe 未渲染等问题）
    4. 视觉对比各维度是否都执行了
    """
    report = AuditReport(date=date)
    base_dir = config.OUTPUT_DIR / date

    if not base_dir.exists():
        report.summary = {"error": f"日期目录不存在: {date}"}
        return report

    meta = _load_meta(date)
    stored_report = _load_report(date)

    # 构建 URL -> 对比结果 的映射
    compare_map: dict[str, dict] = {}
    for page in meta.get("pages", []):
        url = page.get("url", "")
        if url:
            compare_map[url] = page

    # 构建报告详情中的对比结果映射
    report_details: list[dict] = stored_report.get("details", [])
    detail_map: dict[str, dict] = {}
    for d in report_details:
        url = d.get("url", "")
        if url:
            detail_map[url] = d

    # 收集所有已生成的页面文件（优先 output，回退到 docs）
    scan_dir = base_dir
    # 检查 output 目录是否有 HTML 文件；没有则回退到 docs
    has_html = any(scan_dir.rglob("*.html")) or any(scan_dir.rglob("*.htm"))
    if not has_html:
        docs_dir = config.BASE_DIR / "docs" / date
        if docs_dir.exists():
            scan_dir = docs_dir
    if not scan_dir.exists():
        report.summary = {"error": f"日期目录不存在: {date}"}
        return report

    all_files: list[Path] = []
    for ext in ("*.html", "*.htm"):
        all_files.extend(scan_dir.rglob(ext))

    # 过滤掉 compare.html 和 diff 目录
    all_files = [
        f for f in all_files
        if ".compare." not in f.name and "diff" not in f.parts
    ]

    # 计算相对路径的基准目录
    rel_base = config.OUTPUT_DIR if scan_dir == base_dir else config.BASE_DIR / "docs"

    # 按主页面和标签页分类
    main_pages: dict[str, Path] = {}  # rel_path -> Path
    tab_files: dict[str, list[Path]] = {}  # main_rel -> [tab_paths]

    for f in all_files:
        rel = str(f.relative_to(rel_base))
        # 判断是否是标签页文件
        if _is_tab_file(f.stem):
            # 提取主页面名：去掉标签后缀
            stem = f.stem
            main_stem = stem
            for suffix in [
                "_event_detail", "_tech_same", "_jsq_50",
                "_players", "_text", "_animation", "_hd",
            ]:
                if stem.endswith(suffix):
                    main_stem = stem[:-len(suffix)]
                    break
            if "_tab" in stem:
                main_stem = stem.split("_tab")[0]
            main_name = f"{main_stem}{f.suffix}"
            # 找到主页面对应的目录
            main_rel = str(f.parent / main_name)
            if main_rel not in tab_files:
                tab_files[main_rel] = []
            tab_files[main_rel].append(f)
        else:
            main_pages[rel] = f

    # 审查每个主页面
    for rel_path, file_path in sorted(main_pages.items()):
        page_type = _detect_page_type_from_path(rel_path)

        # 从 meta 中查找对应的 URL
        url = ""
        for u, p in compare_map.items():
            if p.get("rel_path") == rel_path:
                url = u
                break

        # 也从文件路径推断 URL（用于 live detail 等不在 meta 中的页面）
        if not url and page_type == "live_detail":
            m = re.match(r"live/detail/(\d+cn)\.htm", rel_path)
            if m:
                url = f"https://live.titan007.com/detail/{m.group(1)}.htm"

        page_audit = PageAudit(
            url=url,
            rel_path=rel_path,
            page_type=page_type,
        )

        # 检查视觉对比结果
        meta_entry = compare_map.get(url, {})
        report_entry = detail_map.get(url, {})

        if meta_entry or report_entry:
            page_audit.has_visual_compare = True
            entry = report_entry or meta_entry
            page_audit.diff_ratio = entry.get("diff_ratio")
            page_audit.dom_status = entry.get("dom_status", "skipped")
            page_audit.ext_status = entry.get("ext_status", "ok")
            page_audit.console_status = entry.get("console_status", "ok")

            # 检查各维度状态
            if page_audit.dom_status == "error":
                page_audit.issues.append(f"DOM 差异过大: {entry.get('dom_diff_ratio')}")
                page_audit.status = CheckStatus.ERROR
            if page_audit.ext_status == "error":
                page_audit.issues.append(f"外部资源过多: {entry.get('ext_resource_count')} 个")
                page_audit.status = CheckStatus.ERROR
            if page_audit.console_status == "error":
                page_audit.issues.append(f"控制台错误过多: {entry.get('console_error_count')} 个")
                page_audit.status = CheckStatus.ERROR
            if page_audit.diff_ratio is not None and page_audit.diff_ratio > config.DIFF_THRESHOLD_RETRY:
                page_audit.issues.append(f"像素差异过大: {page_audit.diff_ratio:.2%}")
                page_audit.status = CheckStatus.ERROR
        else:
            page_audit.has_visual_compare = False
            page_audit.issues.append("缺少视觉对比记录")
            if page_audit.status == CheckStatus.OK:
                page_audit.status = CheckStatus.WARNING

        # 检查标签页
        expected_tabs = _get_expected_tabs(page_type, rel_path)
        page_audit.expected_tabs = [Path(t).name for t in expected_tabs]

        # 查找已生成的标签页
        found_tab_names: list[str] = []
        main_stem = Path(rel_path).stem
        main_suffix = Path(rel_path).suffix

        for tab_file in tab_files.get(rel_path, []):
            found_tab_names.append(tab_file.name)

        # 也检查目录中实际存在的标签页文件
        file_dir = file_path.parent
        for ext in ("*.html", "*.htm"):
            for f in file_dir.glob(ext):
                if f.stem.startswith(f"{main_stem}_tab"):
                    if f.name not in found_tab_names:
                        found_tab_names.append(f.name)

        page_audit.found_tabs = found_tab_names

        # 检查缺失的标签页
        missing = []
        for expected in expected_tabs:
            expected_name = Path(expected).name
            if expected_name not in found_tab_names:
                missing.append(expected_name)
        page_audit.missing_tabs = missing

        # 检查空标签页（内容过短，可能 iframe 未渲染）
        empty_tabs = []
        for tab_file in tab_files.get(rel_path, []):
            if not _check_file_not_empty(tab_file):
                empty_tabs.append(tab_file.name)
        page_audit.empty_tabs = empty_tabs

        # 更新状态
        if missing:
            page_audit.issues.append(f"缺少标签页: {', '.join(missing)}")
            if page_audit.status == CheckStatus.OK:
                page_audit.status = CheckStatus.WARNING
        if empty_tabs:
            page_audit.issues.append(f"空标签页（可能 iframe 未渲染）: {', '.join(empty_tabs)}")
            page_audit.status = CheckStatus.ERROR

        report.pages.append(page_audit)
        report.total_pages += 1
        if page_audit.has_visual_compare:
            report.pages_with_compare += 1
        else:
            report.pages_without_compare += 1
        report.total_tabs_expected += len(expected_tabs)
        report.total_tabs_found += len(found_tab_names)
        report.total_tabs_missing += len(missing)
        report.total_empty_tabs += len(empty_tabs)

    # 生成汇总
    status_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    for p in report.pages:
        s = p.status.value
        status_counts[s] = status_counts.get(s, 0) + 1
        type_counts[p.page_type] = type_counts.get(p.page_type, 0) + 1

    report.summary = {
        "status_counts": status_counts,
        "type_counts": type_counts,
        "compare_coverage": (
            f"{report.pages_with_compare}/{report.total_pages} "
            f"({report.pages_with_compare / report.total_pages * 100:.1f}%)"
            if report.total_pages > 0
            else "0/0"
        ),
        "tab_coverage": (
            f"{report.total_tabs_found}/{report.total_tabs_expected} "
            f"({report.total_tabs_found / report.total_tabs_expected * 100:.1f}%)"
            if report.total_tabs_expected > 0
            else "N/A"
        ),
        "missing_tabs": report.total_tabs_missing,
        "empty_tabs": report.total_empty_tabs,
    }

    return report


def print_audit_report(report: AuditReport) -> None:
    """以可读格式打印审查报告。"""
    print(f"\n{'='*60}")
    print(f"  视觉对比覆盖审查报告 - {report.date}")
    print(f"{'='*60}")
    print(f"\n  总页面数: {report.total_pages}")
    print(f"  有视觉对比: {report.pages_with_compare}")
    print(f"  无视觉对比: {report.pages_without_compare}")
    print(f"  对比覆盖率: {report.summary.get('compare_coverage', 'N/A')}")
    print(f"\n  预期标签页: {report.total_tabs_expected}")
    print(f"  已生成标签页: {report.total_tabs_found}")
    print(f"  缺失标签页: {report.total_tabs_missing}")
    print(f"  空标签页: {report.total_empty_tabs}")
    print(f"  标签页覆盖率: {report.summary.get('tab_coverage', 'N/A')}")

    print(f"\n  状态统计:")
    for status, count in sorted(report.summary.get("status_counts", {}).items()):
        print(f"    {status}: {count}")

    print(f"\n  页面类型统计:")
    for ptype, count in sorted(report.summary.get("type_counts", {}).items()):
        print(f"    {ptype}: {count}")

    # 列出有问题的页面
    problem_pages = [p for p in report.pages if p.status != CheckStatus.OK]
    if problem_pages:
        print(f"\n  有问题的页面 ({len(problem_pages)} 个):")
        for p in problem_pages:
            print(f"\n    [{p.status.value.upper()}] {p.rel_path} ({p.page_type})")
            if not p.has_visual_compare:
                print(f"      - 缺少视觉对比记录")
            if p.diff_ratio is not None and p.diff_ratio > config.DIFF_THRESHOLD_IGNORE:
                print(f"      - 像素差异: {p.diff_ratio:.2%}")
            if p.missing_tabs:
                print(f"      - 缺失标签页: {', '.join(p.missing_tabs)}")
            if p.empty_tabs:
                print(f"      - 空标签页: {', '.join(p.empty_tabs)}")
            for issue in p.issues:
                if issue not in (
                    "缺少视觉对比记录",
                    f"缺少标签页: {', '.join(p.missing_tabs)}" if p.missing_tabs else "",
                ):
                    print(f"      - {issue}")
    else:
        print(f"\n  所有页面审查通过!")

    print(f"\n{'='*60}\n")


def save_audit_report(report: AuditReport) -> Path:
    """将审查报告保存为 JSON 文件。"""
    output_path = config.OUTPUT_DIR / report.date / "audit_report.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path


if __name__ == "__main__":
    import sys

    d = sys.argv[1] if len(sys.argv) > 1 else "2026-07-21"
    r = audit_date(d)
    print_audit_report(r)
    saved = save_audit_report(r)
    print(f"审查报告已保存: {saved}")
