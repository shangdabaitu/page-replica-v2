#!/usr/bin/env python3
"""为各类复刻页面注入所需的 JavaScript 交互函数。

_freeze_rendered_page 会删除所有 <script>，但页面中的交互元素
（折叠区块、子标签页、筛选按钮等）依赖这些函数才能正常工作。
本模块根据页面类型注入纯 JS 实现（不依赖 jQuery），确保静态
浏览时交互元素可用。
"""
from urllib.parse import urlparse
import re
from bs4 import BeautifulSoup


def _detect_page_type(html: str, url: str = "") -> str:
    """根据 HTML 内容和 URL 判断页面类型。"""
    soup = BeautifulSoup(html, "html.parser")

    # 通过 URL 判断
    if url:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.lower()

        if host == "live.titan007.com" and "/detail/" in path:
            return "live_detail"
        if host == "zq.titan007.com" and "/analysis/" in path:
            return "analysis"
        if host in ("info.titan007.com", "zq.titan007.com") and "/cn/team/" in path:
            return "team"
        if host == "info.titan007.com" and "/cn/" in path and (
            "/subleague/" in path or "/cupmatch/" in path or "/league/" in path
        ):
            return "league"
        if host == "vip.titan007.com" and "asianodds" in path:
            return "asianodds"
        if host == "vip.titan007.com" and "overdown" in path:
            return "overdown"
        if host == "vip.titan007.com" and "corner" in path:
            return "corner"
        if host in ("op1.titan007.com", "1x2.titan007.com") and "/oddslist/" in path:
            return "oddslist"

    # 通过 HTML 内容判断（后备）
    title = soup.find("title")
    title_text = title.get_text() if title else ""

    if soup.find("div", id="matchData") and soup.find("li", id="menu0"):
        return "live_detail"
    if soup.find("ul", id="odds_menu") and soup.find("h2", class_="fx_title2"):
        return "analysis"
    # 列表页：包含 openAnalysisPage 或比赛行表格
    if soup.find("table", id="MatchTable") or (
        soup.find("a", attrs={"onclick": re.compile(r"openAnalysisPage")}) is not None
        and soup.find("table", id="MatchTable") is not None
    ):
        return "list"
    if soup.find("li", class_="nav_selected") and soup.find("li", class_="nav_unselected"):
        # 球队页有 nav_selected/nav_unselected
        if soup.find("div", id="teamData") or soup.find("div", class_="team-nav"):
            return "team"
        # 联赛页也有类似的导航
        return "league"
    if soup.find("table", id="oddsTable") or (
        "AsianOdds" in title_text or "亚盘" in title_text
    ):
        return "asianodds"
    if soup.find("table", class_="oddsTable") or "oddslist" in title_text.lower():
        return "oddslist"

    return "generic"


def _build_common_scripts() -> str:
    """所有页面通用的 JS 函数（无操作占位符）。"""
    return """
function openAjaxLoginWin(){return false;}
function gotoloadPage(){return false;}
function ChangeLang(t){return false;}
function changeLang(t){return false;}
function SelectTimeZone(url){return false;}
function vipBannerCheck(){return false;}
function vipSubscribe(t){return false;}
function UserEdit(){return false;}
function miniopen(url){window.open(url);return false;}
"""


