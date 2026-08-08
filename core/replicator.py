#!/usr/bin/env python3
"""复刻引擎：按日期递归复刻页面并进行视觉对比"""
import hashlib
import io
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlunparse

from bs4 import BeautifulSoup
from PIL import Image

import config
from core.fetcher import fetch_url, normalize_url, decode_html
from core.inliner import inline_page
from core.renderer import render_and_capture, render_league_with_tabs
from core.simplifier import simplify_html
from core.watermark import inject_watermark
from core.live_tabs import replicate_all_live_tabs
from core.extractor import (
    extract_schedule_ids,
    extract_match_data,
    extract_links,
    extract_team_ids_from_analysis,
    extract_tab_urls,
    get_detail_urls,
)
from storage import data_store
from compare import visual


# 默认最大递归层级：1 列表页 -> 2 详情页 -> 3 子页面
DEFAULT_MAX_LEVEL = 3

# 任务级安全限制
MAX_PAGES_PER_JOB = 500          # 单个任务最多处理页面数
MAX_JOB_SECONDS = 30 * 60        # 单个任务最长运行 30 分钟


def _canonical_url(url: str) -> str:
    """把 URL 规范成统一形式，用于去重判断。

    规则：
      - 去掉 fragment
      - 域名、路径统一小写
      - query 参数按字母排序
      - 去掉已知无意义导航参数（如 l=0）
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    qs = parse_qs(parsed.query, keep_blank_values=True)
    # 删除空值和无意义导航参数
    drop_keys = {"l"}
    normalized_qs: dict[str, list[str]] = {}
    for key, values in qs.items():
        key_lower = key.lower()
        if key_lower in drop_keys:
            continue
        kept = [v for v in values if v.strip() != ""]
        if not kept:
            continue
        if key_lower not in normalized_qs:
            normalized_qs[key_lower] = []
        normalized_qs[key_lower].extend(kept)

    sorted_query = "&".join(
        f"{k}={v}"
        for k in sorted(normalized_qs.keys())
        for v in sorted(set(normalized_qs[k]))
    )
    return urlunparse((parsed.scheme.lower(), host, path, "", sorted_query, ""))


def _url_key(url: str) -> str:
    """生成 URL 的唯一键（基于规范化后的 URL）。"""
    return hashlib.md5(_canonical_url(url).encode("utf-8")).hexdigest()


def _is_league_page(url: str) -> bool:
    """判断 URL 是否为 info.titan007.com 的联赛/杯赛资料页（含赛程）。"""
    parsed = urlparse(url)
    if parsed.netloc.lower() != "info.titan007.com":
        return False
    return re.match(r"/cn/(?:SubLeague|CupMatch|League)/\d+\.html", parsed.path, re.I) is not None


def _is_analysis_page(url: str) -> bool:
    """判断 URL 是否为 zq.titan007.com 的比赛分析页（依赖 JS 动态加载数据）。"""
    parsed = urlparse(url)
    if parsed.netloc.lower() != "zq.titan007.com":
        return False
    return re.match(r"/analysis/\d+cn\.htm", parsed.path, re.I) is not None


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

    # info.titan007.com 资料库页
    if parsed.netloc.lower() == "info.titan007.com":
        # /cn/CupMatch/103.html   -> league/103.html
        # /cn/SubLeague/15.html   -> league/15.html
        # /cn/League/4.html       -> league/4.html
        # /cn/team/1234.html      -> team/1234.html
        m = re.match(r"cn/(?:CupMatch|SubLeague|League)/(\d+)\.html", path, re.I)
        if m:
            return f"league/{m.group(1)}.html"
        m = re.match(r"cn/team/(\d+)\.html", path, re.I)
        if m:
            return f"team/{m.group(1)}.html"
        # /cn/team/Summary/4075.html -> team/4075.html（与 zq.titan007.com 球队资料页等价）
        m = re.match(r"cn/team/Summary/(\d+)\.html", path, re.I)
        if m:
            return f"team/{m.group(1)}.html"

        # /cn/team/SummaryLeague/4075.html -> team/SummaryLeague/4075.html
        # /cn/team/Summary/SummaryLeague/4075.html -> team/SummaryLeague/4075.html
        # 必须放在通用三字段匹配之前，避免被误判为联赛资料页
        m = re.match(r"cn/team/([A-Za-z]+)/(\d+)\.html", path, re.I)
        if m:
            tab_name = _canonical_team_tab(m.group(1))
            if tab_name:
                return f"team/{tab_name}/{m.group(2)}.html"
        m = re.match(r"cn/team/Summary/([A-Za-z]+)/(\d+)\.html", path, re.I)
        if m:
            tab_name = _canonical_team_tab(m.group(1))
            if tab_name:
                return f"team/{tab_name}/{m.group(2)}.html"

        # 杯赛/联赛资料页的主页（带赛季参数与不带赛季参数等价）
        # /cn/SubLeague/2026/15.html   -> league/15.html
        # /cn/CupMatch/2026-2027/103.html -> league/103.html
        m = re.match(r"cn/(CupMatch|SubLeague|League)/([^/]+)/(\d+)\.html", path, re.I)
        if m:
            return f"league/{m.group(3)}.html"

        # 杯赛/联赛资料页的标签页（独立 URL）
        # /cn/CletGoal/2026-2027/103.html -> league/CletGoal/2026-2027/103.html
        # /cn/Archer/2026/15.html         -> league/Archer/2026/15.html
        m = re.match(r"cn/([A-Za-z]+)/([^/]+)/(\d+)\.html", path, re.I)
        if m:
            return f"league/{m.group(1)}/{m.group(2)}/{m.group(3)}.html"

    # zq.titan007.com 球队资料汇总页
    if parsed.netloc.lower() == "zq.titan007.com":
        m = re.match(r"cn/team/Summary/(\d+)\.html", path, re.I)
        if m:
            return f"team/{m.group(1)}.html"
        m = re.match(r"big/team/Summary/(\d+)\.html", path, re.I)
        if m:
            return f"team/big_{m.group(1)}.html"
        # 球队资料页的标签页（独立 URL）
        # /cn/team/SummaryLeague/4075.html -> team/SummaryLeague/4075.html
        # /cn/team/Summary/SummaryLeague/4075.html -> team/SummaryLeague/4075.html
        m = re.match(r"cn/team/([A-Za-z]+)/(\d+)\.html", path, re.I)
        if m:
            tab_name = _canonical_team_tab(m.group(1))
            if tab_name:
                return f"team/{tab_name}/{m.group(2)}.html"
        m = re.match(r"cn/team/Summary/([A-Za-z]+)/(\d+)\.html", path, re.I)
        if m:
            tab_name = _canonical_team_tab(m.group(1))
            if tab_name:
                return f"team/{tab_name}/{m.group(2)}.html"

    # live.titan007.com 现场分析页
    if parsed.netloc.lower() == "live.titan007.com":
        m = re.match(r"detail/(\d+)(cn|sb)?\.htm", path, re.I)
        if m:
            suffix = m.group(2) or ""
            return f"live/detail/{m.group(1)}{suffix}.htm"

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


def _freeze_rendered_page(html: str) -> str:
    """
    对渲染后的 DOM 做冻结处理：删除所有脚本，
    只注入 openAnalysisPage 的最小本地实现，确保页面以静态方式展示。
    同时注入 UTF-8 charset meta，避免静态文件缺少 HTTP 头时浏览器乱猜编码。
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in list(soup.find_all("script")):
        tag.decompose()

    head = soup.head
    if head is None:
        head = soup.new_tag("head")
        if soup.html:
            soup.html.insert(0, head)
        elif soup.body:
            soup.body.insert_before(head)

    # 确保有 charset 声明
    has_charset = False
    for meta in head.find_all("meta"):
        content = (meta.get("content") or "").lower()
        http_equiv = (meta.get("http-equiv") or "").lower()
        if meta.get("charset") or "charset" in content or http_equiv == "content-type":
            has_charset = True
            break
    if not has_charset:
        charset_meta = soup.new_tag("meta", charset="utf-8")
        head.insert(0, charset_meta)

    # 禁用页面缓存，避免 GitHub Pages 10 分钟缓存导致用户看到过旧版本
    cache_meta = soup.new_tag("meta", attrs={"http-equiv": "Cache-Control", "content": "no-cache, no-store, must-revalidate"})
    head.insert(0, cache_meta)
    pragma_meta = soup.new_tag("meta", attrs={"http-equiv": "Pragma", "content": "no-cache"})
    head.insert(0, pragma_meta)
    expires_meta = soup.new_tag("meta", attrs={"http-equiv": "Expires", "content": "0"})
    head.insert(0, expires_meta)

    cache_buster = config.CACHE_BUSTER

    # URL 自校验：如果当前地址没有缓存破坏参数，强制重定向到带参地址，
    # 避免用户继续沿用旧版缓存页面。
    reload_script = soup.new_tag("script")
    reload_script.string = (
        "(function(){"
        "var qs=location.search;"
        f"if(!qs.includes('{cache_buster}')){{"
        "var u=location.href;location.replace(u+(u.indexOf('?')>-1?'&':'?')+'" + cache_buster + "');"
        "}}"
        ")();"
    )
    head.insert(0, reload_script)

    # 注入本地 openAnalysisPage（列表页传入的是 match_id，分析页后缀为 cn.htm）。
    # 加入缓存破坏参数，避免浏览器沿用旧版无水印页面。
    new_tag = soup.new_tag("script")
    new_tag.string = f"function openAnalysisPage(matchID){{ window.open('./analysis/' + matchID + 'cn.htm?{cache_buster}'); }}"
    head.append(new_tag)

    # 注入本地 showDetail，让分析页“现场分析”标签跳转本地 live/detail 页面。
    # 由于分析页位于 {date}/analysis/ 下，而 live/detail 位于 {date}/live/detail/ 下，
    # 需要从当前文件所在目录向上退一级再进入 live/detail。
    # 加入缓存破坏参数，确保从分析页点入时加载最新版本。
    show_detail_tag = soup.new_tag("script")
    show_detail_tag.string = f"function showDetail(matchID){{ var base = window.location.href.replace(/\\/[^\\/]*$/, '/'); window.location.href = base + '../live/detail/' + matchID + 'cn.htm?{cache_buster}'; }}"
    head.append(show_detail_tag)

    # 移除依赖已删除脚本的悬停事件，保留 onclick
    for tag in soup.find_all(attrs={"onmouseover": True, "onmouseout": True}):
        del tag["onmouseover"]
        del tag["onmouseout"]
    return str(soup)


