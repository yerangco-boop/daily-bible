"""
sum.su.or.kr(성서유니온 매일성경)의 오늘자 페이지를 가져와
성경본문 / 해설 / 기도문 / 오디오 URL을 추출한다.

주의(중요): 이 파서는 실제 페이지의 HTML class/id 구조를 직접 보고 만든 것이
아니라, 페이지 텍스트에 등장하는 고정 문구를 기준으로 앞뒤를 잘라내는
"텍스트 앵커" 방식이다. 원본 사이트의 문구가 바뀌면 파싱이 깨질 수 있다.

2026-08-30(2차) 확인: 정표님이 보내주신 /debug 원문 캡처로 실제 DOM 텍스트
순서가 화면에 보이는 순서와 다르다는 걸 확인했다 — 절 번호가 붙은 진짜
성경 본문(1절~마지막 절)이 제목/참조/요약보다 먼저 나오고, 그 바로 뒤에
번역본 저작권 문구가 붙는다. 그 다음에야 오디오해설 크레딧, 날짜, 제목,
참조 줄, 짧은 요약 문단, "— 개역개정", 해설, 기도문이 순서대로 이어진다.
그래서 성경 본문은 "저작권" 문구를 기준으로 거꾸로 절 번호를 찾아 추출하고,
짧은 요약 문단은 기존처럼 참조 줄 뒤에서 추출한다.
"""
from __future__ import annotations

import re
import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

# 2026-09-02 수정: Render 서버는 UTC로 돌아가는데 dt.date.today()를 그대로 쓰면
# 한국시간 자정~오전9시 사이엔 서버 UTC 날짜가 아직 "어제"라서, 실제로는 이미
# 갱신된(한국시간 기준 오늘자) 사이트 내용을 긁어오고도 날짜 라벨만 하루 전으로
# 잘못 찍히는 버그가 있었다(정표님이 9/2에 접속했는데 화면엔 9/1로 나온 사고).
# 그래서 기본값도 반드시 한국시간(KST) 기준으로 계산한다.
KST = ZoneInfo("Asia/Seoul")

BASE_URL = "https://sum.su.or.kr:8888/bible/today"
MP3_URL_TMPL = "https://mp3.dailybible.or.kr/kor/{year}/{ymd}.mp3"

# 정표님 요청: 개역개정 하나만 쓴다. 다른 번역본 탭은 만들지 않는다.
TRANSLATION_LABEL = "개역개정"

# 페이지에 여러 번역본 탭이 나열될 때 흔히 같이 등장하는 이름들 — 혹시 본문 앞에
# 탭 이름이 한 줄로 섞여 나오면 건너뛰기 위한 참고용 목록(추출 대상 아님).
_OTHER_TRANSLATION_NAMES = ["개역한글", "쉬운성경", "새번역", "ESV"]

# 2026-08-30 실제 화면 캡처로 확인한 정확한 형식: "본문: 이사야(Isaiah)
# 38:1 - 38:22 찬송가 363장" (책이름과 절 사이에 영문 표기가 괄호로 낀다.
# 절 구분 기호는 "~"인 날도 있고 "-"인 날도 있어서 문자 종류는 신경 쓰지 않는다)
REF_LINE_PATTERN = re.compile(
    r"본문\s*[:：]\s*(?P<ref>.+?)\s*찬송가\s*(?P<hymn_no>\d+)\s*장"
)
ANCHOR_SUMMARY = "본문요약"
ANCHOR_GOD = "하나님은 어떤 분입니까"
ANCHOR_LESSON = "내게 주시는 교훈은 무엇입니까"

