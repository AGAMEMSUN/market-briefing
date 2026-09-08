---
name: brief
description: 텔레그램 채널 소식을 중복 제거해 정리하고 시장 지표와 함께 대시보드 Artifact 로 갱신한다. "브리핑 줘", "오늘 시황 보여줘", "시장 브리핑" 같은 요청에 사용한다. 수집만 하려면 brief-collect, 토픽만 보려면 brief-topics 를 쓴다.
---

# 브리핑 오케스트레이터

온디맨드 시황 브리핑 파이프라인의 본체. 다른 brief-* 스킬은 여기의 특정 단계만
실행하는 얇은 진입점이다.

설계 근거: `docs/superpowers/specs/2026-09-06-market-briefing-agent-design.md`

## 전체 순서

### 1. 수집 (파이썬)

```bash
PYTHONIOENCODING=utf-8 python scripts/telegram_digest.py
PYTHONIOENCODING=utf-8 python scripts/market_snapshot.py
PYTHONIOENCODING=utf-8 python scripts/build_index.py
```

구독 중인 브로드캐스트 채널 전체를 수집한다. 제외하려면 `scripts/channels.py` 의
`EXCLUDE_TITLES` 에 정확한 제목을 넣는다. 시세는 30분 TTL 캐시가 있어 같은 날
재호출하면 다시 받지 않는다. 세션 오류가 나면 사용자에게 별도 터미널에서
`telegram_login_setup.py` 실행을 요청한다.

### 2. 토픽 클러스터링 (직접 수행)

`scripts/.state/cluster_view.txt` **만** 읽는다. 인덱스 JSON 이나
원문을 읽지 마라 — 뷰가 이미 노이즈를 빼고 헤드라인을 70자로 자른 결과다.
실측 기준 원문의 15%(97,206자 -> 15,013자)다.

토픽 4~7개로 묶어 `.state/topics.json` 에 쓴다.

```json
{"date": "2026-09-08", "topics": [{
  "topic_id": "t1", "title": "이란-미국 해상 충돌 격화",
  "primary_channel": "미국 주식 인사이더 🇺🇸 (US Stocks Insider)",
  "merged_channels": ["급등일보 미국주식🇺🇸 속보·매크로·리서치"],
  "message_refs": [{"channel": "미국 주식 인사이더 🇺🇸 (US Stocks Insider)", "index": 99},
                   {"channel": "급등일보 미국주식🇺🇸 속보·매크로·리서치", "index": 170}]}]}
```

**`index` 는 `cluster_view.txt` 의 `[N]` 값을 그대로 쓴다 — 문서 전체 0-based 순번이지
채널별 순번이 아니다.** 채널명도 뷰 헤더에서 `(n/m)` 을 뗀 형태와 정확히 일치해야
한다. 둘 중 하나라도 어긋나면 그 토픽의 슬라이스가 비고 3단계가 실패한다.

primary 채널은 그 이슈를 가장 자세히 다룬 채널로 정한다. 결정적 중복 탐지는 같은
기사를 퍼온 경우만 잡는다 — 채널마다 출처와 문장이 달라서다. 실제 병합은 이 단계의
판단이 한다.

**금리·채권 파급은 한 토픽이 맡는다.** 유가·원자재·환율 토픽이 각자 "→물가→금리"를
전개하면 세 섹션이 같은 말을 하고 US 10Y 칩이 세 번 붙는다(실측). 금리 토픽을 하나
두고 나머지는 그쪽을 가리키게 한다.

뷰 머리말의 `noise` 줄은 파이썬이 걸러낸 건수다. `not_checked` 는 수집이 중간에
끊겨 **조회조차 못 한** 채널이라 `quiet`(오늘 새 글 없음)와 다르다 — "새 글 없음"
이라고 쓰면 안 된다.

### 3. 섹션 입력 추출 (파이썬)

```bash
PYTHONIOENCODING=utf-8 python scripts/extract_sections.py
```

`.state/sections/input/<topic_id>.md` 가 생긴다. 섹션 작성 에이전트는 이 파일만
읽으면 되고 원문을 Grep 하지 않는다.

이 스크립트가 **지난 실행의 잔여 슬라이스·섹션을 지운다.** 예전에 남아 있던
`t7.md` 를 검수 에이전트가 읽고 "t7 섹션 누락"이라는 없는 문제를 보고한 적이 있다.
슬라이스가 하나라도 비면 여기서 멈추고 `index`/채널명을 다시 보라고 알려준다.

### 4. 섹션 작성 (서브에이전트 병렬)

토픽마다 `briefing-section-writer` 를 하나씩 띄우되 **한 번에 최대 3개씩 배치**로
나눈다. 7개를 동시에 띄우면 세션 한도(rate limit)에 걸려 전원 실패한다 — 실측됨.
각 프롬프트에 topic_id·title·primary_channel·merged_channels 와 아래 경로를 넣는다.

- input_path: `scripts/.state/sections/input/<topic_id>.md` (자기 토픽 원문, 이미 잘려 있음)
- snapshot_path: `scripts/.state/snapshot_brief.json`
- out_path: `scripts/.state/sections/<topic_id>.json`

원문은 이미 토픽별로 잘려 있으므로 `message_refs` 나 타임스탬프를 넘길 필요가 없다.
에이전트는 `input_path` 파일 하나만 읽는다.

**`snapshot_brief.json` 을 넘겨라.** `market_snapshot.json` 은 시계열이 들어 있어
40KB 이고, 에이전트 7개가 각자 읽으면 5만 토큰이 그냥 나간다. brief 는 1KB 다.