def _equivalent_urls(url: str) -> list[str]:
    """返回与给定 URL 等价的可能变体，用于处理站点内部多域名指向同一内容的情况。"""
    variants = {url}
    parsed = urlparse(url)
    path = parsed.path
    qs = parsed.query

    # 1x2.titan007.com 的欧赔页与 op1.titan007.com 的欧赔页等价
    if parsed.netloc.lower() == "1x2.titan007.com" and re.match(r"/oddslist/\d+\.htm", path, re.I):
        variants.add(url.replace("//1x2.titan007.com", "//op1.titan007.com", 1))
        variants.add(url.replace("https://1x2.titan007.com", "https://op1.titan007.com", 1))

    # info.titan007.com 的球队资料页/分析页与 zq.titan007.com 等价（双向）
    if parsed.netloc.lower() in ("info.titan007.com", "zq.titan007.com"):
        if re.match(r"/cn/team/Summary/\d+\.html", path, re.I):
            variants.add(url.replace("//info.titan007.com", "//zq.titan007.com", 1))
            variants.add(url.replace("https://info.titan007.com", "https://zq.titan007.com", 1))
            variants.add(url.replace("//zq.titan007.com", "//info.titan007.com", 1))
            variants.add(url.replace("https://zq.titan007.com", "https://info.titan007.com", 1))
        if re.match(r"/analysis/\d+cn\.htm", path, re.I):
            variants.add(url.replace("//info.titan007.com", "//zq.titan007.com", 1))
            variants.add(url.replace("https://info.titan007.com", "https://zq.titan007.com", 1))
            variants.add(url.replace("//zq.titan007.com", "//info.titan007.com", 1))
            variants.add(url.replace("https://zq.titan007.com", "https://info.titan007.com", 1))

    # 去掉仅用于导航的 l=0 参数（AsianOdds_n、OverDown_n、Corner 等）
    if "l=0" in qs:
        qs_clean = "&".join(p for p in qs.split("&") if p != "l=0")
        variants.add(parsed._replace(query=qs_clean).geturl())

    # http 与 https 等价
    if url.startswith("http://"):
        variants.add("https://" + url[7:])
    elif url.startswith("https://"):
        variants.add("http://" + url[8:])

    return list(variants)


