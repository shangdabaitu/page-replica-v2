#!/usr/bin/env python3
"""复刻 live.titan007.com/detail/{id}cn.htm 的所有标签页状态。

支持批量处理某日期下所有已生成的 live/detail 页面，并自动改写标签导航链接。
该模块同时被独立脚本和主复刻管线调用。
"""
import json
import re
from io import BytesIO
from pathlib import Path

from PIL import Image
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
    "tech_same": "_tech_same",
    "jsq_50": "_jsq_50",
    "players": "_players",
    "text_live": "_text",
    "animation": "_animation",
    "hd": "_hd",
}


def _launch_browser():
    """启动浏览器，复用 thread-local 实例避免与视觉对比模块的 Playwright 冲突。"""
    from core.renderer import get_thread_browser
    browser = get_thread_browser()
    # 返回 (playwright, browser) 以保持兼容性，但 playwright 由 thread-local 管理
    p = getattr(getattr(browser, '_impl_object', None), '_loop', None)
    return p, browser


def _validate_html_content(html: str, min_len: int = 500) -> bool:
    """验证 HTML 内容是否有效（非空壳、非乱码）。

    PRD 要求：提取的 iframe HTML 内容长度必须 > 500 字符，
    且必须包含基本 HTML 结构标签，否则视为无效。
    """
    if not html or len(html) < min_len:
        return False
    # 检查是否包含基本 HTML 标签
    lower = html.lower()
    has_html_tag = "<html" in lower or "<body" in lower or "<table" in lower or "<div" in lower
    if not has_html_tag:
        return False
    # 检查是否是乱码（连续的非 ASCII 且无 HTML 结构）
    ascii_ratio = sum(1 for c in html[:2000] if ord(c) < 128) / min(len(html), 2000)
    if ascii_ratio < 0.15:
        return False
    return True


def _fetch_iframe_html(url: str) -> str:
    """抓取 iframe 页面内容并内联。

    PRD 约束：HTTP 抓取的 HTML 不保证包含 JS 渲染后的内容，
    此函数仅作为回退方案。主提取路径应从浏览器 contentDocument 获取。
    """
    data, ct = fetch_url(url, timeout=30)
    if data is None:
        print(f"  [WARN] HTTP 抓取失败: {url}")
        return ""
    html = decode_html(data, ct)
    if not _validate_html_content(html):
        print(f"  [WARN] HTTP 抓取内容无效或乱码: {url} (长度={len(html)})")
        return ""
    inlined = inline_page(html, url)
    if not _validate_html_content(inlined):
        print(f"  [WARN] 内联后内容无效: {url} (长度={len(inlined)})")
        return ""
    print(f"  [OK] HTTP 抓取 iframe 内容: {len(inlined)} 字符 ({url})")
    return inlined


