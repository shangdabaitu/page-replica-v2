#!/usr/bin/env python3
"""复刻水印注入模块"""
import re

from config import WATERMARK_TEXT


def _build_watermark_html(text: str = WATERMARK_TEXT) -> str:
    """生成水印 HTML：使用固定定位的旋转文本网格，避免依赖背景图/SVG data URL。

    同时附带一段兜底 JS：如果因为缓存或解析问题导致 overlay 为空，
    页面加载后会自动把文字项补进去，确保水印一定可见。
    """
    # 5 行 x 5 列的文本网格，覆盖更密集
    rows, cols = 5, 5
    items = []
    for r in range(rows):
        for c in range(cols):
            top = (r + 0.5) * (100 / rows)
            left = (c + 0.5) * (100 / cols)
            items.append(
                f'<div class="replica-watermark-item" '
                f'style="position:absolute;top:{top:.1f}%;left:{left:.1f}%;'
                f'transform:translate(-50%,-50%) rotate(-30deg);">{text}</div>'
            )

    items_html = "\n".join(items)

    # 兜底脚本：DOM 就绪后检查 overlay 是否为空，空则自动填充
    fallback_script = f"""<script>
(function(){{
  var text="{text}";
  function fill(){{
    var overlay=document.getElementById("replica-watermark-overlay");
    if(!overlay){{
      overlay=document.createElement("div");
      overlay.className="replica-watermark-overlay";
      overlay.id="replica-watermark-overlay";
      document.body.appendChild(overlay);
    }}
    if(overlay.querySelector(".replica-watermark-item")) return;
    overlay.innerHTML="";
    var rows=5,cols=5;
    for(var r=0;r<rows;r++){{
      for(var c=0;c<cols;c++){{
        var d=document.createElement("div");
        d.className="replica-watermark-item";
        d.style.position="absolute";
        d.style.top=((r+0.5)*(100/rows))+"%";
        d.style.left=((c+0.5)*(100/cols))+"%";
        d.style.transform="translate(-50%,-50%) rotate(-30deg)";
        d.textContent=text;
        overlay.appendChild(d);
      }}
    }}
  }}
  if(document.readyState==="loading"){{
    document.addEventListener("DOMContentLoaded",fill);
  }}else{{
    fill();
  }}
}})();
</script>"""

    return f"""<style>
.replica-watermark-overlay {{
    position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
    z-index: 2147483647; pointer-events: none; overflow: hidden;
}}
.replica-watermark-overlay .replica-watermark-item {{
    font-size: clamp(40px, 6vw, 80px);
    font-weight: 900;
    letter-spacing: 6px;
    color: rgba(255, 0, 0, 0.28);
    text-shadow:
        -2px -2px 0 rgba(255,255,255,0.9),
         2px -2px 0 rgba(255,255,255,0.9),
        -2px  2px 0 rgba(255,255,255,0.9),
         2px  2px 0 rgba(255,255,255,0.9),
         0 0 8px rgba(255,255,255,0.8);
    white-space: nowrap;
    user-select: none;
}}
</style>
<div class="replica-watermark-overlay" id="replica-watermark-overlay">
{items_html}
</div>
{fallback_script}"""


_HEAD_RE = re.compile(r"<head\b[^>]*>", re.I)
_BODY_RE = re.compile(r"<body\b[^>]*>", re.I)


def inject_watermark(html: str) -> str:
    """在 HTML 页面中注入复刻水印。

    水印尽量放在 <head> 之后（最靠前的位置），确保即使 CDN/浏览器对大文件
    做截断/流式渲染，最先到达的也是水印，用户一打开就能看到。
    """
    # 如果已经存在则不再注入
    if "replica-watermark-overlay" in html:
        return html

    watermark_html = _build_watermark_html()

    # 优先在 <head> 后注入（最靠前）
    m = _HEAD_RE.search(html)
    if m:
        return html[: m.end()] + watermark_html + html[m.end() :]

    # 其次在 <body> 后注入
    m = _BODY_RE.search(html)
    if m:
        return html[: m.end()] + watermark_html + html[m.end() :]

    # 退回到 </body> 前
    if "</body>" in html:
        return html.replace("</body>", watermark_html + "\n</body>", 1)

    # 退回到 </html> 前
    if "</html>" in html:
        return html.replace("</html>", watermark_html + "\n</html>", 1)

    # 最后追加
    return html + watermark_html
