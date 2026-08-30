"""
매일성경 웹앱 — 정표님 · 배우자님 전용

설계 원칙(기획안 v3 기준, 2026-08-30 STT 제거 이후 갱신):
  - Render 무료 웹서비스 "한 개"로 끝낸다. 별도 Cron Job / 외부 스케줄러 없음.
  - 그날 처음 접속한 사람의 요청이 곧 "오늘 콘텐츠 만들어줘" 트리거가 된다
    (온디맨드 생성). 이미 만들어진 날이면 메모리 캐시를 즉시 보여준다.
  - 과거 데이터는 저장하지 않는다 — 새 날짜가 되면 캐시를 통째로 덮어쓴다.
  - 오디오는 재호스팅하지 않고 원본 mp3 URL을 그대로 재생 링크로 쓴다.
  - 음성을 텍스트로 자동 변환(STT)하지 않는다. 원본 사이트에 이미 텍스트로
    있는 해설(하나님은 어떤 분입니까/내게 주시는 교훈은 무엇입니까)을 그대로
    쓰고, 음성은 원본 그대로 재생만 제공한다 — Render 무료 CPU로는 STT가
    비현실적으로 느렸기 때문에(정표님과 상의 후 2026-08-30 결정).
"""
from __future__ import annotations

import os
import threading
import datetime as dt
from zoneinfo import ZoneInfo
from functools import wraps

from flask import Flask, render_template, jsonify, request, Response, redirect, url_for, make_response

import scraper

KST = ZoneInfo("Asia/Seoul")

# 두 분이 서로 다른 아이디를 쓸 수 있도록 최대 2쌍까지 지원한다.
# (APP_USER/APP_PASSWORD, APP_USER2/APP_PASSWORD2 — Render 환경변수로 설정)
# 수동으로 아이디/비번을 입력하는 경우(Basic Auth)를 위한 것.
_pairs = [
    (os.environ.get("APP_USER", ""), os.environ.get("APP_PASSWORD", "")),
    (os.environ.get("APP_USER2", ""), os.environ.get("APP_PASSWORD2", "")),
]
VALID_CREDENTIALS = {u: p for u, p in _pairs if u and p}

# QR코드/바로가기 링크로 "자동 로그인"하기 위한 토큰.
# 자격증명을 URL에 그대로 넣지 않고(user:pass@ 형식은 일부 QR스캐너/브라우저가
# 인식하지 못함), 깨끗한 URL(/enter/<토큰>)을 한 번 열면 쿠키를 심어 이후
# 자동으로 로그인 상태를 유지한다. (APP_TOKEN, APP_TOKEN2 — Render 환경변수)
ACCESS_TOKENS = {
    t: True for t in [os.environ.get("APP_TOKEN", ""), os.environ.get("APP_TOKEN2", "")] if t
}
AUTH_COOKIE = "db_auth"
AUTH_COOKIE_MAX_AGE = 60 * 60 * 24 * 400  # 약 400일 (브라우저 쿠키 최대 수명)

# 매일 새벽 GitHub Actions(외부 무료 스케줄러)가 이 값을 알고 /warm 을
# 호출해서, 두 분이 실제로 앱을 열기 전에 미리 콘텐츠 생성을 끝내둔다.
# STT를 없앤 뒤로는 스크래핑만 하면 되므로 몇 초면 끝나지만, Render 무료
# 플랜이 잠들어 있던 경우 깨우는 역할은 여전히 유효해서 남겨둔다.
WARM_SECRET = os.environ.get("WARM_SECRET", "")

app = Flask(__name__)

# ---- 아주 단순한 상태 저장소 (당일 캐시, 재시작하면 사라짐 — 의도된 동작) ----
_lock = threading.Lock()
STATE = {
    "date": None,       # "YYYY-MM-DD" (KST 기준)
    "status": "idle",   # idle | processing | ready | error
    "content": None,    # scraper.TodayContent
    "error": None,
}


def today_str() -> str:
    return dt.datetime.now(KST).date().isoformat()


# ---------------------------------------------------------------- Basic Auth
def check_auth(username: str, password: str) -> bool:
    if not VALID_CREDENTIALS:
        # 아이디/비밀번호가 하나도 설정되지 않은 상태로 배포되는 사고를 막는다.
        return False
    return VALID_CREDENTIALS.get(username) == password


def authenticate() -> Response:
    return Response(
        "인증이 필요합니다.", 401,
        {"WWW-Authenticate": 'Basic realm="daily-bible"'},
    )


def requires_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        # 1) QR/바로가기 링크로 심어둔 쿠키가 있으면 그걸로 통과시킨다.
        cookie_token = request.cookies.get(AUTH_COOKIE)
        if cookie_token and cookie_token in ACCESS_TOKENS:
            return f(*args, **kwargs)
        # 2) 없으면 기존 방식대로 아이디/비번 직접 입력(Basic Auth)도 허용한다.
        auth = request.authorization
        if auth and check_auth(auth.username, auth.password):
            return f(*args, **kwargs)
        return authenticate()
    return wrapper


# ---------------------------------------------------------------- 생성 로직
def _generate_today() -> None:
    """백그라운드 스레드에서 실행된다. 스크래핑만 하면 되므로 보통 몇 초 안에
    끝난다(예전 STT 단계가 없어졌다)."""
    date_str = today_str()
    try:
        content = scraper.fetch_today()
        with _lock:
            STATE["date"] = date_str
            STATE["content"] = content
            STATE["status"] = "ready"
            STATE["error"] = None
    except Exception as e:  # noqa: BLE001
        with _lock:
            STATE["date"] = date_str
            STATE["status"] = "error"
            STATE["error"] = str(e)


def _ensure_generation_started() -> None:
    with _lock:
        if STATE["date"] == today_str() and STATE["status"] in ("processing", "ready"):
            return
        STATE["date"] = today_str()
        STATE["status"] = "processing"
        STATE["content"] = None
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
        return render_template(
            "index.html",
            content=content,
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


@app.route("/enter/<token>")
def enter(token):
    """QR/바로가기 링크 전용 진입점. 유효한 토큰이면 쿠키를 심고 홈으로 보낸다."""
    if token not in ACCESS_TOKENS:
        return "유효하지 않은 링크입니다.", 404
    resp = make_response(redirect(url_for("index")))
    resp.set_cookie(
        AUTH_COOKIE,
        token,
        max_age=AUTH_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=True,
    )
    return resp


@app.route("/warm")
def warm():
    """외부 무료 스케줄러(GitHub Actions)가 매일 새벽 호출하는 예열 엔드포인트.
    사람이 열기 전에 미리 스크래핑을 시작해둔다. Basic Auth 대신 ?token=
    쿼리로 보호한다(자동화 스크립트는 로그인 세션을 못 만드므로)."""
    if not WARM_SECRET or request.args.get("token") != WARM_SECRET:
        return "forbidden", 403
    with _lock:
        is_today = STATE["date"] == today_str()
        status = STATE["status"] if is_today else "idle"
    if status not in ("processing", "ready"):
        _ensure_generation_started()
        status = "processing"
    return jsonify({"status": status})


@app.route("/debug")
@requires_auth
def debug():
    """스크래핑이 실제로 무엇을 추출했는지 원문과 함께 확인하는 화면.
    해설/기도문 등이 잘못 추출됐을 때 원인을 바로 확인하기 위한 용도."""
    with _lock:
        is_today = STATE["date"] == today_str()
        content = STATE["content"] if is_today else None
        status = STATE["status"] if is_today else "idle"
    return render_template("debug.html", content=content, status=status)


@app.route("/healthz")
def healthz():
    return "ok", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
