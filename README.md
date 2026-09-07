# Market Briefing (KECOBUGS)

KECOBUGS 26th 매주 제출용 "마켓브리핑" 작성을 돕는 대화형 워크플로.
다른 리포트 파이프라인(`equity-report` 등)과 달리 정형 데이터 계산이 아니라
**이슈 선정 + 시황 정리 + 본인 견해**를 쓰는 질적 작업이라 별도 Python 파이프라인
없이 에이전트 스킬(`.agents/skills/market-briefing/SKILL.md`)로 진행한다.

## 온디맨드 브리핑 (`/brief`)

호출하면 텔레그램 수집 → 시세 조회 → 중복 제거 → 토픽별 섹션 작성 → 대시보드
Artifact 갱신까지 한 번에 돌린다. 대시보드 주소는 `DASHBOARD_URL.txt` 에 있고
매 호출마다 같은 주소가 갱신된다.

| 명령 | 동작 |
|---|---|
| `/brief` | 전체 파이프라인 |
| `/brief-collect` | 수집만 |
| `/brief-topics` | 토픽 목록만 |
| `/brief-dig <토픽>` | 특정 토픽 심층 |

설계: `docs/superpowers/specs/2026-09-06-market-briefing-agent-design.md`
계획: `docs/superpowers/plans/2026-09-06-market-briefing-dashboard.md`

### 알려진 한계

- **코스피·코스닥 추이 데이터 신뢰 불가**: `market_snapshot.py` 가 쓰는 야후
  `^KS11`/`^KQ11` 시계열에 비정상적인 일간 변동이 섞여 있다(64거래일 중 각
  20일·16일이 5% 초과, 최대 17.9%). 현재가만 참고용으로 쓰고 추이 차트는
  대시보드에서 제외한다. 해외 지수·환율·금리는 정상.
- **협찬성 필터는 채널 지정 opt-in**: 전역 기계 판정이 성립하지 않아
  (`우리銀, 아시안게임 승리 기원...연 7.5% 적금 출시` 같은 진짜 뉴스가 같은 형태다)
  `channels.py` 의 `ADVERTORIAL_CHANNELS` 에 등록된 채널에서만 적용한다. 필터는
  삭제가 아니라 주석이라 전체 인덱스에는 모든 메시지가 남고, 경량 뷰에서만 빠진다.
  알려진 오탐은 `test_advertorial_known_false_positive_is_documented` 에 고정해뒀다.
- **결정적 중복 탐지의 실효**: URL·문자 n-gram 방식은 같은 기사를 퍼온 경우만
  잡는다. 실제 채널 간 중복은 출처와 문장이 달라 대부분 걸리지 않으므로,
  의미 수준 병합은 오케스트레이터의 토픽 클러스터링 단계가 담당한다.
- **축약 인덱스 크기**: 인덱스 JSON 은 메시지당 메타데이터 때문에 원문보다 커질 수
  있다. 오케스트레이터가 읽는 것은 인덱스가 아니라 `cluster_view.txt` 이며, 실측
  기준 원문의 15%(97,206자 -> 15,013자)다.

## 진행 방식 요약

1. 텔레그램은 `scripts/telegram_digest.py`가 매일 정오에 자동으로 새 메시지를
   모아 `daily/YYYY-MM-DD.md`에 저장해둔다 (유튜브·카카오톡은 자동화 대상 아님,
   필요하면 사용자가 원문을 붙여넣는다).
2. 에이전트가 `daily/`에 쌓인 원문 + 웹에서 접근 가능한 소스(메르의 블로그,
   한국경제/매일경제, KDI, KCIF, thebell, 딜사이트 등)를 검색해 이슈 후보를
   정리한다.
3. 후보 중 이번 주에 쓸 이슈를 고르면, 에이전트가 시황 정리 + **초안 인사이트**를
   제안한다 (초안일 뿐, 최종 견해는 사용자가 수정). 자동 수집된 다이제스트는
   최종본이 아니라 참고용 원자재이므로 그대로 베끼지 않는다.
