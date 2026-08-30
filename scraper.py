"""
sum.su.or.kr(성서유니온 매일성경)의 오늘자 페이지를 가져와
성경본문 / 해설 / 기도문 / 오디오 URL을 추출한다.

주의(중요): 이 파서는 실제 페이지의 HTML class/id 구조를 직접 보고 만든 것이
아니라, 페이지 텍스트에 등장하는 고정 문구("하나님은 어떤 분입니까" 등)를
기준으로 앞뒤를 잘라내는 "텍스트 앵커" 방식이다. 원본 사이트의 문구가 바뀌면
파싱이 깨질 수 있으니, 배포 후 실제 결과를 한 번 확인하고 필요하면
_split_sections()의 앵커 문자열을 조정할 것.
"""
from __future__ import annotations

import re
import datetime as dt
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://sum.su.or.kr:8888/bible/today"
MP3_URL_TMPL = "https://mp3.dailybible.or.kr/kor/{year}/{ymd}.mp3"

# 정표님 요청: 개역개정 하나만 쓴다. 다른 번역본 탭은 만들지 않는다.
TRANSLATION_LABEL = "개역개정"

# 페이지에 여러 번역본 탭이 나열될 때 흔히 같이 등장하는 이름들 — 혹시 본문 앞에
# 탭 이름이 한 줄로 섞여 나오면 건너뛰기 위한 참고용 목록(추출 대상 아님).
_OTHER_TRANSLATION_NAMES = ["개역한글", "쉬운성경", "새번역", "ESV"]

# 해설/기도문/본문을 잘라내는 기준이 되는 고정 문구들.
ANCHOR_SUMMARY = "본문요약"
ANCHOR_GOD = "하나님은 어떤 분입니까"
ANCHOR_LESSON = "내게 주시는 교훈은 무엇입니까"
ANCHOR_PRAYER_HINTS = ["기도", "열방을 위한 기도"]


@dataclass
class TodayContent:
    date_str: str  # YYYY-MM-DD
    title: str = ""
    hymn: str = ""
    verse_ref: str = ""
    verse_text: str = ""     # 개역개정 본문
    note_god: str = ""       # 하나님은 어떤 분입니까
    note_lesson: str = ""    # 내게 주시는 교훈은 무엇입니까
    prayer: str = ""
    mp3_url: str = ""
    reader_credit: str = ""
    commentator_credit: str = ""
    raw_text: str = ""       # 디버깅용 원문 전체 텍스트


def build_mp3_url(target_date: dt.date) -> str:
    return MP3_URL_TMPL.format(year=target_date.year, ymd=target_date.strftime("%Y%m%d"))


def fetch_today(target_date: dt.date | None = None, timeout: int = 20) -> TodayContent:
    """오늘(또는 지정한 날짜)의 매일성경 콘텐츠를 가져온다.

    주의: 이 함수는 실제 인터넷에 나가는 요청을 던진다. 원본 사이트가
    날짜별 접속 URL을 어떻게 받는지(쿼리 파라미터 등)는 확인되지 않았으므로,
    우선 "오늘" 페이지만 지원한다. 오디오 URL은 날짜로부터 결정적으로
    계산되므로 스크래핑 성공 여부와 무관하게 항상 만들어진다.
    """
    target_date = target_date or dt.date.today()

    resp = requests.get(
        BASE_URL,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (compatible; DailyBibleApp/1.0; personal-use)"},
    )
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text("\n", strip=True)

    content = TodayContent(date_str=target_date.isoformat(), raw_text=text)
    content.mp3_url = build_mp3_url(target_date)

    _extract_title_and_hymn(content, text)
    _extract_verse_ref(content, text)
    _extract_verse_text(content, text)
    _extract_sections(content, text)
    _extract_credits(content, text)

    return content


def _extract_title_and_hymn(content: TodayContent, text: str) -> None:
    # 찬송가 N장 패턴
    m = re.search(r"찬송가\s*\d+\s*장", text)
    if m:
        content.hymn = m.group(0)

    # 날짜 바로 다음 줄들 중 너무 짧지 않은 첫 줄을 제목으로 추정
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    date_pat = re.compile(r"\d{4}[.\-]\d{2}[.\-]\d{2}")
    for i, line in enumerate(lines):
        if date_pat.search(line):
            for cand in lines[i + 1 : i + 5]:
                if 2 <= len(cand) <= 40 and "찬송가" not in cand:
                    content.title = cand
                    break
            break


