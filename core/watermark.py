#!/usr/bin/env python3
"""复刻水印注入模块"""
import re

from config import WATERMARK_TEXT


def _build_watermark_html(text: str = WATERMARK_TEXT) -> str:
    """生成水印 HTML：使用固定定位的旋转文本网格，避免依赖背景图/SVG data URL。"""
    # 3 行 x 4 列的文本网格，交错排列
    rows, cols = 3, 4
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

    return f"""<style>
.replica-watermark-overlay {{
    position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
    z-index: 2147483647; pointer-events: none; opacity: 0.95;
    overflow: hidden;
}}
.replica-watermark-overlay .replica-watermark-item {{
    font-size: clamp(24px, 3vw, 48px);
    font-weight: bold;
    color: #000;
    opacity: 0.45;
    text-shadow: 1px 1px 0 #fff, -1px -1px 0 #fff, 1px -1px 0 #fff, -1px 1px 0 #fff;
    white-space: nowrap;
    user-select: none;
}}
</style>
<div class="replica-watermark-overlay" id="replica-watermark-overlay">
{items_html}
</div>"""


def inject_watermark(html: str) -> str:
    """在 HTML 页面中注入复刻水印。"""
    # 如果已经存在则不再注入
    if "replica-watermark-overlay" in html:
        return html

    watermark_html = _build_watermark_html()

    # 优先在 </body> 前注入
    if "</body>" in html:
        return html.replace("</body>", watermark_html + "\n</body>", 1)

    # 退回到 </html> 前
    if "</html>" in html:
        return html.replace("</html>", watermark_html + "\n</html>", 1)

    # 最后追加
    return html + watermark_html