def _render_states(page, match_id: str, player_html: str, text_live_html: str) -> tuple[dict[str, str], dict[str, bytes], str, str]:
    """渲染 live detail 页面的所有标签页状态。

    返回:
      - states: 各标签页的 HTML
      - pngs: 各标签页的截图 (PNG 字节)
      - rendered_player_html: 从浏览器 iframe 中提取的已渲染球员统计 HTML
      - rendered_text_live_html: 从浏览器 iframe 中提取的已渲染文字直播 HTML
    """
    url = f"https://live.titan007.com/detail/{match_id}cn.htm"
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(4000)

    states = {}
    pngs = {}
    # 初始为空，等待浏览器提取或 HTTP 回退
    rendered_player_html = ""
    rendered_text_live_html = ""
    # 如果 HTTP 抓取的内容有效，作为回退候选
    http_player_html = player_html if _validate_html_content(player_html) else ""
    http_text_live_html = text_live_html if _validate_html_content(text_live_html) else ""

    # 先保存默认状态（无论页面是否有完整标签结构）
    states["match_important"] = page.content()
    pngs["match_important"] = page.screenshot(full_page=False, type="png")

    # 检查页面是否具备标签切换所需的 DOM 结构
    has_tabs = page.evaluate("""() => {
        return !!(document.getElementById('matchData') &&
                  document.getElementById('menu0') &&
                  document.getElementById('menu1') &&
                  document.getElementById('menu2'));
    }""")
    if not has_tabs:
        print(f"  [WARN] {match_id} 没有完整标签页结构，仅保存默认页")
        return states, pngs, rendered_player_html, rendered_text_live_html

    # 辅助函数占位，避免下方引用报错（has_tabs=False 时不会执行到这里）

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
        pngs["match_detail"] = page.screenshot(full_page=False, type="png")

    # 2b. 技统数据 - 同主客（子标签页，默认"全部"已在 match_important 中捕获）
    # 先切回重要事件视图，确保能访问技统数据区域
    _safe_switch("""() => {
        if (typeof ShowIframe === 'function') ShowIframe(0);
        if (typeof ShowEventDetail === 'function') ShowEventDetail(0);
    }""")
    if _safe_switch("""() => {
        if (typeof changeTechCount === 'function') changeTechCount(2);
    }""", wait_ms=800):
        states["tech_same"] = page.content()
        pngs["tech_same"] = page.screenshot(full_page=False, type="png")
        # 切回"全部"
        _safe_switch("""() => {
            if (typeof changeTechCount === 'function') changeTechCount(1);
        }""", wait_ms=300)

    # 2c. 进失球概率 - 近50场（子标签页，默认"近30场"已在 match_important 中捕获）
    if _safe_switch("""() => {
        if (typeof changeJsq === 'function') changeJsq(2);
    }""", wait_ms=800):
        states["jsq_50"] = page.content()
        pngs["jsq_50"] = page.screenshot(full_page=False, type="png")
        # 切回"近30场"
        _safe_switch("""() => {
            if (typeof changeJsq === 'function') changeJsq(1);
        }""", wait_ms=300)

    # 3. 球员统计：切换到该标签，等待 iframe 加载完成
    # PRD 要求：必须从浏览器渲染后的 iframe contentDocument 提取，禁止仅用 HTTP 抓取
    # 关键问题：原始页面的 playerTechIframe 由 JS 动态创建，初始 DOM 中不存在，
    # 直接调用 ShowIframe(1) 会因访问 null 元素而崩溃。
    # 解决方案：手动创建 iframe 并设置 src，或使用直接导航方式提取。
    player_url = f"https://live.titan007.com/PlayerTech.aspx?ID={match_id}&l=0"
    try:
        # 方式 1：手动创建 iframe 并加载 PlayerTech.aspx
        page.evaluate(f"""() => {{
            // 显示球员统计区域，隐藏其他区域
            var md = document.getElementById('matchData');
            var pd = document.getElementById('playerTechData');
            var td = document.getElementById('textLiveData');
            if (md) md.style.display = 'none';
            if (td) td.style.display = 'none';
            if (pd) pd.style.display = '';

            // 更新菜单高亮
            for (var i = 0; i < 3; i++) {{
                var m = document.getElementById('menu' + i);
                if (m) m.className = '';
            }}
            var m1 = document.getElementById('menu1');
            if (m1) m1.className = 'ontab';

            // 如果 iframe 不存在，创建它
            var iframe = document.getElementById('playerTechIframe');
            if (!iframe && pd) {{
                iframe = document.createElement('iframe');
                iframe.id = 'playerTechIframe';
                iframe.name = 'ifLive';
                iframe.setAttribute('allowfullscreen', 'true');
                iframe.setAttribute('frameborder', '0');
                iframe.setAttribute('scrolling', 'no');
                iframe.style.width = '1080px';
                iframe.style.height = '1470px';
                pd.appendChild(iframe);
            }}
            if (iframe) {{
                iframe.src = '{player_url}';
            }}
        }}""")
        print(f"  [INFO] 已创建/设置 playerTechIframe src: {player_url}")

        # 等待 iframe 加载完成（最多 10 秒）
        iframe_loaded = False
        for _ in range(10):
            page.wait_for_timeout(1000)
            loaded = page.evaluate("""() => {
                try {
                    var iframe = document.getElementById('playerTechIframe');
                    if (!iframe || !iframe.contentDocument) return false;
                    var doc = iframe.contentDocument;
                    return !!(doc && doc.body && doc.body.innerHTML.length > 100);
                } catch(e) { return false; }
            }""")
            if loaded:
                iframe_loaded = True
                print(f"  [OK] playerTechIframe 内容加载完成")
                break
        if not iframe_loaded:
            print(f"  [WARN] playerTechIframe 等待 10s 后仍未加载内容")

        # 截图（无论 iframe 是否加载成功，都保存当前页面状态）
        states["players"] = page.content()
        pngs["players"] = page.screenshot(full_page=False, type="png")

        # 从浏览器 iframe contentDocument 提取渲染后的完整 DOM
        try:
            iframe_html = page.evaluate("""() => {
                const iframe = document.getElementById('playerTechIframe');
                if (iframe && iframe.contentDocument) {
                    const doc = iframe.contentDocument;
                    if (doc.documentElement) {
                        return '<!DOCTYPE html>' + doc.documentElement.outerHTML;
                    }
                }
                return '';
            }""")
            if iframe_html and _validate_html_content(iframe_html):
                # 内联 iframe 内容中的外部资源
                inlined = inline_page(iframe_html, player_url)
                if _validate_html_content(inlined):
                    rendered_player_html = inlined
                    print(f"  [OK] 从浏览器提取球员统计 iframe 内容: {len(inlined)} 字符")
                else:
                    rendered_player_html = iframe_html
                    print(f"  [OK] 从浏览器提取球员统计 iframe 内容(未内联): {len(iframe_html)} 字符")
            elif iframe_html and len(iframe_html) > 500:
                print(f"  [WARN] 球员统计 iframe 内容长度 {len(iframe_html)} 但验证未通过")
            else:
                print(f"  [WARN] 球员统计 iframe 内容为空或过短 ({len(iframe_html)} 字符)")
        except Exception as e:
            print(f"  [WARN] 提取球员统计 iframe 内容失败: {e}")

        # 方式 2：如果浏览器 iframe 提取失败，直接导航到 PlayerTech.aspx
        if not _validate_html_content(rendered_player_html):
            print(f"  [INFO] 浏览器 iframe 提取失败，尝试直接导航到 PlayerTech.aspx...")
            try:
                player_page = page.context.new_page()
                player_page.goto(player_url, wait_until="domcontentloaded", timeout=30000)
                player_page.wait_for_timeout(3000)
                direct_html = player_page.content()
                player_page.close()
                if _validate_html_content(direct_html):
                    inlined = inline_page(direct_html, player_url)
                    if _validate_html_content(inlined):
                        rendered_player_html = inlined
                        print(f"  [OK] 直接导航提取球员统计内容: {len(inlined)} 字符")
                    else:
                        rendered_player_html = direct_html
                        print(f"  [OK] 直接导航提取球员统计内容(未内联): {len(direct_html)} 字符")
                else:
                    print(f"  [WARN] 直接导航内容验证未通过: {len(direct_html)} 字符")
            except Exception as e2:
                print(f"  [WARN] 直接导航提取也失败: {e2}")

        # 方式 3：最终回退到 HTTP 抓取版本
        if not _validate_html_content(rendered_player_html):
            if _validate_html_content(http_player_html):
                rendered_player_html = http_player_html
                print(f"  [FALLBACK] 使用 HTTP 抓取的球员统计内容: {len(http_player_html)} 字符")
            else:
                rendered_player_html = ""
                print(f"  [ERROR] 球员统计内容提取完全失败，srcdoc 将为空")

    except Exception as e:
        print(f"  [ERROR] 球员统计标签处理失败: {e}")
        # 回退到 HTTP 抓取
        if _validate_html_content(http_player_html):
            rendered_player_html = http_player_html
            print(f"  [FALLBACK] 使用 HTTP 抓取的球员统计内容: {len(http_player_html)} 字符")
        # 仍然保存当前页面状态
        try:
            states["players"] = page.content()
            pngs["players"] = page.screenshot(full_page=False, type="png")
        except:
            pass

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
        }}""", text_live_html if text_live_html else "<html><body></body></html>")
        page.wait_for_timeout(1000)

        # 从浏览器 iframe 中提取已渲染的文字直播内容
        try:
            tl_html = page.evaluate("""() => {
                const iframe = document.getElementById('textLiveIframe');
                if (iframe && iframe.contentDocument) {
                    const doc = iframe.contentDocument;
                    if (doc.documentElement) {
                        return '<!DOCTYPE html>' + doc.documentElement.outerHTML;
                    }
                }
                return '';
            }""")
            if tl_html and _validate_html_content(tl_html):
                rendered_text_live_html = tl_html
                print(f"  [OK] 从浏览器提取文字直播 iframe 内容: {len(tl_html)} 字符")
            elif tl_html and len(tl_html) > 500:
                print(f"  [WARN] 文字直播 iframe 内容长度 {len(tl_html)} 但验证未通过")
            else:
                print(f"  [WARN] 文字直播 iframe 内容为空或过短 ({len(tl_html)} 字符)")
        except Exception as e:
            print(f"  [WARN] 提取文字直播 iframe 内容失败: {e}")

        # 回退到 HTTP 抓取版本
        if not _validate_html_content(rendered_text_live_html):
            if _validate_html_content(http_text_live_html):
                rendered_text_live_html = http_text_live_html
                print(f"  [FALLBACK] 使用 HTTP 抓取的文字直播内容: {len(http_text_live_html)} 字符")
            else:
                rendered_text_live_html = ""
                print(f"  [ERROR] 文字直播内容提取完全失败，srcdoc 将为空")

        states["text_live"] = page.content()
        pngs["text_live"] = page.screenshot(full_page=False, type="png")
    except Exception as e:
        print(f"  [WARN] 文字直播状态失败: {e}")

    # 5. 动画直播
    if _safe_switch("""() => {
        if (typeof changeLive === 'function') changeLive(1);
    }""", wait_ms=1500):
        states["animation"] = page.content()
        pngs["animation"] = page.screenshot(full_page=False, type="png")

    # 6. 高清直播
    if _safe_switch("""() => {
        if (typeof changeLive === 'function') changeLive(4);
    }""", wait_ms=1500):
        states["hd"] = page.content()
        pngs["hd"] = page.screenshot(full_page=False, type="png")

    return states, pngs, rendered_player_html, rendered_text_live_html


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

    # 子标签页按钮：技统数据 全部/同主客、进失球概率 近30场/近50场
    subtab_links = {
        "changeTechCount(2)": f"{base}_tech_same.htm?{cache_buster}",
        "changeTechCount(1)": f"{base}.htm?{cache_buster}",
        "changeJsq(2)": f"{base}_jsq_50.htm?{cache_buster}",
        "changeJsq(1)": f"{base}.htm?{cache_buster}",
    }
    for span in soup.find_all("span", onclick=True):
        onclick_val = span.get("onclick", "")
        for js_call, href in subtab_links.items():
            if js_call in onclick_val:
                span["onclick"] = f"window.location.href='{href}'"
                style = span.get("style") or ""
                if "cursor" not in style:
                    span["style"] = style + ";cursor:pointer;" if style else "cursor:pointer;"
                break

    return str(soup)


def _inline_live_iframes(html: str, player_html: str, text_live_html: str) -> str:
    """把 playerTech / textLive iframe 替换为 srcdoc 内联内容，确保静态打开可见。

    PRD 约束：仅当 iframe 内容通过验证时才设置 srcdoc，
    防止乱码或空内容被写入复刻页面。
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    player_iframe = soup.find("iframe", id="playerTechIframe")
    if player_iframe and _validate_html_content(player_html):
        player_iframe["srcdoc"] = player_html
        player_iframe["src"] = "about:blank"
        if player_iframe.get("height"):
            player_iframe["style"] = (player_iframe.get("style") or "") + ";height:1470px;"
        print(f"  [OK] 球员统计 srcdoc 已设置: {len(player_html)} 字符")
    elif player_iframe:
        # 内容无效时不设置 srcdoc，保留原 src（如果有）或设为 about:blank
        if not player_iframe.get("src"):
            player_iframe["src"] = "about:blank"
        print(f"  [WARN] 球员统计内容未通过验证，不设置 srcdoc")

    text_iframe = soup.find("iframe", id="textLiveIframe")
    if text_iframe and _validate_html_content(text_live_html):
        text_iframe["srcdoc"] = text_live_html
        text_iframe["src"] = "about:blank"
        text_iframe["style"] = (text_iframe.get("style") or "") + ";height:1000px;"
        print(f"  [OK] 文字直播 srcdoc 已设置: {len(text_live_html)} 字符")
    elif text_iframe:
        if not text_iframe.get("src"):
            text_iframe["src"] = "about:blank"
        print(f"  [WARN] 文字直播内容未通过验证，不设置 srcdoc")

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
                player_html: str = "", text_live_html: str = "",
                source_png: bytes | None = None) -> Path:
    from core.replicator import _freeze_rendered_page
    from compare import visual

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

    # 视觉对比：用渲染时捕获的源截图与复刻页面做对比
    if source_png is not None:
        try:
            source_img = Image.open(BytesIO(source_png))
            compare_result = visual.compare_with_source_image(
                source_img,
                f"{base_url}#{suffix}",
                output_path,
                output_dir=output_dir / "diff",
                source_html=html,
            )
            print(f"  [COMPARE] {match_id}{suffix}: "
                  f"diff={compare_result.get('diff_ratio')}, "
                  f"status={compare_result.get('status')}, "
                  f"dom={compare_result.get('dom_status')}, "
                  f"ext={compare_result.get('ext_status')}, "
                  f"console={compare_result.get('console_status')}")
        except Exception as e:
            print(f"  [WARN] 标签页视觉对比失败 {match_id}{suffix}: {e}")

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
            states, pngs, rendered_player_html, rendered_text_live_html = _render_states(page, match_id, player_html, text_live_html)
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
                               player_html=rendered_player_html, text_live_html=rendered_text_live_html,
                               source_png=pngs.get(state_name))
            saved.append(path.name)
            print(f"  saved: {path}")
        return saved
    finally:
        if close_browser and own_browser:
            # 仅关闭 context，不关闭 browser（thread-local 共享实例由调用方管理）
            try:
                context.close()
            except Exception:
                pass


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
        # 仅关闭 context，不关闭共享的 thread-local browser
        try:
            context.close()
        except Exception:
            pass

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
