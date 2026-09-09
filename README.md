# Market Briefing

구독 중인 텔레그램 채널을 훑어 **중복을 걷어내고 토픽별로 정리한 시황 대시보드**를
만드는 Claude Code 파이프라인. `/brief` 한 번이면 수집 → 시세 조회 → 노이즈 필터 →
토픽 클러스터링 → 섹션 작성(서브에이전트 병렬) → 검수 → 대시보드 Artifact 발행까지
돌아간다.

텔레그램으로 시황을
받아보는 사람이면 그대로 쓸 수 있다. 본인 API 키만 넣으면 된다.

> **인사이트는 무엇을 읽어내야 하는지에 대한 정리다.** 이 도구는 사실관계 정리와
> 논점 제시까지 한다. 최종 판단·투자의견은 사람이 쓴다.

| 명령 | 동작 |
|---|---|
| `/brief` | 전체 파이프라인 |
| `/brief-collect` | 수집만 |
| `/brief-topics` | 토픽 목록만 |
| `/brief-dig <토픽>` | 특정 토픽 심층 |

---

## 설치

### 0. 요구사항

- Python 3.10+
- [Claude Code](https://claude.com/claude-code)
- 텔레그램 계정 (시황 채널을 구독 중이어야 의미가 있다)

### 1. 클론 + 의존성

```bash
git clone https://github.com/<your-account>/market-briefing.git
cd market-briefing
pip install -r requirements.txt
```

### 2. API 키

```bash
cp .env.example .env   # Windows: copy .env.example .env
```

`.env` 를 열어 값을 채운다. 필요한 키와 발급 방법은 파일 안에 적혀 있다.

| 키 | 필수 | 발급처 / 용도 |
|---|---|---|
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | **필수** | https://my.telegram.org → API development tools |
| `TOSS_CLIENT_ID` / `TOSS_CLIENT_SECRET` | 선택 | 토스증권 앱 → 설정 → Open API |

`.env` 는 `.gitignore` 에 등록돼 있어 커밋되지 않는다. 사용자별 설정이 전부 여기
모여 있어서, 이 저장소를 클론해도 남의 계정·소속·대시보드 주소가 따라오지 않는다.

### 3. 텔레그램 로그인 (최초 1회)

```bash
python scripts/telegram_login_setup.py
```

전화번호와 인증코드를 입력하는 **대화형** 스크립트라 반드시 직접 터미널에서
실행해야 한다(Claude Code 툴 호출로는 불가). 성공하면
`scripts/.state/session.session` 이 생기고, 이후로는 자동 수집이 된다.

### 4. 스킬 연결

Claude Code 는 슬래시 명령을 `.claude/skills/` 에서만 찾는다. 이 저장소는 스킬
원본을 `.agents/skills/` 에 두므로 한 번 연결해줘야 한다.

```bash
./setup-skills.sh          # macOS / Linux
powershell -File setup-skills.ps1   # Windows
```

이제 Claude Code 에서 `/brief` 를 부르면 된다.

---

## 토스증권 Open API (선택)

없어도 동작한다. 넣으면 코스피·코스닥·국고채 금리를 KRX 원천으로 받는다.

**왜 쓰나** — 야후는 장중에 조회하면 그 시점 값을 `as_of=오늘` 로 돌려줘 종가처럼
보인다. 2026-09-08 실측에서 야후 7,046.74 vs 실제 종가 6,954.52 로 **92포인트**
차이가 났고, 그 탓에 "코스피 7000선 회복"이라는 틀린 제목이 나갔다. 과거 시계열은
두 출처가 65거래일 중 64일 일치하므로 야후도 정확하다 — 문제는 당일 값뿐이다.

**주의 두 가지**

1. `Client Secret` 은 발급 시 **한 번만** 표시된다. 놓치면 재발급해야 하고,
   재발급하면 `Client Id` 도 함께 바뀔 수 있다.
2. **허용 IP** 기반이다. 현재 공인 IP(`curl https://api.ipify.org`)를 등록하지
   않으면 403 이 난다. 가정용 회선은 IP 가 바뀌므로 종종 다시 등록해야 한다.

```bash
python scripts/toss_client.py   # 연결 점검. 실패하면 원인과 조치법을 알려준다
```

토스 심볼 카탈로그에 없는 S&P500·나스닥·원/달러·미 국채 10년·VIX 는 야후를 쓴다.

---

## 동작 방식

```
telegram_digest.py   구독 채널 전수 수집        → daily/YYYY-MM-DD.md
market_snapshot.py   지표 11종 + 섹터 11종      → .state/market_snapshot.json
                                                  .state/snapshot_brief.json
build_index.py       노이즈 필터 + 중복 탐지    → .state/cluster_view.txt
   ↓ 오케스트레이터가 cluster_view 만 읽고 토픽 4~7개로 묶는다
extract_sections.py  토픽별 원문 슬라이스        → .state/sections/input/<id>.md
   ↓ briefing-section-writer 를 토픽마다 병렬로(3개씩 배치)
review_view.py       수치 대조표 + 누락 클러스터 → .state/review_view.txt
   ↓ briefing-reviewer 가 이 파일 하나만 읽고 검수
build_payload.py     섹션 + 지표 합성            → .state/payload.json
render_dashboard.py  템플릿에 주입              → .state/dashboard.html → Artifact
```

**설계 원칙: 결정적인 일은 파이썬이, 판단은 에이전트가.** 수집·중복탐지·노이즈
필터·수치 대조·누락 탐지는 전부 파이썬이 한다. 에이전트는 "무엇을 한 토픽으로
묶을지", "무엇이 시장에 의미 있는지"만 판단한다. 그래서 모델이 바뀌어도 결과물
구조가 흔들리지 않는다.

레이아웃도 `templates/dashboard.html` 에 고정돼 있다. 모델은 HTML 을 쓰지 않고
JSON payload 만 만든다.

설계 문서: `docs/superpowers/specs/`

---

## 폴더 구성

| 경로 | 내용 |
|---|---|
| `.agents/skills/` | 슬래시 명령 정의 (`brief`, `brief-collect`, …) |
| `.claude/agents/` | 서브에이전트 정의 (섹션 작성자, 검수자) |
| `scripts/telegram_login_setup.py` | 최초 1회 대화형 로그인 |
| `scripts/telegram_digest.py` | 구독 채널 전수 수집 |
| `scripts/channels.py` | 수집 제외 목록 + 협찬성 필터 대상 채널 |
| `scripts/channel_selection.py` | 브로드캐스트 채널만 고르는 필터 |
| `scripts/digest_manifest.py` | 수집 결과 매니페스트 기록 |
| `scripts/market_snapshot.py` | 지표 조회 (토스 + 야후) |
| `scripts/toss_client.py` | 토스증권 Open API 클라이언트 |
| `scripts/noise_filter.py` | 저신호·협찬성·채널내 근사중복 주석 |
| `scripts/build_index.py` | 축약 인덱스 + 중복 탐지 + 클러스터 뷰 |
| `scripts/extract_sections.py` | 토픽별 원문 슬라이스 |
| `scripts/review_view.py` | 검수용 경량 뷰 (수치 대조·누락 클러스터) |
| `scripts/build_payload.py` | 섹션 + 지표 → payload |
| `scripts/render_dashboard.py` | payload 주입 + `<script>` 이스케이프 |
| `templates/dashboard.html` | 대시보드 고정 템플릿 |
| `templates/briefing_template.md` | 블로그 원고 + 노션 제출 텍스트 템플릿 |
| `tests/` | 파이썬 모듈 단위 테스트 (`python -m pytest tests/`) |
| `docs/superpowers/` | 설계 문서(specs)와 구현 계획(plans) |
| `archive/` | 완성한 브리핑 원고 보관 |
| `daily/` | 수집 원문 — **git 에 올라가지 않음** |
| `scripts/.state/` | 파이프라인 중간 산출물·세션·토큰 — **git 에 올라가지 않음** |

---

## 개인정보·저작권

- 수집 대상은 `is_channel and not is_group` 필터로 **브로드캐스트 채널만**이다.
  그룹·1:1 대화는 구조적으로 제외되므로 개인 대화가 수집될 여지가 없다.
- 채널은 **읽기 전용**으로만 접근한다 (전송·참여·설정변경 없음).
- 세션 파일과 수집 원문(`daily/`)은 민감정보 + 제3자 저작물이라 `.gitignore` 에
  등록돼 있고 로컬에만 남는다.
- 텔레그램이 요청을 제한하면 재시도 없이 즉시 멈추고 `.state/run_log.jsonl` 에
  기록한다.
- 대시보드 Artifact 는 기본 비공개다. 공유 여부는 본인이 정한다.

---

## 알려진 한계

- **협찬성 필터는 채널 지정 opt-in.** 전역 기계 판정이 성립하지 않는다 —
  `우리銀, 아시안게임 승리 기원…연 7.5% 적금 출시` 같은 진짜 뉴스가 협찬 기사와
  같은 형태다. `channels.py` 의 `ADVERTORIAL_CHANNELS` 에 등록된 채널에서만
  적용하며, 알려진 오탐은 테스트로 고정해뒀다.
- **결정적 중복 탐지는 같은 기사를 퍼온 경우만 잡는다.** 채널마다 출처와 문장이
  달라 대부분 안 걸린다. 의미 수준 병합은 토픽 클러스터링 단계가 담당한다.
- **누락 탐지 클러스터링은 형태소 분석을 안 한다.** `원전주` 가 `원전` 으로
  쪼개지지 않아 같은 주제가 따로 잡힐 수 있다. 후보를 제시할 뿐 최종 판단은
  에이전트가 한다.
- **텔레그램 레이트리밋.** 채널이 많으면 수집 도중 제한에 걸려 몇 분 대기해야
  할 수 있다. 조회조차 못 한 채널은 `not_checked` 로 구분되며, "오늘 새 글 없음"
  (`quiet`)과 다르게 표시된다.

## 테스트

```bash
python -m pytest tests/ -q
```