4. 사용자가 인사이트를 다듬으면 에이전트가 네이버 블로그에 바로 붙여넣을 최종
   원고를 완성한다.
5. 사용자가 원고를 네이버 블로그에 직접 게시하고 URL을 알려주면, 에이전트가
   노션 "Market Briefing" 페이지에 붙여넣을 제출용 텍스트(링크 포함)를 만들어준다.
6. 완성된 원고는 `archive/YYYY-Wxx.md`에 기록해둔다.

자세한 단계별 절차는 `.agents/skills/market-briefing/SKILL.md` 참고.

## 폴더 구성

| 경로 | 내용 |
|---|---|
| `archive/` | 매주 완성한 브리핑 원고 보관 (`2026-W36.md` 형식) |
| `templates/briefing_template.md` | 블로그 원고 + 노션 제출 텍스트 템플릿 |
| `scripts/channels.py` | 수집 제외 목록 + 협찬성 필터 대상 채널 |
| `scripts/telegram_login_setup.py` | 최초 1회, 사용자가 직접 터미널에서 실행하는 로그인 스크립트 |
| `scripts/telegram_digest.py` | 정오 작업 스케줄러가 실행하는 수집 스크립트 |
| `scripts/market_snapshot.py` | 고정 시장 지표 조회 (yfinance) |
| `scripts/build_index.py` | 다이제스트 축약 + 중복 탐지 + 당일 병합 |
| `scripts/digest_manifest.py` | 수집 매니페스트 (조회/제외/미조회 채널 구분) |
| `scripts/noise_filter.py` | 저신호·협찬성·채널내 근사중복 주석 |
| `scripts/extract_sections.py` | 토픽별 원문 슬라이스 추출 |
| `templates/dashboard.html` | 대시보드 고정 템플릿 (`__PAYLOAD__` 주입) |
| `scripts/render_dashboard.py` | payload 주입 + `<script>` 이스케이프 |
| `DASHBOARD_URL.txt` | 대시보드 Artifact 주소 (갱신 대상) |
| `daily/` | 텔레그램에서 매일 수집한 원문 (`YYYY-MM-DD.md`, git에는 안 올라감) |

## 텔레그램 자동 수집 설정 (최초 1회)

1. https://my.telegram.org 에서 `api_id`/`api_hash` 발급
2. 저장소 루트 `.env`에 추가:
   ```
   TELEGRAM_API_ID=...
   TELEGRAM_API_HASH=...
   ```
3. `pip install -r requirements.txt`
4. `python scripts/telegram_login_setup.py` 를 **직접 터미널에서**
   실행 (전화번호+인증코드 입력, 대화형이라 Claude Code 툴 호출로는 불가)
5. Windows 작업 스케줄러에 `telegram_digest.py`를 매일 정오 실행으로 등록

세션 파일(`scripts/.state/session.session`)과 수집 원문(`daily/`)은 민감정보 +
제3자 저작물이라 `.gitignore`에 등록돼 있고 로컬에만 남는다. 채널은
읽기 전용으로만 접근하며(전송·참여·설정변경 없음), 텔레그램이 요청을 제한하면
재시도 없이 즉시 멈추고 `scripts/.state/run_log.jsonl`에 기록한다.

## 참고자료 출처

노션 "마켓브리핑" 페이지의 참고자료 목록 기준. 핵심 카테고리(미장/한국/일본/
중국/원자재/해운/매크로/금융팀/채권/CPI/시황/리포트모음)의 텔레그램 채널은
구독 중인 브로드캐스트 채널 전체를 자동 수집한다(화이트리스트 없음). 카카오톡·유튜브는
자동화하지 않으며 사용자가 원문을 붙여넣는다. 아래는 에이전트가 직접
검색·열람 가능한 웹 소스:

- 메르의 블로그 (네이버 블로그)
- 한국경제, 매일경제, 이투데이, 뉴스핌
- KDI 경제교육·정보센터 국내연구자료
- KCIF 국제금융센터
- 한국금융연구원, 자본시장연구원(KCMI)
- thebell, 딜사이트
