#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import threading
import traceback
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

from send_and_get_result import (
    DEFAULT_DEBUGGER_ADDRESS,
    DEFAULT_DEBUG_LOG_PATH,
    DEFAULT_FALLBACK_WAIT,
    DEFAULT_JSON_OUTPUT_PATH,
    DEFAULT_MESSAGE,
    DEFAULT_OUTPUT_PATH,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_POST_DELAY,
    DEFAULT_STABLE_FOR,
    DEFAULT_STARTUP_WAIT,
    DEFAULT_TIMEOUT,
    run_send_and_get,
)

app = Flask(__name__)
RUN_LOCK = threading.Lock()


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
    return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clean_result(result: dict) -> dict:
    cleaned = dict(result)
    for key in ("output_path", "json_output_path", "debug_log_path", "image_path"):
        if key in cleaned and cleaned[key] is not None:
            cleaned[key] = str(cleaned[key])
    return cleaned


def _execute(payload: dict, force_no_wait_reply: bool | None = None) -> tuple[dict, int]:
    if not RUN_LOCK.acquire(blocking=False):
        return {
            "ok": False,
            "code": 1,
            "error": "busy",
            "detail": "Service dang ban, hay thu lai sau.",
        }, 409

    try:
        result = run_send_and_get(
            debugger_address=str(payload.get("debugger_address", DEFAULT_DEBUGGER_ADDRESS)),
            message=str(payload.get("message", DEFAULT_MESSAGE)),
            image=str(payload.get("image", "")),
            fallback_wait=_as_float(payload.get("fallback_wait"), DEFAULT_FALLBACK_WAIT),
            keep_open=_as_float(payload.get("keep_open"), 0.0),
            auto_launch_chrome=_as_bool(payload.get("auto_launch_chrome"), False),
            startup_wait=_as_float(payload.get("startup_wait"), DEFAULT_STARTUP_WAIT),
            no_enter=_as_bool(payload.get("no_enter"), False),
            debug_log=str(payload.get("debug_log", DEFAULT_DEBUG_LOG_PATH)),
            no_wait_reply=(
                bool(force_no_wait_reply)
                if force_no_wait_reply is not None
                else _as_bool(payload.get("no_wait_reply"), False)
            ),
            timeout=_as_float(payload.get("timeout"), DEFAULT_TIMEOUT),
            poll_interval=_as_float(payload.get("poll_interval"), DEFAULT_POLL_INTERVAL),
            stable_for=_as_float(payload.get("stable_for"), DEFAULT_STABLE_FOR),
            post_delay=_as_float(payload.get("post_delay"), DEFAULT_POST_DELAY),
            output=str(payload.get("output", DEFAULT_OUTPUT_PATH)),
            json_output=str(payload.get("json_output", DEFAULT_JSON_OUTPUT_PATH)),
            print_all=_as_bool(payload.get("print_all"), False),
        )
        cleaned = _clean_result(result)
        return cleaned, 200 if cleaned.get("ok") else 500
    except Exception as exc:
        return {
            "ok": False,
            "code": 1,
            "error": "internal_error",
            "detail": str(exc),
            "traceback": traceback.format_exc(),
        }, 500
    finally:
        RUN_LOCK.release()


@app.get("/")
def index() -> Any:
    return jsonify(
        {
            "ok": True,
            "service": "hust_ai_auto_flask_api",
            "endpoints": [
                "/health",
                "/api/v1/send-and-get",
                "/api/v1/send",
                "/api/v1/latest",
            ],
        }
    )


@app.get("/health")
def health() -> Any:
    return jsonify({"ok": True, "status": "healthy"})


@app.post("/api/v1/send-and-get")
def api_send_and_get() -> Any:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "code": 1, "error": "invalid_json"}), 400
    result, status = _execute(payload, force_no_wait_reply=None)
    return jsonify(result), status


@app.post("/api/v1/send")
def api_send_only() -> Any:
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({"ok": False, "code": 1, "error": "invalid_json"}), 400
    result, status = _execute(payload, force_no_wait_reply=True)
    return jsonify(result), status


@app.get("/api/v1/latest")
def api_latest() -> Any:
    output_path = Path(request.args.get("output", DEFAULT_OUTPUT_PATH)).expanduser().resolve()
    json_output_path = Path(
        request.args.get("json_output", DEFAULT_JSON_OUTPUT_PATH)
    ).expanduser().resolve()

    text_exists = output_path.exists() and output_path.is_file()
    json_exists = json_output_path.exists() and json_output_path.is_file()
    if not text_exists and not json_exists:
        return jsonify(
            {
                "ok": False,
                "code": 1,
                "error": "result_not_found",
                "output_path": str(output_path),
                "json_output_path": str(json_output_path),
            }
        ), 404

    latest_text = ""
    json_payload: Any = {}
    if text_exists:
        latest_text = output_path.read_text(encoding="utf-8").strip()
    if json_exists:
        raw_json = json_output_path.read_text(encoding="utf-8")
        try:
            json_payload = json.loads(raw_json)
        except json.JSONDecodeError:
            json_payload = {"raw": raw_json}

    return jsonify(
        {
            "ok": True,
            "code": 0,
            "latest_text": latest_text,
            "latest_json": json_payload,
            "output_path": str(output_path),
            "json_output_path": str(json_output_path),
        }
    )


if __name__ == "__main__":
    host = os.environ.get("HUST_API_HOST", "127.0.0.1")
    port = int(os.environ.get("HUST_API_PORT", "5000"))
    app.run(host=host, port=port, debug=True, use_reloader=True, threaded=True)