# 2026-09-02 추가(정표님 제안): 날짜를 서버 시계로 "계산"하지 않고, 사이트
# 화면에 실제로 찍혀 있는 날짜("2026.09.02" 형식, 참조 줄 앞쪽에 등장)를
# 성경본문처럼 그대로 긁어온다. 계산이 아니라 원본 값을 그대로 읽는 방식이라
# 서버 시간대 설정에 문제가 생기더라도 화면 날짜와 본문 내용이 항상 정확히
# 일치한다. 혹시 사이트 형식이 바뀌어 못 찾으면(page_date가 None이면)
# fetch_today()에서 한국시간(KST) 계산값으로 안전하게 폴백한다.
PAGE_DATE_PATTERN = re.compile(r"(?P<y>\d{4})[.\-](?P<m>\d{2})[.\-](?P<d>\d{2})")

# "기도"라는 단어는 해설 문장 속에도 자연스럽게 자주 등장한다
# (예: "...얼굴을 벽으로 향하고 기도합니다."). 그래서 문단 속에 섞인 단어가
# 아니라, 그 줄에 "기도"라는 글자만 단독으로 있는 경우(실제 소제목)만
# 기도문의 시작으로 인정한다 — 예전 버전은 문장 속 "기도"에 걸려 해설
# 뒷부분을 통째로 기도문으로 잘못 삼키는 버그가 있었다.
PRAYER_HEADING_PATTERNS = [
    re.compile(r"^\s*기도\s*$", re.MULTILINE),
    re.compile(r"^\s*열방을 위한 기도\s*$", re.MULTILINE),
]


@dataclass
class TodayContent:
    date_str: str  # YYYY-MM-DD
    title: str = ""
    hymn: str = ""
    verse_ref: str = ""
    verse_start_no: str = ""  # 참조의 시작 절 번호 (예: "1")
    verse_end_no: str = ""    # 참조의 마지막 절 번호 (예: "22")
    verse_text: str = ""      # 개역개정 본문 (절 번호가 있는 실제 성경 본문)
    verse_summary: str = ""   # 참조 줄 뒤에 붙는 짧은 요약 문단(성경 본문이 아님)
    note_god: str = ""        # 하나님은 어떤 분입니까
    note_lesson: str = ""     # 내게 주시는 교훈은 무엇입니까
    prayer: str = ""
    mp3_url: str = ""
    reader_credit: str = ""
    commentator_credit: str = ""
    raw_text: str = ""        # 디버깅용 원문 전체 텍스트


def build_mp3_url(target_date: dt.date) -> str:
    return MP3_URL_TMPL.format(year=target_date.year, ymd=target_date.strftime("%Y%m%d"))


def _extract_page_date(text: str, search_from: int) -> dt.date | None:
    """사이트 화면에 실제로 찍혀 있는 날짜를 그대로 읽어온다(계산하지 않음).

    "저작권" 문구 이후 구간(참조 줄보다 앞, 제목보다 앞)에서 처음 만나는
    'YYYY.MM.DD' 또는 'YYYY-MM-DD' 형식의 줄을 사이트가 표시 중인 날짜로
    본다. 형식이 바뀌어 못 찾으면 None을 돌려주고, 호출하는 쪽에서 한국시간
    계산값으로 폴백한다.
    """
    m = PAGE_DATE_PATTERN.search(text, search_from)
    if not m:
        return None
    try:
        return dt.date(int(m.group("y")), int(m.group("m")), int(m.group("d")))
    except ValueError:
        return None


