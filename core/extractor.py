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
    # 方法1：从亚盘/欧赔/盘路/大小球/分析弹窗链接中提取真实 matchID
    match_patterns = [
        r'AsianOdds_n\.aspx\?id=(\d+)',
        r'OverDown_n\.aspx\?id=(\d+)',
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

        # 从行内亚/欧/析/大链接提取真实 match_id
        match_id = ""
        for a in row.find_all("a", href=True):
            m = re.search(
                r'(?:AsianOdds_n|OverDown_n)\.aspx\?id=(\d+)|oddslist/(\d+)\.htm|analysis/(\d+)\.htm',
                a["href"],
                re.I,
            )
            if m:
                match_id = m.group(1) or m.group(2) or m.group(3)
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
            if not _is_interesting_url(href):
                continue
            if _has_empty_param(href):
                continue
            # 按层级过滤，避免把全站导航都抓进来导致数量爆炸。
            # L1 只提取进入 L2 的入口（赛事类型页、单场亚/欧/析/大详情页），
            # 不提取 L1 列表页中零散的球队资料链接，避免数量失控。
            # L2 中仅单场分析页（析）里的主队/客队资料库链接需要继续下钻到 L3。
            if current_level == 1:
                if not _is_l2_url(href):
                    continue
            elif current_level == 2:
                if not (_is_analysis_page(base_url) and _is_l3_url(href)):
                    continue
            seen.add(href)
            links.append({
                "url": href,
                "type": "link",
                "text": a.get_text(strip=True)[:50],
                "level": current_level + 1,
            })

    return links


def _is_l2_url(url: str) -> bool:
    """L2 入口：赛事类型页、单场亚/欧/析/大详情页。"""
    parsed = urlparse(url)
    path = parsed.path.lower()
    qs = parse_qs(parsed.query)

    if parsed.netloc.lower() == "info.titan007.com" and re.match(r"/cn/CupMatch/\d+\.html", path, re.I):
        return True

    if "id" in qs and re.match(r"\d+$", qs["id"][0]):
        if "AsianOdds_n.aspx" in path or "OverDown_n.aspx" in path:
            return True

    if re.match(r"/oddslist/\d+\.htm", path, re.I):
        return True

    if re.match(r"/analysis/\d+cn\.htm", path, re.I) or re.match(r"/analysis/\d+\.htm", path, re.I):
        return True

    return False


def _is_l3_url(url: str) -> bool:
    """L3 入口：球队资料库页。"""
    parsed = urlparse(url)
    path = parsed.path.lower()

    # 球队资料汇总页
    if re.search(r"/team/Summary/\d+\.html", path, re.I):
        return True

    # info 域名的球队/联赛资料页
    if parsed.netloc.lower() == "info.titan007.com" and re.match(r"/cn/team/\d+\.html", path, re.I):
        return True

    return False


def _is_analysis_page(url: str) -> bool:
    """判断 URL 是否为单场分析页（析）。"""
    parsed = urlparse(url)
    path = parsed.path.lower()
    return "/analysis/" in path and path.endswith(".htm")


def extract_team_ids_from_analysis(html: str) -> dict:
    """从单场分析页（析）提取主队/客队资料库 ID。

    页面中主队、客队链接通常形如：
      <a href="//zq.titan007.com/cn/team/Summary/4075.html">济州SK(主)</a>
      <a href="//zq.titan007.com/cn/team/Summary/9945.html">江原FC</a>
    链接重写后也可能变成 ../team/4075.html。这里按顺序取前两个
    team/Summary/{id}.html 或 team/{id}.html 链接，第一个为主队，第二个为客队。
    """
    soup = BeautifulSoup(html, "html.parser")
    ids = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"team/(?:Summary/)?(\d+)\.html", href, re.I)
        if not m:
            continue
        ids.append(m.group(1))
        if len(ids) >= 2:
            break
    if len(ids) < 2:
        return {}
    return {"home_team_id": ids[0], "away_team_id": ids[1]}


def _is_interesting_url(url: str) -> bool:
    """判断 URL 是否属于需要复刻的范围。"""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()

    if host not in config.ALLOWED_HOSTS:
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
