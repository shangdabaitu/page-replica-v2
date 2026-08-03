#!/usr/bin/env python3
"""Flask 后端：提供日期列表、复刻进度 SSE 和静态文件服务"""
import json
import os
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, request, Response, send_from_directory, jsonify
from flask_cors import CORS

import config
from core.replicator import replicate_date
from storage import data_store

app = Flask(__name__, static_folder=None)
CORS(app)

_running: dict[str, threading.Thread] = {}
_cancel_events: dict[str, threading.Event] = {}


def _event_stream(generator):
    """把生成器事件包装成 SSE 格式。"""
    for event in generator:
        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.route("/")
def index():
    return send_from_directory(config.BASE_DIR / "web", "index.html")


@app.route("/web/<path:path>")
def web_assets(path):
    return send_from_directory(config.BASE_DIR / "web", path)


@app.route("/output/<path:path>")
def output_files(path):
    return send_from_directory(config.OUTPUT_DIR, path)


@app.route("/api/dates")
def api_dates():
    return jsonify({"dates": data_store.list_replicated_dates()})


@app.route("/api/status")
def api_status():
    date = request.args.get("date", "").strip()
    if not date:
        return jsonify({"error": "缺少 date 参数"}), 400
    meta = data_store.load_meta(date)
    report = data_store.load_report(date)
    list_page = data_store.get_list_page_path(date)
    return jsonify({
        "date": date,
        "meta": meta,
        "report": report,
        "list_page_url": f"/output/{date}/index.html" if list_page else None,
        "running": date in _running,
    })


@app.route("/api/stop")
def api_stop():
    """请求停止指定日期的复刻任务。"""
    date = request.args.get("date", "").strip()
    if not date:
        return jsonify({"error": "缺少 date 参数"}), 400

    event = _cancel_events.get(date)
    if event is None:
        return jsonify({"stopped": False, "message": "该日期没有运行中的任务"}), 404

    event.set()
    return jsonify({"stopped": True, "date": date})


@app.route("/api/replicate")
def api_replicate():
    date = request.args.get("date", "").strip()
    if not date:
        return jsonify({"error": "缺少 date 参数"}), 400

    # 规范化日期格式：保留用户输入作为目录名即可
    max_level = request.args.get("max_level", type=int)

    if date in _running:
        return jsonify({"error": f"日期 {date} 的复刻任务正在进行中"}), 409

    cancel_event = threading.Event()
    _cancel_events[date] = cancel_event

    def generate():
        try:
            for event in replicate_date(date, max_level=max_level, cancel_event=cancel_event):
                yield event
        except Exception as e:
            yield {"type": "error", "message": str(e)}
        finally:
            _running.pop(date, None)
            _cancel_events.pop(date, None)

    _running[date] = threading.current_thread()
    return Response(
        _event_stream(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True, debug=False)
