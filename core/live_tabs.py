#!/usr/bin/env python3
"""复刻 live.titan007.com/detail/{id}cn.htm 的所有标签页状态。

支持批量处理某日期下所有已生成的 live/detail 页面，并自动改写标签导航链接。
该模块同时被独立脚本和主复刻管线调用。
"""
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

import config
from core.fetcher import fetch_url, decode_html
from core.inliner import inline_page
from core.simplifier import simplify_html
from core.watermark import inject_watermark

# 延迟导入 core.replicator，避免循环依赖


TAB_SUFFIXES = {
    "match_important": "",
    "match_detail": "_event_detail",
    "players": "_players",
    "text_live": "_text",
    "animation": "_animation",
    "hd": "_hd",
}


def _launch_browser():
    from core.renderer import _find_chrome

    kwargs = {"args": ["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"]}
    chrome = _find_chrome()
    if chrome:
        kwargs["executable_path"] = chrome
    p = sync_playwright().start()
    browser = p.chromium.launch(**kwargs)
    return p, browser


def _fetch_iframe_html(url: str) -> str:
    """抓取 iframe 页面内容并内联。"""
    data, ct = fetch_url(url, timeout=30)
    if data is None:
        return f"<!-- fetch failed: {url} -->"
    html = decode_html(data, ct)
    return inline_page(html, url)


def _render_states(page, match_id: str, player_html: str, text_live_html: str) -> dict[str, str]:
    url = f"https://live.titan007.com/detail/{match_id}cn.htm"
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(4000)

    states = {}

    # 先保存默认状态（无论页面是否有完整标签结构）
    states["match_important"] = page.content()

    # 检查页面是否具备标签切换所需的 DOM 结构
    has_tabs = page.evaluate("""() => {
        return !!(document.getElementById('matchData') &&
                  document.getElementById('menu0') &&
                  document.getElementById('menu1') &&
                  document.getElementById('menu2'));
    }""")
    if not has_tabs:
        print(f"  [WARN] {match_id} 没有完整标签页结构，仅保存默认页")
        return states

    # 辅助函数：安全执行标签切换
    def _safe_switch(js_code: str, wait_ms: int = 800):
        try:
            page.evaluate(js_code)
            page.wait_for_timeout(wait_ms)
            return True
        except Exception as e:
            print(f"  [WARN] 标签切换失败: {e}")
            return False

    # 2. 现场分析 - 详细事件
    if _safe_switch("""() => {
        if (typeof ShowIframe === 'function') ShowIframe(0);
        if (typeof ShowEventDetail === 'function') ShowEventDetail(1);
    }"""):
        states["match_detail"] = page.content()

    # 3. 球员统计：切换到该标签，等待 iframe 加载完成
    if _safe_switch("""() => {
        if (typeof ShowIframe === 'function') ShowIframe(1);
    }""", wait_ms=3000):
        states["players"] = page.content()

    # 4. 文字直播：手动创建 iframe 并加载 textLive.aspx
    try:
        page.evaluate(f"""(html) => {{
            if (typeof ShowIframe === 'function') ShowIframe(0);
            const container = document.getElementById('textLiveData');
            if (container) {{
                container.innerHTML = '<iframe id="textLiveIframe" name="ifLive" allowfullscreen="true" frameborder="0" style="width:1080px;height:1000px" scrolling="no"></iframe>';
                const iframe = document.getElementById('textLiveIframe');
                if (iframe) {{
                    iframe.contentDocument.open();
                    iframe.contentDocument.write(html);
                    iframe.contentDocument.close();
                }}
            }}
            if (typeof ShowIframe === 'function') ShowIframe(2);
        }}""", text_live_html)
        page.wait_for_timeout(1000)
        states["text_live"] = page.content()
    except Exception as e:
        print(f"  [WARN] 文字直播状态失败: {e}")

    # 5. 动画直播
    if _safe_switch("""() => {
        if (typeof changeLive === 'function') changeLive(1);
    }""", wait_ms=1500):
        states["animation"] = page.content()

    # 6. 高清直播
    if _safe_switch("""() => {
        if (typeof changeLive === 'function') changeLive(4);
    }""", wait_ms=1500):
        states["hd"] = page.content()

    return states