def fetch_today(target_date: dt.date | None = None, timeout: int = 20) -> TodayContent:
    """오늘(또는 지정한 날짜)의 매일성경 콘텐츠를 가져온다.

    주의: 이 함수는 실제 인터넷에 나가는 요청을 던진다. 원본 사이트가
    날짜별 접속 URL을 어떻게 받는지(쿼리 파라미터 등)는 확인되지 않았으므로,
    우선 "오늘" 페이지만 지원한다. 오디오 URL은 날짜로부터 결정적으로
    계산되므로 스크래핑 성공 여부와 무관하게 항상 만들어진다.

    화면에 보여줄 날짜(date_str)는 원칙적으로 사이트 화면에 실제로 찍힌
    날짜를 그대로 읽어서 쓴다(_extract_page_date) — 서버 시계로 "오늘"을
    계산하는 것보다 원본 값을 그대로 읽는 쪽이 더 정확하다(2026-09-02,
    정표님 제안). target_date 인자(또는 기본값인 한국시간 계산값)는 사이트
    형식이 바뀌어 날짜를 못 찾았을 때만 쓰이는 안전장치다.
    """
    fallback_date = target_date or dt.datetime.now(KST).date()

    resp = requests.get(
        BASE_URL,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (compatible; DailyBibleApp/1.0; personal-use)"},
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text("\n", strip=True)

    # "찬송가 N장" 같은 문구가 페이지 앞쪽(성경 본문 쪽)에도 한 번, 제목/참조
    # 줄에도 한 번, 이렇게 두 번 나오는 것으로 확인됐다. 그래서 이미 안정적으로
    # 찾은 "저작권" 문구(성경 본문 바로 뒤) 이후부터만 제목/참조 줄을 찾아야
    # 앞쪽의 것과 혼동하지 않는다. 날짜도 같은 구간에서 찾는다.
    copyright_idx = text.find("저작권")
    search_from = copyright_idx if copyright_idx != -1 else 0

    page_date = _extract_page_date(text, search_from)
    target_date = page_date or fallback_date

    content = TodayContent(date_str=target_date.isoformat(), raw_text=text)
    content.mp3_url = build_mp3_url(target_date)

    ref_end = _extract_title_and_ref(content, text, search_from)
    if content.verse_ref:
        content.verse_start_no = _extract_start_verse_no(content.verse_ref)
        content.verse_end_no = _extract_end_verse_no(content.verse_ref)
    _extract_scripture_verses(content, text)
    _extract_summary(content, text, ref_end)
    _extract_sections(content, text)
    _extract_credits(content, text)

    return content


def _extract_start_verse_no(ref: str) -> str:
    """참조 문자열에서 시작 절 번호를 뽑아낸다.
    예: '이사야(Isaiah) 38:1 - 38:22' → '1'"""
    nums = re.findall(r"(\d+):(\d+)", ref)
    return nums[0][1] if nums else ""


def _extract_end_verse_no(ref: str) -> str:
    """참조 문자열에서 마지막 절 번호를 뽑아낸다.
    예: '이사야(Isaiah) 38:1 - 38:22' → '22'"""
    nums = re.findall(r"(\d+):(\d+)", ref)
    if nums:
        return nums[-1][1]
    m = re.search(r"[~-]\s*(\d+)\s*$", ref.strip())
    if m:
        return m.group(1)
    return ""


def _extract_title_and_ref(content: TodayContent, text: str, search_from: int = 0) -> int:
    """제목 / 성경구절 / 찬송가를 추출한다.

    search_from 이후에서만 찾는다(성경 본문 쪽에도 같은 문구가 있을 수 있어서).
    반환값: 참조 줄이 끝나는 위치(요약 문단이 시작되는 지점) — 못 찾으면 -1.
    """
    m = REF_LINE_PATTERN.search(text, search_from)
    if m:
        content.verse_ref = m.group("ref").strip()
        content.hymn = f"찬송가 {m.group('hymn_no')}장"

        # 참조 줄 바로 앞의 줄들 중, 날짜/라벨처럼 보이지 않는 첫 후보를 제목으로 쓴다.
        before = text[search_from: m.start()]
        lines = [l.strip() for l in before.split("\n") if l.strip()]
        date_pat = re.compile(r"\d{4}[.\-]\d{2}[.\-]\d{2}")
        for cand in reversed(lines[-4:]):
            if 2 <= len(cand) <= 40 and not date_pat.search(cand) and "매일성경" not in cand:
                content.title = cand
                break
        return m.end()

    # 폴백(참조 줄 형식이 바뀐 경우): 예전 방식으로라도 찬송가/제목을 시도한다.
    m2 = re.search(r"찬송가\s*\d+\s*장", text[search_from:])
    if m2:
        content.hymn = m2.group(0)
    lines = [l.strip() for l in text[search_from:].split("\n") if l.strip()]
    date_pat = re.compile(r"\d{4}[.\-]\d{2}[.\-]\d{2}")
    for i, line in enumerate(lines):
        if date_pat.search(line):
            for cand in lines[i + 1 : i + 5]:
                if 2 <= len(cand) <= 40 and "찬송가" not in cand:
                    content.title = cand
                    break
            break
    return -1


def _extract_scripture_verses(content: TodayContent, text: str) -> None:
    """실제 절 번호가 붙은 성경 본문(개역개정)을 추출한다.

    2026-08-30 실제 원문(raw_text) 확인 결과, 이 본문은 제목/참조/요약보다
    먼저 나오고 바로 뒤에 번역본 저작권 문구("...의 저작권은...")가 붙는다.
    그래서 참조 줄이 아니라 "저작권"이라는 단어의 첫 등장 위치를 기준으로
    거꾸로 절 번호 줄(끝 절 → 시작 절)을 찾아 그 구간을 본문으로 잡는다.

    한계: 시작 절과 끝 절 번호가 같은 날(단일 절만 읽는 날)이나, 장이 바뀌는
    구간(예: 37:38 ~ 38:8)은 지금 방식으로 정확히 못 잡을 수 있다 — 이런
    경우 "성경본문" 카드에 "자동 추출 실패" 문구가 뜨는데, 실제로 그렇게
    나오면 알려주시면 그때 더 다듬으면 된다.
    """
    start_no = content.verse_start_no
    end_no = content.verse_end_no
    if not start_no or not end_no:
        return

    copyright_idx = text.find("저작권")
    if copyright_idx == -1:
        return
    scripture_end = text.rfind("\n", 0, copyright_idx)
    if scripture_end == -1:
        scripture_end = copyright_idx

    end_pat = re.compile(rf"^{re.escape(end_no)}$", re.MULTILINE)
    end_match = None
    for m in end_pat.finditer(text, 0, scripture_end):
        end_match = m  # 저작권 앞에서 가장 마지막에 나오는 절 번호 줄을 쓴다
    if not end_match:
        return

    start_pat = re.compile(rf"^{re.escape(start_no)}$", re.MULTILINE)
    start_match = None
    for m in start_pat.finditer(text, 0, end_match.start()):
        start_match = m  # 끝 절 번호 줄 바로 앞에서 가장 가까운 시작 절 번호 줄
    if not start_match:
        return

    chunk = text[start_match.start():scripture_end]
    lines = [l.strip() for l in chunk.split("\n") if l.strip()]
    content.verse_text = _clean("\n".join(lines))


def _extract_summary(content: TodayContent, text: str, start: int) -> None:
    """참조 줄 바로 뒤에 오는 짧은 요약 문단(성경 본문 자체가 아님)을 추출한다.

    start는 _extract_title_and_ref가 돌려준, 참조 줄이 끝나는 위치다 — 예전에는
    text.find(content.hymn)으로 다시 찾았는데, "찬송가 N장" 문구가 성경 본문
    쪽에도 나올 수 있어서 엉뚱한(앞쪽) 위치를 찾는 문제가 있었다.
    """
    if start is None or start < 0:
        return

    end = len(text)
    for anchor in (ANCHOR_SUMMARY, ANCHOR_GOD, "해설", TRANSLATION_LABEL):
        i = text.find(anchor, start)
        if i != -1:
            end = min(end, i)

    chunk = text[start:end]
    lines = [l.strip() for l in chunk.split("\n") if l.strip()]
    # 번역본 탭 이름이나 "— 개역개정" 같은 출처 표기는 요약 문단이 아니므로 제외
    lines = [
        l for l in lines
        if l not in _OTHER_TRANSLATION_NAMES and l != TRANSLATION_LABEL and not l.startswith("—")
    ]
    content.verse_summary = _clean("\n".join(lines))


def _extract_sections(content: TodayContent, text: str) -> None:
    idx_god = text.find(ANCHOR_GOD)
    idx_lesson = text.find(ANCHOR_LESSON)

    if idx_god != -1 and idx_lesson != -1:
        content.note_god = _strip_leading_qmark(_clean(text[idx_god + len(ANCHOR_GOD) : idx_lesson]))
    elif idx_god != -1:
        content.note_god = _strip_leading_qmark(
            _clean(text[idx_god + len(ANCHOR_GOD) : idx_god + len(ANCHOR_GOD) + 800])
        )

    if idx_lesson != -1:
        lesson_body_start = idx_lesson + len(ANCHOR_LESSON)

        # 교훈 섹션이 끝나고 기도문이 시작하는 지점("기도" 단독 줄)을 찾는다.
        prayer_hint_start = -1
        prayer_hint_len = 0
        for pat in PRAYER_HEADING_PATTERNS:
            m = pat.search(text, lesson_body_start)
            if m and (prayer_hint_start == -1 or m.start() < prayer_hint_start):
                prayer_hint_start = m.start()
                prayer_hint_len = len(m.group(0))

        lesson_end = prayer_hint_start if prayer_hint_start != -1 else len(text)
        content.note_lesson = _strip_leading_qmark(_clean(text[lesson_body_start:lesson_end]))

        if prayer_hint_start != -1:
            # "기도" 라벨 자체는 기도문 내용이 아니므로 그 뒤부터 시작한다.
            prayer_start = prayer_hint_start + prayer_hint_len

            # 기도문 끝 지점: 저작권 문구, 오디오해설 크레딧, 페이지 하단의
            # "매일성경 해설의 저작권은..." 문구가 시작되기 전까지.
            end_candidates = []
            for stop in ("저작권", "오디오해설", "본문낭독", "매일성경"):
                i = text.find(stop, prayer_start)
                if i != -1:
                    end_candidates.append(i)
            prayer_end = min(end_candidates) if end_candidates else min(len(text), prayer_start + 600)
            content.prayer = _clean(text[prayer_start:prayer_end])


def _extract_credits(content: TodayContent, text: str) -> None:
    # 실제 형식: "본문낭독:지음원(ERICA 한양대학교회)|오디오해설: 임하수(광주지부 총무)"
    # 두 이름 사이 구분자가 어떤 문자든 상관없이 "오디오해설"이라는 단어
    # 자체를 경계로 삼아 잘라낸다.
    m = re.search(r"본문낭독\s*[:：]\s*(.+?)\s*오디오해설\s*[:：]\s*([^\n]{2,40})", text)
    if m:
        content.reader_credit = m.group(1).strip(" |｜lL·-")
        content.commentator_credit = m.group(2).strip()
        return
    m = re.search(r"오디오해설\s*[:：]?\s*([^\n|]{2,30})", text)
    if m:
        content.commentator_credit = m.group(1).strip()


def _strip_leading_qmark(s: str) -> str:
    """소제목과 본문 사이에 "?"만 단독으로 있는 줄이 낄 때가 있어 제거한다."""
    lines = s.split("\n")
    if lines and lines[0].strip() in ("?", "？"):
        lines = lines[1:]
    return "\n".join(lines).strip()


def _clean(s: str) -> str:
    s = re.sub(r"\n{2,}", "\n", s.strip())
    return s.strip(" \n|·-")


if __name__ == "__main__":
    c = fetch_today()
    print("date:", c.date_str)
    print("title:", c.title)
    print("hymn:", c.hymn)
    print("verse_ref:", c.verse_ref, "/", c.verse_start_no, "~", c.verse_end_no)
    print("verse_text:", c.verse_text[:300])
    print("verse_summary:", c.verse_summary[:200])
    print("mp3:", c.mp3_url)
    print("note_god:", c.note_god[:200])
    print("note_lesson:", c.note_lesson[:200])
    print("prayer:", c.prayer[:200])