def _canonical_team_tab(tab_name: str) -> str | None:
    """把球队标签页名称统一成规范大小写，返回 None 表示不是已知标签。"""
    mapping = {
        "summaryleague": "SummaryLeague",
        "summarycup": "SummaryCup",
        "teamnearyear": "TeamNearYear",
        "teamsche": "TeamSche",
        "cteamsche": "CTeamSche",
        "lineup": "Lineup",
        "playerdata": "PlayerData",
        "playerzh": "PlayerZh",
        "teamhistoryorder": "TeamHistoryOrder",
        "teamnews": "TeamNews",
    }
    return mapping.get(tab_name.lower())


def _resolve_team_tab_link(source_path: Path, raw_href: str, base_url: str) -> str | None:
    """球队资料页 team-nav 标签导航的兜底改写：修正源站 malformed 相对路径。

    支持的畸形形式（以 team_id=4075, tab=SummaryLeague 为例）：
      - ../cn/team/Summary/SummaryLeague/4075.html
      - ../Summary/SummaryLeague/4075.html
      - ../SummaryLeague/4075.html
      - SummaryLeague/4075.html
      - ../../league/team/Summary/4075.html（旧错误路径）
    返回相对于 source_path 的正确本地路径。
    """
    # 1) 先按常规方式解析，若已能解析到正确的 team/tab URL 则不再兜底
    abs_url = normalize_url(raw_href, base_url)
    if abs_url:
        parsed = urlparse(abs_url)
        # 球队汇总页 /cn/team/Summary/{id}.html
        m = re.match(r"/cn/team/Summary/(\d+)\.html", parsed.path, re.I)
        if m:
            return _build_team_summary_rel(source_path, m.group(1))
        m = re.match(r"/cn/team/([A-Za-z]+)/(\d+)\.html", parsed.path, re.I)
        if m:
            tab_name = _canonical_team_tab(m.group(1))
            if tab_name:
                return _build_team_tab_rel(source_path, tab_name, m.group(2))

    # 2) 直接从 raw_href 中匹配 tab 名与球队 ID
    # 先尝试球队汇总页（Summary）
    m = re.search(r"(?:league/)?team/Summary/(\d+)\.html", raw_href, re.I)
    if m:
        return _build_team_summary_rel(source_path, m.group(1))
    m = re.search(r"(?:Summary/)?([A-Za-z]+)/(\d+)\.html", raw_href)
    if not m:
        return None
    tab_name = _canonical_team_tab(m.group(1))
    if not tab_name:
        return None
    return _build_team_tab_rel(source_path, tab_name, m.group(2))


