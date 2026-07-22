#!/usr/bin/env python3
"""链接提取与结构化数据提取模块"""
import html as html_module
import re
import json
from urllib.parse import urljoin, urlparse, parse_qs

from bs4 import BeautifulSoup

from core.fetcher import normalize_url
import config


def extract_schedule_ids(html: str, base_url: str) -> list[str]:
    """从列表页提取所有比赛 ID（优先返回能打开详情页的真实 matchID）。"""
    ids = set()
    # 方法1：从亚盘/欧赔/盘路/分析弹窗链接中提取真实 matchID
    match_patterns = [
        r'AsianOdds_n\.aspx\?id=(\d+)',
        r'oddslist/(\d+)\.htm',
        r'panlu/(\d+)\.htm',
        r'openAnalysisPage\((\d+)\)',
    ]
    for pat in match_patterns:
        for m in re.finditer(pat, html, re.I):
            ids.add(m.group(1))

    # 方法2：兜底，从 row id / analysis 链接 / scheduleID 变量中提取
    if not ids:
        for m in re.finditer(r'id=["\']row_(\d+)["\']', html, re.I):
            ids.add(m.group(1))
        for pat in [r'analysis/(\d+)\.htm', r'scheduleID[=:](\d+)']:
            for m in re.finditer(pat, html, re.I):
                ids.add(m.group(1))

    return sorted(ids, key=int)


def extract_match_data(html: str, base_url: str) -> list[dict]:
    """从列表页提取比赛结构化数据。"""
    soup = BeautifulSoup(html, "lxml")
    matches = []
    seen = set()

    for row in soup.find_all(attrs={"id": re.compile(r"row_\d+")}):
        sid_match = re.search(r"row_(\d+)", row.get("id", ""))
        if not sid_match:
            continue
        schedule_id = sid_match.group(1)
        if schedule_id in seen:
            continue
        seen.add(schedule_id)

        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) < 9:
            continue

        # 从行内亚盘链接提取真实 match_id
        match_id = ""
        for a in row.find_all("a", href=True):
            m = re.search(r'AsianOdds_n\.aspx\?id=(\d+)', a["href"], re.I)
            if m:
                match_id = m.group(1)
                break

        # 主队、客队分别带链接，过滤掉只取球队名
        home_cell = cells[4]
        away_cell = cells[7]
        home_team = " ".join(a.get_text(strip=True) for a in home_cell.find_all("a") if a.get_text(strip=True))
        away_team = " ".join(a.get_text(strip=True) for a in away_cell.find_all("a") if a.get_text(strip=True))
        if not home_team:
            home_team = home_cell.get_text(strip=True)
        if not away_team:
            away_team = away_cell.get_text(strip=True)

        # 状态/开赛时间
        status = cells[3].get_text(strip=True)
        match_time = cells[2].get("title", "").replace("开赛时间：", "") if cells[2].get("title") else ""
        league = cells[1].get_text(strip=True)

        matches.append({
            "schedule_id": schedule_id,
            "match_id": match_id,
            "league": league,
            "match_time": match_time,
            "home_team": home_team,
            "away_team": away_team,
            "status": status,
        })

    return matches


def extract_links(html: str, base_url: str, current_level: int, max_level: int) -> list[dict]:
    """从页面中提取需要继续复刻的链接。"""
    if current_level >= max_level:
        return []

    soup = BeautifulSoup(html, "lxml")
    links = []
    seen = set()

    # 第一层列表页只提取比赛行内的链接，避免把整站导航都抓进来
    if current_level == 1:
        candidate_parents = soup.find_all(attrs={"id": re.compile(r"row_\d+")})
        if not candidate_parents:
            candidate_parents = soup.find_all("tr", attrs={"matchid": True})
    else:
        candidate_parents = [soup]

    # 提取 <a href="...">
    for parent in candidate_parents:
        for a in parent.find_all("a", href=True):
            raw = html_module.unescape(a["href"])
            href = normalize_url(raw, base_url)
            if not href or href in seen:
                continue
            # 只抓同源或已知的详情域
            if not _is_interesting_url(href):
                continue
            if _has_empty_param(href):
                continue
            seen.add(href)
            links.append({
                "url": href,
                "type": "link",
                "text": a.get_text(strip=True)[:50],
                "level": current_level + 1,
            })

    # 提取 onclick 中打开窗口/跳转的 URL（第一层列表页不提取，避免导航噪声）
    if current_level > 1:
        onclick_pattern = re.compile(
            r"(?:window\.open\(|location\.href\s*=|window\.location\s*=)\s*[\"']([^\"']+)[\"']",
            re.I,
        )
        for m in onclick_pattern.finditer(html):
            raw = html_module.unescape(m.group(1))
            href = normalize_url(raw, base_url)
            if not href or href in seen:
                continue
            if not _is_interesting_url(href):
                continue
            if _has_empty_param(href):
                continue
            seen.add(href)
            links.append({
                "url": href,
                "type": "onclick",
                "text": "",
                "level": current_level + 1,
            })

    return links


def _is_interesting_url(url: str) -> bool:
    """判断 URL 是否属于需要复刻的范围。"""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    allowed_hosts = {
        "cp.titan007.com",
        "zq.titan007.com",
        "vip.titan007.com",
        "op1.titan007.com",
        "data.titan007.com",
    }
    if host not in allowed_hosts:
        return False

    # 排除纯资源文件和已知非页面路径
    if path.endswith((".css", ".js", ".png", ".jpg", ".gif", ".ico", ".svg", ".woff", ".ttf")):
        return False

    # 排除已知非比赛详情页（北京单场、空 analysis 目录、彩票购买页等）
    if "beijingdanchang" in path or "/buy/lottery.aspx" in path:
        return False

    return True


def _has_empty_param(url: str) -> bool:
    """跳过关键参数为空的 URL，避免复刻到无意义的模板页。"""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    for key in ("id", "date", "lotteryid"):
        if key in qs and qs[key][0].strip() == "":
            return True
    # 详情页路径以 / 结尾视为空
    if parsed.path.lower().endswith(("/analysis/", "/oddslist/")):
        return True
    return False


def get_detail_urls(schedule_id: str) -> list[dict]:
    """根据 scheduleID 构造分析/亚盘/欧赔详情页 URL。"""
    urls = []
    for name, template in config.DETAIL_URL_TEMPLATES.items():
        urls.append({
            "url": template.format(id=schedule_id),
            "type": f"detail_{name}",
            "text": name,
            "schedule_id": schedule_id,
            "level": 2,
        })
    return urls
