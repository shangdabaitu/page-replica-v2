#!/usr/bin/env python3
"""繁体中文转简体中文模块"""
import re

import config

# 延迟初始化 OpenCC，避免未安装时整个模块无法导入
_converter = None


def _get_converter():
    global _converter
    if _converter is not None:
        return _converter
    try:
        import opencc
        _converter = opencc.OpenCC("t2s")
    except Exception as e:
        print(f"[WARN] OpenCC 初始化失败: {e}，繁体转简体将跳过")
        _converter = False
    return _converter


def to_simplified(text: str) -> str:
    """将文本中的繁体中文转换为简体中文。"""
    if not text:
        return text
    conv = _get_converter()
    if conv:
        try:
            return conv.convert(text)
        except Exception:
            pass
    return text


def simplify_html(html: str) -> str:
    """将 HTML 中的可见文本和常用属性转换为简体，同时不破坏标签结构。"""
    if not html:
        return html
    conv = _get_converter()
    if not conv:
        return html

    # 1. 转换 <title>、<meta> content、<a title>/<img alt> 等属性中的中文
    attr_tags = ("title", "alt", "placeholder", "content", "value")
    for attr in attr_tags:
        html = _convert_attr(html, attr, conv)

    # 2. 转换标签之间的文本节点：用 BeautifulSoup 更安全
    try:
        from bs4 import BeautifulSoup, NavigableString
        soup = BeautifulSoup(html, "lxml")
        changed = False
        for node in soup.find_all(string=True):
            if isinstance(node, NavigableString):
                original = str(node)
                simplified = conv.convert(original)
                if simplified != original:
                    node.replace_with(NavigableString(simplified))
                    changed = True
        # 如果 bs4 改变了结构则使用结果，否则返回原始 HTML
        if changed:
            return str(soup)
    except Exception as e:
        print(f"[WARN] BeautifulSoup 简体转换失败: {e}")

    return html


def _convert_attr(html: str, attr: str, conv) -> str:
    """转换指定属性的中文内容。"""
    pattern = re.compile(rf'\s{attr}=["\']([^"\']+)["\']', re.I)

    def repl(m):
        return m.group(0).replace(m.group(1), conv.convert(m.group(1)))

    return pattern.sub(repl, html)