def _patch_live_tab_links(html: str, match_id: str) -> str:
    """把 live detail 页内各标签的 JS 切换改成本地文件链接，并加缓存破坏参数。"""
    from bs4 import BeautifulSoup

    cache_buster = config.CACHE_BUSTER
    base = f"./{match_id}cn"
    links = {
        "menu0": f"{base}.htm?{cache_buster}",
        "menu1": f"{base}_players.htm?{cache_buster}",
        "menu2": f"{base}_text.htm?{cache_buster}",
        "tvLive1": f"{base}_animation.htm?{cache_buster}",
        "tvLive2": f"{base}_hd.htm?{cache_buster}",
        "eventMenu0": f"{base}.htm?{cache_buster}",
        "eventMenu1": f"{base}_event_detail.htm?{cache_buster}",
    }

    soup = BeautifulSoup(html, "html.parser")
    for el_id, href in links.items():
        el = soup.find(id=el_id)
        if el is None:
            continue
        if el.name == "a":
            el["href"] = href
            if "onclick" in el.attrs:
                del el["onclick"]
        else:
            el["onclick"] = f"window.location.href='{href}'"
            style = el.get("style") or ""
            if "cursor" not in style:
                el["style"] = style + ";cursor:pointer;" if style else "cursor:pointer;"

    # 顶部"现场分析"A标签
    for a in soup.find_all("a", href=True):
        if a.get("href") == "javascript:void(0);" and "现场分析" in (a.text or ""):
            a["href"] = f"{base}.htm?{cache_buster}"
            if "onclick" in a.attrs:
                del a["onclick"]

    return str(soup)


def _inline_live_iframes(html: str, player_html: str, text_live_html: str) -> str:
    """把 playerTech / textLive iframe 替换为 srcdoc 内联内容，确保静态打开可见。"""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    player_iframe = soup.find("iframe", id="playerTechIframe")
    if player_iframe and player_html:
        player_iframe["srcdoc"] = player_html
        player_iframe["src"] = "about:blank"
        if player_iframe.get("height"):
            player_iframe["style"] = (player_iframe.get("style") or "") + ";height:1470px;"

    text_iframe = soup.find("iframe", id="textLiveIframe")
    if text_iframe and text_live_html:
        text_iframe["srcdoc"] = text_live_html
        text_iframe["src"] = "about:blank"
        text_iframe["style"] = (text_iframe.get("style") or "") + ";height:1000px;"

    return str(soup)