def _extract_verse_ref(content: TodayContent, text: str) -> None:
    # 예: "이사야 37:21-37:38" 또는 "이사야 37:21~38"
    m = re.search(r"[가-힣]{2,6}\s?\d{1,3}[:장]\d{1,3}[-~]\d{0,3}[:]?\d{0,3}", text)
    if m:
        content.verse_ref = m.group(0)


def _extract_verse_text(content: TodayContent, text: str) -> None:
    """개역개정 본문만 추출한다.

    기준: 찬송가 표기(또는 절 참조) 다음부터, "본문요약" 또는 해설 시작
    문구 이전까지를 본문으로 간주한다. 그 사이에 다른 번역본 이름이 한 줄로만
    있으면(탭 라벨) 건너뛴다.
    """
    start = -1
    if content.hymn:
        start = text.find(content.hymn)
        if start != -1:
            start += len(content.hymn)
    if start == -1 and content.verse_ref:
        start = text.find(content.verse_ref)
        if start != -1:
            start += len(content.verse_ref)
    if start == -1:
        return

    end = len(text)
    for anchor in (ANCHOR_SUMMARY, ANCHOR_GOD):
        i = text.find(anchor, start)
        if i != -1:
            end = min(end, i)

    chunk = text[start:end]
    lines = [l.strip() for l in chunk.split("\n") if l.strip()]
    # 번역본 탭 이름만 있는 줄은 제외
    lines = [l for l in lines if l not in _OTHER_TRANSLATION_NAMES and l != TRANSLATION_LABEL]
    content.verse_text = _clean("\n".join(lines))


def _extract_sections(content: TodayContent, text: str) -> None:
    idx_god = text.find(ANCHOR_GOD)
    idx_lesson = text.find(ANCHOR_LESSON)

    if idx_god != -1 and idx_lesson != -1:
        content.note_god = _clean(text[idx_god + len(ANCHOR_GOD) : idx_lesson])
    elif idx_god != -1:
        content.note_god = _clean(text[idx_god + len(ANCHOR_GOD) : idx_god + len(ANCHOR_GOD) + 800])

    if idx_lesson != -1:
        # 기도문 시작 지점을 찾아 그 앞까지를 교훈 섹션으로 자른다
        end = len(text)
        for hint in ANCHOR_PRAYER_HINTS:
            i = text.find(hint, idx_lesson)
            if i != -1:
                end = min(end, i)
        content.note_lesson = _clean(text[idx_lesson + len(ANCHOR_LESSON) : end])

        # 기도문: "교훈" 섹션 끝부터 저작권 문구 전까지
        prayer_start = end
        copyright_idx = text.find("저작권", prayer_start)
        prayer_end = copyright_idx if copyright_idx != -1 else min(len(text), prayer_start + 600)
        content.prayer = _clean(text[prayer_start:prayer_end])


def _extract_credits(content: TodayContent, text: str) -> None:
    m = re.search(r"본문낭독\s*[:：]?\s*([^\n|]{2,20})", text)
    if m:
        content.reader_credit = m.group(1).strip()
    m = re.search(r"오디오해설\s*[:：]?\s*([^\n|]{2,30})", text)
    if m:
        content.commentator_credit = m.group(1).strip()


def _clean(s: str) -> str:
    s = re.sub(r"\n{2,}", "\n", s.strip())
    return s.strip(" \n|·-")


if __name__ == "__main__":
    c = fetch_today()
    print("date:", c.date_str)
    print("title:", c.title)
    print("hymn:", c.hymn)
    print("verse_ref:", c.verse_ref)
    print("verse_text:", c.verse_text[:200])
    print("mp3:", c.mp3_url)
    print("note_god:", c.note_god[:200])
    print("note_lesson:", c.note_lesson[:200])
    print("prayer:", c.prayer[:200])