세션 한도로 서브에이전트를 못 띄우는 상황이면 오케스트레이터가 직접 섹션을 쓰되,
웹 교차검증을 못 했다는 사실을 각 섹션의 `verification_notes` 와 대시보드 하단에
명시한다.

### 5. 검수 (조건부)

섹션이 6개 이상이면 검수 뷰를 만들고 `briefing-reviewer` 를 띄운다.

```bash
PYTHONIOENCODING=utf-8 python scripts/review_view.py
```

프롬프트에는 `review_view_path: scripts/.state/review_view.txt` **하나만** 넘긴다.
섹션 디렉터리나 인덱스 경로를 같이 주면 에이전트가 그걸 다 읽어 예전처럼 190K
토큰을 쓴다 — 뷰가 이미 수치 대조표와 누락 후보를 계산해 담고 있다.

지적은 전부 반영하지 않는다. 뷰의 누락 후보에는 잡음이 섞여 있고, 중복 지적도
합칠 가치가 있는지는 이 단계에서 판단한다. 반영하기로 한 것만 섹션 JSON 을 고친다.

### 6. 렌더

HTML 을 쓰지 마라. **payload 도 손으로 만들지 마라.**

```bash
PYTHONIOENCODING=utf-8 python scripts/build_payload.py
PYTHONIOENCODING=utf-8 python scripts/render_dashboard.py
```

`build_payload.py` 가 섹션 JSON 7개와 스냅샷을 읽어 `.state/payload.json` 을 만든다.
오케스트레이터가 섹션 JSON 을 컨텍스트로 읽을 이유가 없다(7개 = 15K 토큰).
레이아웃은 `templates/dashboard.html` 에 고정돼 있어 실행마다 변하지 않는다 —
이것이 모델이 바뀌어도 결과물이 흔들리지 않는 이유다.

payload 스키마는 `docs/superpowers/specs/2026-09-06-brief-efficiency-design.md`
의 "데이터 계약" 절에 있다.

**직접 문자열 치환으로 주입하지 마라.** payload 에는 텔레그램 채널이 쓴 제3자
본문이 들어가고, 본문에 `</script>` 가 있으면 스크립트 블록이 거기서 끝나 나머지가
문서에 HTML 로 주입된다(실측으로 재현됨). `render_dashboard.py` 가 그 이스케이프를
담당한다. 산출물은 `.state/dashboard.html` 이고, 이 파일을 Artifact 로 발행한다.

지표는 `spark` 가 있으면 스파크라인을, 없으면 `note` 를 표시한다. 판정은
`build_payload.py` 가 한다 — **0 이하 값이나 일간 50% 초과처럼 물리적으로 불가능한
경우에만** 뺀다. 변동이 크다는 이유로 빼지 마라. 그건 장세지 오류가 아니다.

출처는 지표마다 다르다. 코스피·코스닥·국고채 10Y 는 **토스증권 Open API**(KRX 원천),
S&P500·나스닥·원달러·미 10년물·VIX 는 야후다.

**토스를 쓰는 이유는 당일 종가 정확도다.** 야후는 장중 조회 시 그 시점 값을
`as_of=오늘` 로 돌려줘 종가처럼 보인다 — 2026-09-08 에 야후 7,046.74 vs 실제 종가
6,954.52 로 92포인트 차이가 났고 "7000선 회복"이라는 틀린 제목이 나갔다. 과거
시계열은 두 출처가 65일 중 64일 일치하므로 야후도 정확하다.

자격증명(`.env` 의 `TOSS_CLIENT_ID`/`TOSS_CLIENT_SECRET`)이 없으면 경고를 찍고
야후로 폴백하고, 국고채 10Y 는 야후에 없어 지표에서 빠진다. 토스는 **허용 IP**
기반이라 회선 IP 가 바뀌면 403 이 난다 — 오류 메시지가 조치법을 알려준다.

발행: 최초 1회는 `favicon` 을 붙여 발행하고 URL 을 `DASHBOARD_URL.txt`
에 기록한다. 이후에는 그 URL 을 `url` 인자로 넘겨 같은 주소를 갱신하고 `favicon` 은
넘기지 않는다.

채팅에는 링크와 3~5줄 요약만 남긴다. 섹션 전문을 복사하지 마라.

## 하지 않는 것

- 원문 다이제스트 전체를 오케스트레이터 컨텍스트에 적재하지 않는다. 2단계는
  클러스터 뷰만, 원문은 섹션 작성 에이전트가 각자 자기 몫만 읽는다.
- **오케스트레이터가 읽는 파일은 `cluster_view.txt` 하나다.** 섹션 JSON(15K 토큰),
  `market_snapshot.json`(8K), `briefing_index.json`(33K) 은 전부 파이썬이 처리한다.
  값 하나가 궁금하면 파일을 열지 말고 한 줄짜리 python -c 로 그것만 찍어라.
- 채널당 에이전트 1개로 팬아웃하지 않는다. 그 구조로는 채널 간 중복을 볼 수 없다.
- 수집·중복탐지를 에이전트에게 시키지 않는다. 결정적 작업은 파이썬이 한다.
- 군더더기 문구를 쓰지 않는다. 단계 설명·대시보드 카피·채팅 보고 모두 사실과
  판단만 남기고, 상투어와 이미 아는 내용의 재진술을 뺀다. 근거 없는 형용사보다
  측정값을 쓴다.