def _build_list_scripts() -> str:
    """列表页（L1）所需的 JS 函数。"""
    return """
function AddMutiple(n){return false;}
function BuyAreaLogin(){return false;}
function CheckGoalNotify(checked){return false;}
function CheckScoreAlert(checked){return false;}
function ClearSelect(){return false;}
function DateChange(t){
    var links=document.querySelectorAll('a[onclick*="DateChange"]');
    for(var i=0;i<links.length;i++){links[i].style.fontWeight='normal';}
    if(t==1){
        var el=document.querySelector('a[onclick*="DateChange(1)"]');
        if(el)el.style.fontWeight='bold';
    }else{
        var el=document.querySelector('a[onclick*="DateChange(2)"]');
        if(el)el.style.fontWeight='bold';
    }
    return false;
}
function MM_showHideLayers(id,sub,action){
    var el=document.getElementById(id);
    if(el){
        el.style.display=action==='hide'?'none':'';
    }
    return false;
}
function ReverseSclass(flag){return false;}
function SelectKind2(t){return false;}
function SelectRadio(name,el){
    var inputs=document.querySelectorAll('input[name="'+name+'"]');
    for(var i=0;i<inputs.length;i++){
        inputs[i].checked=false;
    }
    if(el)el.checked=true;
    return false;
}
function SetLanguage(t){return false;}
function ShowOddsWinow(url,name,evt){
    var el=document.getElementById(name);
    if(el){
        el.style.display=el.style.display==='none'?'':'none';
    }
    return false;
}
function ShowOverSale(checked){return false;}
function ShowRedCard(checked){
    var rows=document.querySelectorAll('tr');
    for(var i=0;i<rows.length;i++){
        if(checked){
            var hasRed=rows[i].querySelector('img[src*="red"],img[src*="Red"]');
            if(hasRed)rows[i].style.display='';
        }
    }
    return false;
}
function ShowTeamOrder(checked){return false;}
function hideRow(rowId){
    var el=document.getElementById(rowId);
    if(el)el.style.display=el.style.display==='none'?'':'none';
    return false;
}
function isShowSclass(day,display){
    var rows=document.querySelectorAll('tr[id*="'+day+'"]');
    for(var i=0;i<rows.length;i++){
        rows[i].style.display=display;
    }
    var ah=document.getElementById('ah_'+day);
    var as_=document.getElementById('as_'+day);
    if(ah)ah.style.display=display==='none'?'':'none';
    if(as_)as_.style.display=display==='none'?'none':'';
    return false;
}
function onlyChooseShow(el){return false;}
function prizeForecast(el){return false;}
function resetCurFilter(){return false;}
function showHided(){
    var els=document.querySelectorAll('[style*="display:none"]');
    for(var i=0;i<els.length;i++){
        if(els[i].tagName==='TR')els[i].style.display='';
    }
    return false;
}
function submitCurFilter(){return false;}
function switchNormal(flag){return false;}
function switchPageLeagueDIV(el){
    var div=document.getElementById('DivLeague');
    if(div)div.style.display=div.style.display==='none'?'':'none';
    return false;
}
function tbSort(col,el){return false;}
function getByID(id){return document.getElementById(id);}
"""


def _build_analysis_scripts() -> str:
    """分析页（析）所需的 JS 函数。"""
    return """
function r_close(id){
    var spans=document.querySelectorAll('.porlet_right .porlet_close');
    for(var i=0;i<spans.length;i++){
        if(spans[i].getAttribute('onclick')==='r_close('+id+')'){
            var h2=spans[i].closest('h2');
            if(!h2)return;
            h2.style.display='none';
            var next=h2.nextElementSibling;
            while(next){
                if(next.tagName==='H2'&&next.classList.contains('fx_title2'))break;
                next.style.display='none';
                next=next.nextElementSibling;
            }
            return;
        }
    }
}
function ShowIntegral(type){
    var spans=document.querySelectorAll('.st-tit span');
    for(var i=0;i<spans.length;i++){
        spans[i].style.fontWeight='normal';
        spans[i].style.color='';
    }
    var boxes=document.querySelectorAll('.standings-box');
    for(var b=0;b<boxes.length;b++){
        var tables=boxes[b].querySelectorAll('table');
        for(var i=0;i<tables.length;i++){
            if(i===type){tables[i].style.display='';}
            else{tables[i].style.display='none';}
        }
    }
    var clicked=document.querySelector('.st-tit span[onclick="ShowIntegral('+type+')"]');
    if(clicked){clicked.style.fontWeight='bold';clicked.style.color='#007FE4';}
}
function t_onclick(id){
    var el=document.getElementById(id);
    if(!el)return;
    var divId=id.replace('_t','');
    var div=document.getElementById(divId);
    if(div){
        div.style.display=el.checked?'':'none';
    }else{
        var parent=el.parentElement;
        if(parent){
            divId=parent.id;
            div=document.getElementById(divId);
            if(div)div.style.display=el.checked?'':'none';
        }
    }
}
function changePK(isLetGoal){
    var letGoal=document.getElementById('chec_pkLetGoal');
    var totalScore=document.getElementById('chec_pkTotalScore');
    if(isLetGoal){
        if(letGoal)letGoal.checked=true;
        if(totalScore)totalScore.checked=false;
    }else{
        if(letGoal)letGoal.checked=false;
        if(totalScore)totalScore.checked=true;
    }
    var pkLetGoal=document.getElementById('pkLetGoal');
    var pkTotalScore=document.getElementById('pkTotalScore');
    if(pkLetGoal)pkLetGoal.style.display=isLetGoal?'':'none';
    if(pkTotalScore)pkTotalScore.style.display=isLetGoal?'none':'';
    var letGoalTables=document.querySelectorAll('[id^="pkLetGoal"]');
    var totalTables=document.querySelectorAll('[id^="pkTotalScore"]');
    for(var i=0;i<letGoalTables.length;i++){
        if(letGoalTables[i].id!=='chec_pkLetGoal')
            letGoalTables[i].style.display=isLetGoal?'':'none';
    }
    for(var i=0;i<totalTables.length;i++){
        if(totalTables[i].id!=='chec_pkTotalScore')
            totalTables[i].style.display=isLetGoal?'none':'';
    }
}
function setType(t){
    var objLet=document.getElementById('checkLet');
    var objEu=document.getElementById('checkEu');
    var objTotal=document.getElementById('checkTotal');
    if(!objLet||!objEu||!objTotal)return;
    if(objLet.checked&&objEu.checked&&objTotal.checked){
        if(t==1)objLet.checked=false;
        else if(t==2)objTotal.checked=false;
        else if(t==3)objEu.checked=false;
        return;
    }
    if(!objLet.checked&&!objEu.checked&&!objTotal.checked){
        if(t==1)objLet.checked=true;
        else if(t==2)objTotal.checked=true;
        else if(t==3)objEu.checked=true;
    }
    var showLet=objLet.checked;
    var showTotal=objTotal.checked;
    var showEu=objEu.checked;
    var letEls=document.querySelectorAll('[id^="oddsLet"],[id^="div_let"],[class*="letGoal"]');
    var totalEls=document.querySelectorAll('[id^="oddsTotal"],[id^="div_total"],[class*="totalScore"]');
    var euEls=document.querySelectorAll('[id^="oddsEu"],[id^="div_eu"],[class*="euOdds"]');
}
function changeVs2(id){
    var oldEl=document.getElementById('vsOld');
    var newEl=document.getElementById('vsNew');
    if(id==0){
        if(oldEl)oldEl.style.display='';
        if(newEl)newEl.style.display='none';
    }else{
        if(oldEl)oldEl.style.display='none';
        if(newEl)newEl.style.display='';
    }
    var oldChk=document.getElementById('chec_vsOld');
    var newChk=document.getElementById('chec_vsNew');
    if(oldChk)oldChk.checked=(id==0);
    if(newChk)newChk.checked=(id==1);
}
function hidOddsCmp(id){
    var rows=document.querySelectorAll('tr[id^="oddsCmp_'+id+'"]');
    for(var i=0;i<rows.length;i++){
        rows[i].style.display=rows[i].style.display==='none'?'':'none';
    }
}
function GoJcUrl(type){return false;}
function checkOddsComp(){return false;}
function addOddsCmp(){return false;}
"""


