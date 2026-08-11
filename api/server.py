#!/usr/bin/env python3
"""
视觉检查 API 服务器

提供 REST API 让前端页面触发视觉对比检查：
  POST /api/visual-check        — 启动检查（参数: date, use_playwright）
  GET  /api/visual-check/status  — 获取当前进度
  POST /api/visual-check/cancel   — 取消检查
  GET  /api/dates                — 获取可用日期列表
  GET  /api/meta?date=xxx        — 获取指定日期的 meta.json

启动方式:
  cd /data/user/work/page-replica-v2
  python3 api/server.py
"""
import sys
import os
import threading
import json
from pathlib import Path

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from api.visual_checker import CheckState, run_visual_check

app = Flask(__name__, static_folder=None)
CORS(app)  # 允许跨域，方便 GitHub Pages 前端调用

# 全局检查线程
_check_thread = None


# ------------------------------------------------------------------ #
#  静态文件服务（方便本地直接访问 docs 目录）
# ------------------------------------------------------------------ #

@app.route("/")
def index():
    """返回前端控制页面。"""
    return send_from_directory(PROJECT_ROOT / "docs", "index.html")


@app.route("/<path:path>")
def static_files(path):
    """提供 docs 目录下的静态文件。"""
    docs_dir = PROJECT_ROOT / "docs"
    full = docs_dir / path
    if full.is_file():
        return send_from_directory(docs_dir, path)
    # 尝试作为目录
    if full.is_dir():
        index_file = full / "index.html"
        if index_file.is_file():
            return send_from_directory(full, "index.html")
    return ("Not Found", 404)


# ------------------------------------------------------------------ #
#  API 端点
# ------------------------------------------------------------------ #

@app.route("/api/dates")
def api_dates():
    """获取可用日期列表。"""
    dates_path = PROJECT_ROOT / "docs" / "dates.json"
    if dates_path.exists():
        with open(dates_path, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify([])


@app.route("/api/meta")
def api_meta():
    """获取指定日期的 meta.json。"""
    date = request.args.get("date", "")
    if not date:
        return jsonify({"error": "date 参数必填"}), 400
    meta_path = PROJECT_ROOT / "docs" / date / "meta.json"
    if not meta_path.exists():
        return jsonify({"error": f"未找到 {date} 的 meta.json"}), 404
    with open(meta_path, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route("/api/visual-check", methods=["POST"])
def api_start_check():
    """启动视觉检查。

    请求参数:
      - date: 日期字符串（如 "2026-07-21"），必填
      - use_playwright: 是否尝试使用 Playwright（默认 false）
    """
    global _check_thread

    data = request.get_json(force=True, silent=True) or {}
    date = data.get("date") or request.args.get("date", "")
    use_pw = data.get("use_playwright", False)

    if not date:
        return jsonify({"error": "date 参数必填"}), 400

    state = CheckState()
    if state.running:
        return jsonify({
            "error": "视觉检查正在进行中，请等待完成",
            "status": state.to_dict(),
        }), 409

    # 验证日期数据存在
    meta_path = PROJECT_ROOT / "docs" / date / "meta.json"
    if not meta_path.exists():
        return jsonify({"error": f"未找到 {date} 的 meta.json"}), 404

    # 启动检查线程
    _check_thread = threading.Thread(
        target=run_visual_check,
        args=(date, use_pw),
        daemon=True,
    )
    _check_thread.start()

    return jsonify({
        "message": f"视觉检查已启动（日期: {date}）",
        "status": state.to_dict(),
    })


@app.route("/api/visual-check/status")
def api_check_status():
    """获取当前视觉检查进度。"""
    state = CheckState()
    return jsonify(state.to_dict())


@app.route("/api/visual-check/cancel", methods=["POST"])
def api_cancel_check():
    """取消当前视觉检查。"""
    state = CheckState()
    if not state.running:
        return jsonify({"message": "没有正在进行的视觉检查"})
    state.running = False
    return jsonify({"message": "视觉检查已取消（将在当前页面完成后停止）"})


@app.route("/api/health")
def api_health():
    """健康检查。"""
    return jsonify({
        "status": "ok",
        "playwright_available": _check_playwright(),
        "project_root": str(PROJECT_ROOT),
    })


def _check_playwright() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        return True
    except ImportError:
        return False


# ------------------------------------------------------------------ #
#  入口
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    print("=" * 60)
    print("  视觉检查 API 服务器")
    print(f"  项目根目录: {PROJECT_ROOT}")
    print(f"  Playwright: {'可用' if _check_playwright() else '不可用（使用 DOM 对比模式）'}")
    print("  访问地址: http://0.0.0.0:5001")
    print("=" * 60)

    app.run(host="0.0.0.0", port=5001, debug=False, threaded=True)