def _build_team_summary_rel(source_path: Path, team_id: str) -> str | None:
    """根据 source_path 的位置生成指向球队汇总页 team/{team_id}.html 的相对路径。"""
    parts = source_path.relative_to(config.OUTPUT_DIR).parts
    if len(parts) < 3 or parts[1] != "team":
        return None

    if len(parts) == 3:
        # 已经在球队汇总页，当前页
        if parts[2] == f"{team_id}.html":
            return None
        return f"{team_id}.html"
    elif len(parts) == 4:
        # {date}/team/{current_tab}/{team_id}.html -> ../{team_id}.html
        return f"../{team_id}.html"
    return None


def _build_team_tab_rel(source_path: Path, tab_name: str, team_id: str) -> str | None:
    """根据 source_path 的位置生成指向 team/{tab}/{team_id}.html 的相对路径。"""
    parts = source_path.relative_to(config.OUTPUT_DIR).parts
    # 期望路径：{date}/team/{team_id}.html 或 {date}/team/{tab}/{team_id}.html
    if len(parts) < 3 or parts[1] != "team":
        return None

    if len(parts) == 3:
        # {date}/team/{team_id}.html -> team/{tab}/{team_id}.html 的相对路径
        return f"{tab_name}/{team_id}.html"
    elif len(parts) == 4:
        # {date}/team/{current_tab}/{team_id}.html
        if tab_name == parts[2]:
            return None  # 当前页，不生成链接
        return f"../{tab_name}/{team_id}.html"
    return None


