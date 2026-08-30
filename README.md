# 매일성경 웹앱 (정표님 · 배우자님 전용)

성서유니온 매일성경(sum.su.or.kr)을 매일 자동으로 가져와 스마트폰에서 보는
개인용 웹앱. 기획안 v3 기준으로 만들었으며, **월 비용 $0**을 목표로
Render 무료 웹서비스 **한 개**만 사용한다 (Cron Job 등 유료 기능 없음).

## 동작 방식 (꼭 읽어주세요)

- 스케줄러가 없다. **그날 처음 접속하는 사람**이 화면을 열면 그 요청이
  트리거가 되어 서버가 스크래핑 → mp3 다운로드 → whisper.cpp STT를
  백그라운드에서 수행한다. 처리 중에는 "준비 중" 화면이 5초마다 자동으로
  완료 여부를 확인하다가, 끝나면 새로고침된다.
- 결과는 프로세스 메모리에만 저장된다(당일 1건, 매일 덮어쓰기). Render 무료
  플랜은 persistent disk가 없고 15분간 접속이 없으면 서버가 잠들기 때문에,
  두 분의 접속 시간이 15분 이상 벌어지면 하루 중 다시 한 번(드물게 그 이상)
  재처리가 일어날 수 있다 — 그래도 비용은 항상 $0.

## 파일 구성

```
app.py               Flask 앱, 온디맨드 캐시, Basic Auth
scraper.py           sum.su.or.kr 파싱 (텍스트 앵커 기반, 아래 "확인 필요" 참조)
stt.py                whisper.cpp(pywhispercpp)로 오디오 → 텍스트
templates/index.html          오늘의 말씀 화면 (제주 자연광 팔레트)
templates/processing.html     "준비 중" 화면(폴링)
requirements.txt
```

## 로컬에서 확인한 것 / 못한 것

이 코드는 개발 환경의 네트워크 정책상 아래 항목은 **끝까지 검증하지
못했다**. Render에 배포한 뒤 반드시 실제로 확인해야 한다.

1. **scraper.py의 실제 추출 정확도** — sum.su.or.kr의 실제 HTML을 이
   환경에서는 열람할 수 없어(접속 차단), 페이지 텍스트에 등장하는 고정
   문구를 기준으로 앞뒤를 잘라내는 방식으로 작성했다. 배포 후 첫 실행에서
   `verse_text` / `note_god` / `note_lesson` / `prayer`가 제대로 나오는지
   확인하고, 비어 있거나 이상하면 `scraper.py`의 앵커 문자열을 조정해야
   한다.
2. **whisper.cpp 모델 다운로드** — `pywhispercpp` 설치 자체(사전 컴파일된
   wheel)는 이 환경에서 성공적으로 확인했지만, 첫 실행 시 모델 파일을
   huggingface.co에서 내려받는 과정은 이 환경의 네트워크 제한으로 검증하지
   못했다. Render는 일반 인터넷 접속이 열려 있어야 정상 동작한다.
3. **오디오 핫링크 재생** — `mp3.dailybible.or.kr`가 외부 사이트에서의
   재생을 막아두지 않았는지 실제 배포 환경에서 확인이 필요하다(기획안
   09절).

로컬에서 실제로 통과시킨 테스트: Basic Auth 401/200 동작, 온디맨드 트리거
및 "준비 중" → "완료" 폴링 흐름, 샘플 데이터로 index.html 렌더링(개역개정
표기가 본문 하단에만 나오는 것 포함).

## Render 배포 방법

Render는 Git 저장소를 연결해야 배포할 수 있다(이 세션에는 GitHub 연동
도구가 없어 직접 push는 못 했다). 아래 순서로 진행하면 된다.

1. 이 폴더를 GitHub 저장소(비공개로 생성 권장)에 올린다.
2. Render 대시보드 → New → Web Service → 방금 만든 저장소 연결.
3. 아래 값으로 설정:
   - **Runtime**: Python
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn app:app --workers 1 --threads 4 --timeout 120`
     - `--workers 1`이 중요하다 — 오늘의 콘텐츠를 프로세스 메모리에만
       저장하므로, worker가 2개 이상이면 서로 다른 캐시를 가져 뒤죽박죽이
       된다.
   - **Plan**: Free
4. 환경변수(Environment) 추가:
   - `APP_USER` — Basic Auth 아이디 (예: `jeongpyo`)
   - `APP_PASSWORD` — Basic Auth 비밀번호 (두 분만 아는 값으로 설정, 반드시
     지정할 것 — 비어 있으면 앱이 항상 401을 반환하도록 만들어 두었다)
5. 배포 후 스마트폰 브라우저로 접속해 아이디/비밀번호를 입력하면 "준비
   중" 화면이 뜨고, 1~2분 뒤 오늘의 말씀이 표시되는지 확인한다.

## 다음에 손볼 만한 것

- `scraper.py` 앵커 조정(위 1번)
- whisper.cpp `base-q8_0`로도 한국어 정확도가 부족하면 `small`로 올리는
  선택지(단, RAM이 늘어나 무료 플랜 한도를 넘을 수 있어 유료 전환 여부를
  다시 논의해야 함 — 기획안 08절)
- Basic Auth 세션이 스마트폰 브라우저에서 얼마나 오래 유지되는지 실사용
  확인