def _build_live_detail_scripts() -> str:
    """Live detail 页面所需的 JS 函数（除了 _inject_live_subtab_scripts 已有的）。"""
    return """
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
    else if(type==2&&td)pd.style.display='';
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
function goPage(goalType){
    return false;
}
function setType(t){
    var objLet=document.getElementById('checkLet');
    var objEu=document.getElementById('checkEu');
    var objTotal=document.getElementById('checkTotal');
    if(!objLet||!objEu||!objTotal)return;
    if(objLet.checked&&objEu.checked&&objTotal.checked){
        if(t==1)objLet.checked=false;
        else if(t==2)objTotal.checked=false;
        else if(t==3)objEu.checked=false;
        return;
    }
    if(!objLet.checked&&!objEu.checked&&!objTotal.checked){
        if(t==1)objLet.checked=true;
        else if(t==2)objTotal.checked=true;
        else if(t==3)objEu.checked=true;
    }
}
function changeCompany(companyId){return false;}
function ChangeFlashVer(type){return false;}
function goCorner(id){return false;}
"""


def _build_league_scripts() -> str:
    """联赛聚合页所需的 JS 函数。"""
    return """
function changeRound(el){
    var tds=el.parentElement.querySelectorAll('td');
    for(var i=0;i<tds.length;i++){
        tds[i].style.fontWeight='normal';
        tds[i].style.background='';
    }
    el.style.fontWeight='bold';
    el.style.background='#e8f0fe';
    var round=el.textContent.trim();
    var roundDivs=document.querySelectorAll('[id^="round"]');
    for(var i=0;i<roundDivs.length;i++){
        var d=roundDivs[i];
        if(d.id==='round'+round||d.getAttribute('data-round')===round){
            d.style.display='';
        }else if(d.id&&d.id.match(/^round\\d+$/)){
            d.style.display='none';
        }
    }
}
function SelectScore(type){
    for(var i=1;i<=6;i++){
        var m=document.getElementById('menu'+i);
        if(m)m.className=m.className.replace('ontab','').trim();
        var c=document.getElementById('scoreContent'+i);
        if(c)c.style.display='none';
    }
    var cm=document.getElementById('menu'+type);
    if(cm)cm.className='ontab';
    var cc=document.getElementById('scoreContent'+type);
    if(cc)cc.style.display='';
    var alts=document.querySelectorAll('[id^="scoreType"]');
    for(var i=0;i<alts.length;i++){
        alts[i].style.display=(alts[i].id==='scoreType'+type)?'':'none';
    }
}
function searchTeamOrPlayer(){return false;}
"""