def _rewrite_links(html: str, base_url: str, source_path: Path, url_map: dict[str, str]) -> str:
    """把已复刻的链接替换成相对于当前文件的本地路径。"""
    soup = BeautifulSoup(html, "lxml")
    attrs = ["href", "src", "action"]
    source_dir = source_path.parent

    def _resolve_rel(abs_url: str) -> str | None:
        for variant in _equivalent_urls(abs_url):
            if variant in url_map:
                target_rel = url_map[variant]
                target_path = config.OUTPUT_DIR / target_rel
                try:
                    return os.path.relpath(target_path, source_dir)
                except Exception:
                    return target_rel
        return None

    for tag in soup.find_all():
        # 1) 普通 href / src / action
        for attr in attrs:
            val = tag.get(attr)
            if not val:
                continue
            abs_url = normalize_url(val, base_url)
            if not abs_url:
                continue
            rel = _resolve_rel(abs_url)
            if rel is not None:
                tag[attr] = rel

        # 2) onclick 中的 location.href / window.location 跳转（标签页导航常用）
        onclick = tag.get("onclick")
        if onclick:
            new_onclick = onclick
            for m in re.finditer(r"(location\.href|window\.location)\s*=\s*['\"]([^'\"]+)['\"]", onclick):
                raw_href = m.group(2)
                abs_url = normalize_url(raw_href, base_url)
                rel = None
                if abs_url:
                    rel = _resolve_rel(abs_url)
                # 球队资料页 team-nav 畸形路径兜底
                if rel is None:
                    rel = _resolve_team_tab_link(source_path, raw_href, base_url)
                if rel is not None:
                    # 保持引号风格
                    quote = m.group(0)[m.group(0).index(raw_href) - 1]
                    new_onclick = new_onclick.replace(
                        m.group(0),
                        f"{m.group(1)}={quote}{rel}{quote}",
                    )
            if new_onclick != onclick:
                tag["onclick"] = new_onclick

        # 3) showHtml JS 标签页导航（SubLeague 赛程资料统计页内部标签）
        onclick = tag.get("onclick")
        if onclick and source_path.parent.name == "league":
            m = re.search(r"showHtml\s*\(\s*(\d+)\s*\)", onclick, re.I)
            if m:
                tab_num = int(m.group(1))
                base_stem = source_path.stem.split("_tab")[0]
                target_name = f"{base_stem}.html" if tab_num == 1 else f"{base_stem}_tab{tab_num}.html"
                target_path = source_path.parent / target_name
                try:
                    rel = os.path.relpath(target_path, source_dir)
                except Exception:
                    rel = target_name
                tag["href"] = rel
                del tag["onclick"]

    return str(soup)