def _inject_live_subtab_scripts(html: str) -> str:
    """注入 live detail 页面子标签页和折叠区块所需的 JS 函数。

    _freeze_rendered_page 会删除所有 <script>，但子标签页（技统数据 全部/同主客、
    进失球概率 近30场/近50场）和折叠区块（ShowTabContent）的切换依赖这些函数。
    此函数在冻结后重新注入纯 JS 实现（不依赖 jQuery）。
    """
    from bs4 import BeautifulSoup

    script_code = """
function changeTechCount(t){
    var all=document.getElementById('techCountAll');
    var same=document.getElementById('techCountSame');
    if(!all||!same)return;
    if(t==1){all.style.display='';same.style.display='none';}
    else{all.style.display='none';same.style.display='';}
}
function changeJsq(t){
    var j30=document.getElementById('jsq_30');
    var j50=document.getElementById('jsq_50');
    if(!j30||!j50)return;
    if(t==1){j30.style.display='';j50.style.display='none';}
    else{j30.style.display='none';j50.style.display='';}
}
function ShowTabContent(e,id){
    var isShow=false;
    if(e.className.indexOf('up')>0)isShow=true;
    e.className=isShow?'arrow':'arrow up';
    var el=document.getElementById(id);
    if(el)el.style.display=isShow?'':'none';
}
function ShowIframe(type){
    for(var i=0;i<3;i++){
        var m=document.getElementById('menu'+i);
        if(m)m.className='';
    }
    var md=document.getElementById('matchData');
    var pd=document.getElementById('playerTechData');
    var td=document.getElementById('textLiveData');
    if(md)md.style.display='none';
    if(pd)pd.style.display='none';
    if(td)td.style.display='none';
    var cm=document.getElementById('menu'+type);
    if(cm)cm.className='ontab';
    if(type==0&&md)md.style.display='';
    else if(type==1&&pd)pd.style.display='';
    else if(type==2&&td)td.style.display='';
    else{if(cm)cm.className='ontab';if(md)md.style.display='';}
}
function ShowEventDetail(type){
    var em0=document.getElementById('eventMenu0');
    var em1=document.getElementById('eventMenu1');
    var ted=document.getElementById('teamEventDiv');
    var tedd=document.getElementById('teamEventDetailDiv');
    if(type==0){
        if(em0)em0.className='ontab';
        if(em1)em1.className='';
        if(ted)ted.style.display='';
        if(tedd)tedd.style.display='none';
    }else{
        if(em1)em1.className='ontab';
        if(em0)em0.className='';
        if(ted)ted.style.display='none';
        if(tedd)tedd.style.display='';
    }
}
function changeLive(type){
    var fl=document.getElementById('flashLive');
    var tv=document.getElementById('tvLive');
    var tv1=document.getElementById('tvLive1');
    var tv2=document.getElementById('tvLive2');
    if(type==1){
        if(fl)fl.style.display='';
        if(tv)tv.style.display='none';
        if(tv1)tv1.className='ontab';
        if(tv2)tv2.className='';
    }else if(type==4){
        if(fl)fl.style.display='none';
        if(tv)tv.style.display='';
        if(tv2)tv2.className='ontab';
        if(tv1)tv1.className='';
    }
}
"""

    soup = BeautifulSoup(html, "html.parser")
    script_tag = soup.new_tag("script")
    script_tag.string = script_code
    if soup.body:
        soup.body.append(script_tag)
    elif soup.html:
        soup.html.append(script_tag)
    else:
        soup.append(script_tag)
    return str(soup)


def _save_state(html: str, match_id: str, suffix: str, base_url: str,
                output_dir: Path, docs_dir: Path,
                player_html: str = "", text_live_html: str = "") -> Path:
    from core.replicator import _freeze_rendered_page

    rel_path = f"live/detail/{match_id}cn{suffix}.htm"
    output_path = output_dir / rel_path
    docs_path = docs_dir / rel_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.parent.mkdir(parents=True, exist_ok=True)

    inlined = inline_page(html, base_url)
    frozen = _freeze_rendered_page(inlined)
    from core.js_injector import inject_page_scripts
    injected = inject_page_scripts(frozen, base_url)
    simplified = simplify_html(injected)
    marked = inject_watermark(simplified)
    patched = _patch_live_tab_links(marked, match_id)
    final = _inline_live_iframes(patched, player_html, text_live_html)

    output_path.write_text(final, encoding="utf-8")
    docs_path.write_text(final, encoding="utf-8")
    return output_path


def _collect_live_match_ids(output_dir: Path) -> list[str]:
    """从已生成的 live/detail 主页面收集所有 match_id。"""
    live_dir = output_dir / "live" / "detail"
    if not live_dir.exists():
        return []
    ids = set()
    for p in live_dir.glob("*cn.htm"):
        m = re.match(r"(\d+)cn\.htm$", p.name)
        if m:
            ids.add(m.group(1))
    return sorted(ids, key=int)


def _build_url_map(date: str) -> dict[str, str]:
    """从 meta.json 与已生成的 live detail 标签页构建 url_map（原始 URL -> 本地相对路径）。"""
    base_dir = config.OUTPUT_DIR / date
    url_map = {}

    meta_path = base_dir / "meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            for page in meta.get("pages", []):
                url = page.get("url")
                rel = page.get("rel_path")
                if url and rel:
                    url_map[url] = rel
        except Exception as e:
            print(f"[WARN] 读取 meta.json 失败: {e}")

    # 登记 live detail 标签页
    live_dir = base_dir / "live" / "detail"
    if live_dir.exists():
        for p in live_dir.glob("*cn*.htm"):
            m = re.match(r"(\d+)cn(.*)\.htm$", p.name)
            if not m:
                continue
            match_id, suffix = m.group(1), m.group(2)
            rel = str(p.relative_to(config.OUTPUT_DIR))
            url_map[f"https://live.titan007.com/detail/{match_id}cn{suffix}.htm"] = rel

    return url_map