def _build_team_scripts() -> str:
    """球队资料页所需的 JS 函数。"""
    return """
function ClickTechType(type){
    var el0=document.getElementById('techType0');
    var el1=document.getElementById('techType1');
    if(type==0){
        if(el0)el0.className='ontab';
        if(el1)el1.className='';
        var all=document.getElementById('techAll');
        var home=document.getElementById('techHome');
        if(all)all.style.display='';
        if(home)home.style.display='none';
    }else{
        if(el1)el1.className='ontab';
        if(el0)el0.className='';
        var all=document.getElementById('techAll');
        var home=document.getElementById('techHome');
        if(all)all.style.display='none';
        if(home)home.style.display='';
    }
}
"""


def _build_oddslist_scripts() -> str:
    """欧赔列表页所需的 JS 函数。"""
    return """
function OddsHistory(id){return false;}
function getCompanies(type){return false;}
function oderlist(type){return false;}
function companyFilter(type){return false;}
function showFileter(type){
    var el=document.getElementById('filterDiv');
    if(el)el.style.display=el.style.display==='none'?'':'none';
    return false;
}
function CheckAll(){
    var checkboxes=document.querySelectorAll('input[type="checkbox"][name^="company"]');
    for(var i=0;i<checkboxes.length;i++){
        checkboxes[i].checked=true;
    }
    return false;
}
function clearFilter(){
    var checkboxes=document.querySelectorAll('input[type="checkbox"][name^="company"]');
    for(var i=0;i<checkboxes.length;i++){
        checkboxes[i].checked=false;
    }
    return false;
}
function dataFiletr(){return false;}
function delCheck(id){return false;}
function downEx(){return false;}
function exChange(){return false;}
"""


def _build_asianodds_scripts() -> str:
    """亚盘详情页所需的 JS 函数。"""
    return """
function companyFilter(){
    var checkboxes=document.querySelectorAll('input[type="checkbox"]');
    for(var i=0;i<checkboxes.length;i++){
        if(!checkboxes[i].checked){
            var row=checkboxes[i].closest('tr');
            if(row)row.style.display='none';
        }
    }
    return false;
}
function CheckAll(){
    var checkboxes=document.querySelectorAll('input[type="checkbox"]');
    for(var i=0;i<checkboxes.length;i++){
        checkboxes[i].checked=true;
        var row=checkboxes[i].closest('tr');
        if(row)row.style.display='';
    }
    return false;
}
function delCheck(id){
    var el=document.getElementById('check_'+id);
    if(el){
        el.checked=!el.checked;
        var row=el.closest('tr');
        if(row)row.style.display=el.checked?'':'none';
    }
    return false;
}
function RemoveDefault(){return false;}
"""


def _build_corner_overdown_scripts() -> str:
    """角球/大小球详情页所需的 JS 函数。"""
    return """
function companyFilter(){
    var checkboxes=document.querySelectorAll('input[type="checkbox"]');
    for(var i=0;i<checkboxes.length;i++){
        if(!checkboxes[i].checked){
            var row=checkboxes[i].closest('tr');
            if(row)row.style.display='none';
        }
    }
    return false;
}
function CheckAll(){
    var checkboxes=document.querySelectorAll('input[type="checkbox"]');
    for(var i=0;i<checkboxes.length;i++){
        checkboxes[i].checked=true;
        var row=checkboxes[i].closest('tr');
        if(row)row.style.display='';
    }
    return false;
}
"""


_PAGE_SCRIPTS = {
    "list": _build_list_scripts,
    "analysis": _build_analysis_scripts,
    "live_detail": _build_live_detail_scripts,
    "league": _build_league_scripts,
    "team": _build_team_scripts,
    "oddslist": _build_oddslist_scripts,
    "asianodds": _build_asianodds_scripts,
    "overdown": _build_corner_overdown_scripts,
    "corner": _build_corner_overdown_scripts,
}


def inject_page_scripts(html: str, url: str = "") -> str:
    """根据页面类型注入所需的 JavaScript 函数。

    在 _freeze_rendered_page 之后调用，确保交互元素在静态浏览时可用。
    """
    page_type = _detect_page_type(html, url)

    script_parts = [_build_common_scripts()]

    builder = _PAGE_SCRIPTS.get(page_type)
    if builder:
        script_parts.append(builder())

    script_code = "\n".join(script_parts)

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