def _process_single_page(
    url: str,
    date: str,
    level: int,
    base_dir: Path,
    url_map: dict[str, str],
    force_compare: bool = False,
    page_type: str = "link",
    cancel_event: threading.Event | None = None,
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
        if cancel_event is not None and cancel_event.is_set():
            return {
                **event_base,
                "status": "cancelled",
                "attempt": attempt + 1,
                "message": "用户取消",
                "output_path": None,
            }

        try:
            data, ct = fetch_url(url, timeout=config.REQUEST_TIMEOUT)
            if data is None:
                raise RuntimeError(f"第 {attempt + 1} 次抓取失败")
            raw_html = decode_html(data, ct)

            if cancel_event is not None and cancel_event.is_set():
                return {
                    **event_base,
                    "status": "cancelled",
                    "attempt": attempt + 1,
                    "message": "用户取消",
                    "output_path": None,
                }

            # 用 Headless Chromium 渲染，拿到 JS 执行后的 DOM。
            # 需求要求所有页面都先执行原始页面 JS；联赛/杯赛资料页额外合并轮次与标签页。
            # 同时捕获同源首屏截图，避免后续数据源页面被反爬/过期导致视觉对比失真。
            if cancel_event is not None and cancel_event.is_set():
                return {
                    **event_base,
                    "status": "cancelled",
                    "attempt": attempt + 1,
                    "message": "用户取消",
                    "output_path": None,
                }

            tab_htmls: dict[int, str] = {}
            source_img = None
            try:
                if _is_league_page(url):
                    rendered_html, tab_htmls, source_png = render_league_with_tabs(url)
                else:
                    # 非联赛页（如分析页、欧赔页）等待 3s 让 JS 加载动态数据，
                    # 比原来的 8s 显著缩短整体耗时。
                    rendered_html, source_png = render_and_capture(url, wait_ms=3000)
                try:
                    source_img = Image.open(io.BytesIO(source_png))
                except Exception as e:
                    print(f"[WARN] 源页面截图解码失败: {url} -> {e}")
            except Exception as e:
                print(f"[WARN] 页面渲染失败，回退到原始 HTML: {url} -> {e}")
                rendered_html = raw_html

            if cancel_event is not None and cancel_event.is_set():
                return {
                    **event_base,
                    "status": "cancelled",
                    "attempt": attempt + 1,
                    "message": "用户取消",
                    "output_path": None,
                }

            inlined = inline_page(rendered_html, url, cancel_event=cancel_event)
            frozen = _freeze_rendered_page(inlined)

            if cancel_event is not None and cancel_event.is_set():
                return {
                    **event_base,
                    "status": "cancelled",
                    "attempt": attempt + 1,
                    "message": "用户取消",
                    "output_path": None,
                }

            # 注入页面交互所需的 JS 函数（在冻结后、简体转换前）
            from core.js_injector import inject_page_scripts
            injected = inject_page_scripts(frozen, url)

            simplified = simplify_html(injected)

            # 视觉对比使用无水印版本，避免水印自身造成人为差异
            compare_html = _rewrite_links(simplified, url, output_path, url_map)
            compare_path = output_path.with_suffix(".compare.html")
            compare_path.write_text(compare_html, encoding="utf-8")

            marked = inject_watermark(simplified)
            final_html = _rewrite_links(marked, url, output_path, url_map)
            best_html = final_html

            output_path.write_text(final_html, encoding="utf-8")

            # 保存联赛/赛事资料页的 showHtml JS 内部标签页
            tab_paths: dict[int, str] = {}
            for t, tab_html in (tab_htmls or {}).items():
                try:
                    tab_rel = f"{rel_path[:-5]}_tab{t}.html" if rel_path.endswith(".html") else f"{rel_path}_tab{t}.html"
                    tab_output_path = base_dir / tab_rel
                    tab_output_path.parent.mkdir(parents=True, exist_ok=True)
                    tab_inlined = inline_page(tab_html, url, cancel_event=cancel_event)
                    tab_frozen = _freeze_rendered_page(tab_inlined)
                    from core.js_injector import inject_page_scripts as _inject_tab_scripts
                    tab_injected = _inject_tab_scripts(tab_frozen, url)
                    tab_simplified = simplify_html(tab_injected)
                    tab_marked = inject_watermark(tab_simplified)
                    tab_final_html = _rewrite_links(tab_marked, url, tab_output_path, url_map)
                    tab_output_path.write_text(tab_final_html, encoding="utf-8")
                    # 用伪 URL 登记，便于最终统一重写链接
                    tab_url = f"{url}#tab{t}"
                    tab_rel_path = str(tab_output_path.relative_to(config.OUTPUT_DIR))
                    url_map[tab_url] = tab_rel_path
                    tab_paths[t] = tab_rel_path
                except Exception as e:
                    print(f"[WARN] 保存标签页 {t} 失败: {url} -> {e}")

            # 视觉对比：需求要求统计各层级已视觉对比页面数，默认对所有页面执行。
            # 优先使用渲染时捕获的源截图，避免数据源页面后续被反爬或内容过期导致误判。
            if source_img is not None:
                compare_result = visual.compare_with_source_image(
                    source_img,
                    url,
                    compare_path,
                    output_dir=base_dir / "diff",
                )
            else:
                compare_result = visual.compare_pages(
                    url,
                    compare_path,
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


def replicate_date(date: str, max_level: int | None = None, cancel_event: threading.Event | None = None):
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
    visited: set[str] = set()      # 已处理完成的 URL（基于规范键）
    queued: set[str] = set()       # 已在队列中的 URL（基于规范键）
    report = {
        "date": date,
        "max_level": max_level,
        "pages_total": 0,
        "pages_ok": 0,
        "pages_retry": 0,
        "pages_fix": 0,
        "pages_error": 0,
        "pages_cancelled": 0,
        "details": [],
    }

    def _check_cancel(msg: str = "用户取消") -> bool:
        """检查取消标志或总超时，返回是否需要停止。"""
        if cancel_event is not None and cancel_event.is_set():
            return True
        if time.time() - start_time > MAX_JOB_SECONDS:
            return True
        return False

    yield {"type": "start", "date": date, "max_level": max_level}

    if _check_cancel():
        yield {"type": "finish", "date": date, "list_page": f"{date}/index.html", "report": report, "message": "任务启动前已取消"}
        return

    list_url = config.SOURCE_URL_TEMPLATE.format(date=date)
    queue = [(list_url, 1, "list")]
    queued.add(_url_key(list_url))

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
                    detail_key = _url_key(detail["url"])
                    if detail_key not in queued:
                        queue.append((detail["url"], detail["level"], detail["type"]))
                        queued.add(detail_key)
    except Exception as e:
        yield {"type": "warning", "message": f"解析列表页失败: {e}"}

    while queue:
        if _check_cancel():
            yield {"type": "warning", "message": "任务已取消或超过最大运行时间"}
            break

        if report["pages_total"] >= MAX_PAGES_PER_JOB:
            yield {"type": "warning", "message": f"已达到单任务最大页面数限制 {MAX_PAGES_PER_JOB}"}
            break

        # 取出下一批未访问的页面（最多 CONCURRENCY 个），同批次内并发处理
        batch: list[tuple[str, int, str]] = []
        while queue and len(batch) < config.CONCURRENCY and report["pages_total"] + len(batch) < MAX_PAGES_PER_JOB:
            url, level, page_type = queue.pop(0)
            url_key = _url_key(url)
            if url_key in visited:
                continue
            visited.add(url_key)
            batch.append((url, level, page_type))

        if not batch:
            continue

        for url, level, page_type in batch:
            yield {"type": "progress", "url": url, "level": level, "status": "processing"}

        batch_results: list[dict] = []
        with ThreadPoolExecutor(max_workers=min(config.CONCURRENCY, len(batch))) as executor:
            future_to_task: dict = {}
            for url, level, page_type in batch:
                # 对单场分析页启用浏览器渲染并强制视觉对比
                force_compare = page_type == "detail_analysis"
                future = executor.submit(
                    _process_single_page,
                    url, date, level, base_dir, url_map,
                    force_compare=force_compare,
                    page_type=page_type,
                    cancel_event=cancel_event,
                )
                future_to_task[future] = (url, level, page_type)

            for future in as_completed(future_to_task):
                url, level, page_type = future_to_task[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = {
                        "url": url,
                        "level": level,
                        "rel_path": _url_to_relative_path(url),
                        "status": "error",
                        "attempt": 1,
                        "message": f"工作线程异常: {e}",
                        "output_path": None,
                    }
                batch_results.append((url, level, page_type, result))

        # 按 batch 原始顺序输出事件并更新共享状态，保证 SSE 顺序可预测
        batch_results.sort(key=lambda item: batch.index((item[0], item[1], item[2])))
        for url, level, page_type, result in batch_results:
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

                # 对单场分析页（析）提取主队/客队资料库 ID 并写入 meta.matches
                if level == 2 and page_type == "detail_analysis":
                    m = re.search(r"/analysis/(\d+)cn\.htm", url, re.I)
                    if m:
                        match_id = m.group(1)
                        try:
                            saved_path = config.OUTPUT_DIR / result["output_path"]
                            analysis_html = saved_path.read_text(encoding="utf-8")
                            team_ids = extract_team_ids_from_analysis(analysis_html)
                            if team_ids:
                                data_store.update_match_team_ids(date, match_id, team_ids)
                        except Exception as e:
                            yield {"type": "warning", "url": url, "message": f"提取球队资料 ID 失败: {e}"}

            if result["status"] == "ok":
                report["pages_ok"] += 1
            elif result["status"] == "ok_with_diff":
                report["pages_retry"] += 1
            elif result["status"] == "needs_fix":
                report["pages_fix"] += 1
            elif result["status"] == "error":
                report["pages_error"] += 1
            elif result["status"] == "cancelled":
                report["pages_cancelled"] += 1

            report["details"].append(result)
            yield {"type": "page_done", **result}

            # 任务被取消时不再继续扩散
            if _check_cancel():
                yield {"type": "warning", "message": "任务已取消或超过最大运行时间，停止扩散新链接"}
                break

            # 继续提取下一层链接（仅当处理成功时）
            if result["status"] not in ("error", "cancelled") and level < max_level:
                try:
                    saved_path = base_dir / result["rel_path"]
                    html = saved_path.read_text(encoding="utf-8")
                    links = extract_links(html, url, level, max_level)
                    for link in links:
                        link_key = _url_key(link["url"])
                        if link_key not in queued and link_key not in visited:
                            queued.add(link_key)
                            queue.append((link["url"], link["level"], link.get("type", "link")))
                except Exception as e:
                    yield {"type": "warning", "url": url, "message": f"提取子链接失败: {e}"}

            # 提取并复刻页面自身的标签页（球队资料页、杯赛/联赛资料页的独立 URL 标签）。
            # 仅对非标签页类型的页面执行，避免标签页之间互相扩散导致无限循环。
            if result["status"] not in ("error", "cancelled") and result.get("rel_path") and page_type not in ("team_tab", "league_tab"):
                try:
                    saved_path = base_dir / result["rel_path"]
                    html = saved_path.read_text(encoding="utf-8")
                    tabs = extract_tab_urls(html, url)
                    current_rel = _url_to_relative_path(url)
                    for tab in tabs:
                        tab_url = tab["url"]
                        tab_key = _url_key(tab_url)
                        if tab_key in queued or tab_key in visited:
                            continue
                        # 跳过与当前页同内容的标签（如 /cn/SubLeague/2026/15.html 与 /cn/SubLeague/15.html）
                        if _url_to_relative_path(tab_url) == current_rel:
                            continue
                        queued.add(tab_key)
                        queue.append((tab_url, max_level, tab["type"]))
                except Exception as e:
                    yield {"type": "warning", "url": url, "message": f"提取标签页失败: {e}"}

    # 复刻所有 live detail 页的内部标签页（现场分析/动画直播/高清直播/球员统计/文字直播等）
    try:
        if not _check_cancel():
            yield {"type": "progress", "url": "__live_tabs__", "level": 0, "status": "replicating_live_tabs"}
            live_tab_results = replicate_all_live_tabs(date)
            for match_id, saved_files in live_tab_results.items():
                base_url = f"https://live.titan007.com/detail/{match_id}cn.htm"
                for fname in saved_files:
                    # 文件名形如 2929663cn.htm、2929663cn_players.htm 等
                    suffix = fname.replace(f"{match_id}cn", "").replace(".htm", "")
                    tab_url = f"{base_url[:-4]}{suffix}.htm" if suffix else base_url
                    url_map[tab_url] = f"{date}/live/detail/{fname}"
    except Exception as e:
        yield {"type": "warning", "message": f"复刻 live detail 标签页失败: {e}"}

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
    report["cancelled"] = _check_cancel() or report["pages_cancelled"] > 0
    data_store.save_report(date, report)

    # 同步到 docs/ 并更新 dates.json，供 GitHub Pages 直接访问
    try:
        data_store.sync_output_to_docs()
        yield {"type": "progress", "url": "__sync_docs__", "level": 0, "status": "synced"}
    except Exception as e:
        yield {"type": "warning", "message": f"同步到 docs/ 失败: {e}"}

    # 释放当前线程的浏览器实例，避免任务结束后 Chromium 进程残留
    try:
        from core.renderer import close_thread_browser
        close_thread_browser()
    except Exception:
        pass

    # 最终输出：列表页相对路径
    list_rel = url_map.get(list_url, f"{date}/index.html")
    yield {"type": "finish", "date": date, "list_page": list_rel, "report": report}


if __name__ == "__main__":
    import sys
    d = sys.argv[1] if len(sys.argv) > 1 else "2026-7-21"
    for ev in replicate_date(d):
        print(json.dumps(ev, ensure_ascii=False))
