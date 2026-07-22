#!/usr/bin/env python3
"""复刻水印注入模块"""
import re

WATERMARK_HTML = """<style>
.replica-watermark-overlay {
    position: fixed; top: 0; left: 0; width: 100vw; height: 100vh; z-index: 2147483647;
    pointer-events: none; opacity: 0.65;
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='280' height='200' viewBox='0 0 280 200'%3E%3Ctext x='50%25' y='50%25' font-size='28' font-weight='bold' fill='%23555' fill-opacity='0.5' font-family='Arial' text-anchor='middle' dominant-baseline='middle' transform='rotate(-30 140 100)'%3E%E5%A4%8D%E5%88%BB%E9%A1%B5%E9%9D%A2%3C/text%3E%3C/svg%3E");
    background-repeat: repeat; background-size: 280px 200px;
}
</style>
<div class="replica-watermark-overlay" id="replica-watermark-overlay"></div>"""


def inject_watermark(html: str) -> str:
    """在 HTML 页面中注入复刻水印。"""
    # 如果已经存在则不再注入
    if "replica-watermark-overlay" in html:
        return html

    # 优先在 </body> 前注入
    if "</body>" in html:
        return html.replace("</body>", WATERMARK_HTML + "\n</body>", 1)

    # 退回到 </html> 前
    if "</html>" in html:
        return html.replace("</html>", WATERMARK_HTML + "\n</html>", 1)

    # 最后追加
    return html + WATERMARK_HTML
