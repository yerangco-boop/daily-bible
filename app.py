"""
매일성경 웹앱 — 정표님 · 배우자님 전용

설계 원칙(기획안 v3 기준):
  - Render 무료 웹서비스 "한 개"로 끝낸다. 별도 Cron Job / 외부 스케줄러 없음.
  - 그날 처음 접속한 사람의 요청이 곧 "오늘 콘텐츠 만들어줘" 트리거가 된다
    (온디맨드 생성). 이미 만들어진 날이면 메모리 캐시를 즉시 보여준다.
  - 과거 데이터는 저장하지 않는다 — 새 날짜가 되면 캐시를 통째로 덮어쓴다.
  - 오디오는 재호스팅하지 않고 원본 mp3 URL을 그대로 재생 링크로 쓴다.
"""
from __future__ import annotations

import os
import tempfile
import threading
import datetime as dt
from zoneinfo import ZoneInfo
from functools import wraps

import requests
from flask import Flask, render_template, jsonify, request, Response

import scraper
import stt

KST = ZoneInfo("Asia/Seoul")

APP_USER = os.environ.get("APP_USER", "jeongpyo")
APP_PASSWORD = os.environ.get("APP_PASSWORD")  # 반드시 Render 환경변수로 설정할 것

app = Flask(__name__)

# ---- 아주 단순한 상태 저장소 (당일 캐시, 재시작하면 사라짐 — 의도된 동작) ----
_lock = threading.Lock()
STATE = {
    "date": None,       # "YYYY-MM-DD" (KST 기준)
    "status": "idle",   # idle | processing | ready | error
    "content": None,    # scraper.TodayContent
    "script": "",        # STT 결과
    "error": None,
}


def today_str() -> str:
    return dt.datetime.now(KST).date().isoformat()


# ---------------------------------------------------------------- Basic Auth
def check_auth(username: str, password: str) -> bool:
    if not APP_PASSWORD:
        # 비밀번호가 설정되지 않은 상태로 배포되는 사고를 막는다.
        return False
    return username == APP_USER and password == APP_PASSWORD


def authenticate() -> Response:
    return Response(
        "인증이 필요합니다.", 401,
        {"WWW-Authenticate": 'Basic realm="daily-bible"'},
    )


def requires_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return wrapper


# ---------------------------------------------------------------- 생성 로직
def _generate_today() -> None:
    """백그라운드 스레드에서 실행된다. 스크래핑 + STT 후 STATE를 채운다."""
    date_str = today_str()
    try:
        content = scraper.fetch_today()

        script_text = ""
        try:
            script_text = _download_and_transcribe(content.mp3_url)
        except Exception as e:  # STT 실패해도 나머지 콘텐츠는 보여준다
            script_text = f"(자동 스크립트 생성에 실패했습니다: {e})"

        with _lock:
            STATE["date"] = date_str
            STATE["content"] = content
            STATE["script"] = script_text
            STATE["status"] = "ready"
            STATE["error"] = None
    except Exception as e:  # noqa: BLE001
        with _lock:
            STATE["date"] = date_str
            STATE["status"] = "error"
            STATE["error"] = str(e)


def _download_and_transcribe(mp3_url: str) -> str:
    fd, path = tempfile.mkstemp(suffix=".mp3")
    os.close(fd)
    try:
        with requests.get(mp3_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
        return stt.transcribe_audio(path, language="ko")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _ensure_generation_started() -> None:
    with _lock:
        if STATE["date"] == today_str() and STATE["status"] in ("processing", "ready"):
            return
        STATE["date"] = today_str()
        STATE["status"] = "processing"
        STATE["content"] = None
        STATE["script"] = ""
        STATE["error"] = None
    threading.Thread(target=_generate_today, daemon=True).start()


# --------------------------------------------------------------------- 라우트
@app.route("/")
@requires_auth
def index():
    with _lock:
        is_today = STATE["date"] == today_str()
        status = STATE["status"] if is_today else "idle"

    if status == "ready":
        with _lock:
            content = STATE["content"]
            script_text = STATE["script"]
        return render_template(
            "index.html",
            content=content,
            script_text=script_text,
            translation_label=scraper.TRANSLATION_LABEL,
        )

    if status == "error":
        with _lock:
            error = STATE["error"]
        return render_template("processing.html", status="error", error=error), 200

    _ensure_generation_started()
    return render_template("processing.html", status="processing", error=None)


@app.route("/status")
@requires_auth
def status():
    with _lock:
        is_today = STATE["date"] == today_str()
        return jsonify({
            "ready": is_today and STATE["status"] == "ready",
            "status": STATE["status"] if is_today else "idle",
        })


@app.route("/healthz")
def healthz():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