def replicate_live_tabs(match_id: str, output_dir: Path, docs_dir: Path,
                        browser=None, context=None, close_browser: bool = True) -> list[str]:
    """复刻单个 live detail 页面的所有标签页。

    当传入 browser/context 时复用，避免重复启动 Chromium。
    """
    print(f"[INFO] Rendering live detail tabs for {match_id}")

    player_url = f"https://live.titan007.com/PlayerTech.aspx?ID={match_id}&l=0"
    text_live_url = f"https://live.titan007.com/textLive.aspx?id={match_id}&l=0"
    player_html = _fetch_iframe_html(player_url)
    text_live_html = _fetch_iframe_html(text_live_url)

    own_browser = False
    try:
        if browser is None:
            p, browser = _launch_browser()
            own_browser = True
        if context is None:
            context = browser.new_context(
                viewport={"width": 1440, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            context.set_extra_http_headers({"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})
        page = context.new_page()

        try:
            states = _render_states(page, match_id, player_html, text_live_html)
        finally:
            page.close()

        base_url = f"https://live.titan007.com/detail/{match_id}cn.htm"
        saved = []
        for state_name, suffix in TAB_SUFFIXES.items():
            if state_name not in states:
                print(f"  [SKIP] {match_id} 缺少 {state_name} 状态")
                continue
            path = _save_state(states[state_name], match_id, suffix, base_url,
                               output_dir, docs_dir,
                               player_html=player_html, text_live_html=text_live_html)
            saved.append(path.name)
            print(f"  saved: {path}")
        return saved
    finally:
        if close_browser and own_browser:
            context.close()
            browser.close()
            p.stop()


def replicate_all_live_tabs(date: str) -> dict[str, list[str]]:
    """批量复刻指定日期下所有 live detail 页的标签页。"""
    output_dir = config.OUTPUT_DIR / date
    docs_dir = Path("/workspace/page-replica-v2/docs") / date

    match_ids = _collect_live_match_ids(output_dir)
    print(f"[INFO] Found {len(match_ids)} live detail pages to process for {date}")

    p, browser = _launch_browser()
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    )
    context.set_extra_http_headers({"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"})

    results = {}
    try:
        for match_id in match_ids:
            try:
                saved = replicate_live_tabs(match_id, output_dir, docs_dir,
                                            browser=browser, context=context, close_browser=False)
                results[match_id] = saved
            except Exception as e:
                print(f"[ERROR] Failed to replicate tabs for {match_id}: {e}")
                results[match_id] = []
    finally:
        context.close()
        browser.close()
        p.stop()

    return results


def rewrite_live_tab_links(date: str) -> None:
    """对指定日期下所有 live detail 页面做最终链接重写，确保标签页互相跳转且外部链接本地化。"""
    from core.replicator import _rewrite_links

    base_dir = config.OUTPUT_DIR / date
    docs_dir = Path("/workspace/page-replica-v2/docs") / date
    url_map = _build_url_map(date)

    live_dir = base_dir / "live" / "detail"
    if not live_dir.exists():
        return

    for p in live_dir.glob("*cn*.htm"):
        m = re.match(r"(\d+)cn.*\.htm$", p.name)
        if not m:
            continue
        match_id = m.group(1)
        base_url = f"https://live.titan007.com/detail/{match_id}cn.htm"
        try:
            html = p.read_text(encoding="utf-8")
            html = _rewrite_links(html, base_url, p, url_map)
            p.write_text(html, encoding="utf-8")
            # 同步 docs
            docs_path = docs_dir / p.relative_to(base_dir)
            docs_path.parent.mkdir(parents=True, exist_ok=True)
            docs_path.write_text(html, encoding="utf-8")
        except Exception as e:
            print(f"[WARN] 重写链接失败: {p} -> {e}")
