# 브리핑 전 채널 수집 + 토큰 효율화 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 구독 중인 텔레그램 채널 전체를 대상으로 삼고, 실행당 토큰을 줄이면서 Sonnet 에서도 같은 품질의 대시보드가 나오게 한다.

**Architecture:** 화이트리스트를 제외 목록으로 뒤집어 전수 수집으로 전환한다. 노이즈를 파이썬이 주석 처리하고(삭제 아님) 오케스트레이터가 읽는 경량 뷰에서만 제외한다. 대시보드는 커밋된 템플릿 + JSON payload 주입으로 바꿔, 모델이 레이아웃을 재생성하지 않게 한다.

**Tech Stack:** Python 3.12, Telethon, yfinance, pytest, 바닐라 HTML/CSS/JS 템플릿

**Spec:** `docs/superpowers/specs/2026-09-06-brief-efficiency-design.md`

## Global Constraints

- 수치는 `market_snapshot.json` 과 다이제스트 원문에 있는 값만 인용한다. 생성 금지.
- 중간 산출물은 전부 `market-briefing/scripts/.state/` 아래에 둔다.
- 수집 원문(`market-briefing/daily/`)은 제3자 저작물이므로 커밋하지 않는다.
- 텔레그램 접근은 읽기 전용. 전송·참여·설정변경 금지. 브로드캐스트 채널만 읽고 그룹·1:1 대화는 읽지 않는다.
- 새 테스트는 네트워크 없이 실행돼야 한다. `telethon`/`yfinance` 는 지연 임포트하거나 테스트가 건드리지 않는 모듈에 격리한다.
- 노이즈 필터는 삭제가 아니라 주석이다. 전체 인덱스는 모든 메시지를 보관한다.
- `find_duplicate_groups` 의 임계값 0.6 과 5-gram 크기는 건드리지 않는다.

## 스펙에서 조정한 것

스펙의 노이즈 규칙 1(협찬성 기사)을 실측 검증한 결과 전역 기계 판정이 성립하지 않는다.
스펙의 "숫자 없음" 조건은 실제 협찬 기사를 놓치고(`케어센스, 5분마다 혈당 재고 앱
전송…114개국 진출`), 조건을 풀면 진짜 뉴스를 잘못 거른다(`우리銀, 아시안게임 승리
기원...연 7.5% 적금 출시`). URL 유무도 구분자가 아니다 — 양쪽 다 hankyung.com 링크를 단다.

따라서 협찬성 규칙을 **채널 지정 opt-in** 으로 축소한다. `channels.py` 의
`ADVERTORIAL_CHANNELS` 에 등록된 채널에서만 적용해 오탐 범위를 사용자가 지정한
채널로 가둔다. 저신호·근사중복 규칙은 스펙대로 전역 적용한다.

---

### Task 1: 노이즈 필터

**Files:**
- Create: `market-briefing/scripts/noise_filter.py`
- Create: `market-briefing/tests/test_noise_filter.py`

**Interfaces:**
- Consumes: 없음 (순수 함수 모듈, 임포트 의존 없음)
- Produces:
  - `is_low_signal(text: str) -> bool`
  - `is_advertorial(text: str) -> bool`
  - `annotate(messages: list, similarity_fn, advertorial_channels: set[str]) -> list[str | None]`
    — `messages` 는 `.channel` / `.text` 속성을 가진 객체 리스트(build_index.Message).
    반환값은 메시지와 같은 길이의 리스트로, 각 원소는 `"low_signal"` / `"advertorial"` /
    `"near_duplicate"` / `None`.
  - `count_by_reason(annotations: list[str | None]) -> dict[str, int]`

- [ ] **Step 1: 실패하는 테스트 작성**

`market-briefing/tests/test_noise_filter.py`:

```python
from dataclasses import dataclass

import noise_filter


@dataclass
class M:
    channel: str
    text: str


def _sim(a: str, b: str) -> float:
    """테스트용 유사도 — 정규화 후 완전히 같으면 1.0, 아니면 0.0."""
    norm = lambda s: "".join(ch for ch in s if ch.isalnum())
    return 1.0 if norm(a) == norm(b) else 0.0


def test_low_signal_short_text_without_url_or_digit():
    assert noise_filter.is_low_signal("??????") is True
    assert noise_filter.is_low_signal("위켄드 오일 상승중") is True


def test_low_signal_spares_short_text_carrying_a_number():
    assert noise_filter.is_low_signal("코스피 6687 마감") is False


def test_low_signal_spares_short_text_carrying_a_url():
    assert noise_filter.is_low_signal("속보 https://a.com/x") is False


def test_low_signal_spares_long_text():
    assert noise_filter.is_low_signal("가" * 200) is False


def test_advertorial_matches_brand_comma_one_liner():
    assert noise_filter.is_advertorial("무아스, 감성·편의성 다 갖춘 프리미엄 생활용품") is True
    assert noise_filter.is_advertorial("케어센스, 5분마다 혈당 재고 앱 전송…114개국 진출") is True


def test_advertorial_spares_multiline_body():
    assert noise_filter.is_advertorial("무아스, 감성·편의성 다 갖춘 제품\n두 번째 줄") is False


def test_advertorial_spares_long_one_liner():
    assert noise_filter.is_advertorial("무아스, " + "가" * 200) is False


def test_advertorial_spares_headline_without_brand_comma():
    assert noise_filter.is_advertorial("수출 작년 실적 벌써 넘었다…사상 첫 '1조 달러' 코앞") is False


def test_annotate_flags_advertorial_only_in_optin_channel():
    msgs = [M("한국경제", "무아스, 감성·편의성 다 갖춘 프리미엄 생활용품"),
            M("Polaristimes", "무아스, 감성·편의성 다 갖춘 프리미엄 생활용품")]
    got = noise_filter.annotate(msgs, _sim, {"한국경제"})
    assert got == ["advertorial", None]


def test_annotate_flags_near_duplicate_within_channel_keeping_first():
    msgs = [M("A", "수출 작년 실적 벌써 넘었다 사상 첫 1조 달러 코앞"),
            M("A", "수출 작년 실적 벌써 넘었다 사상 첫 1조 달러 코앞!")]
    got = noise_filter.annotate(msgs, _sim, set())
    assert got == [None, "near_duplicate"]


def test_annotate_does_not_flag_duplicate_across_channels():
    msgs = [M("A", "같은 내용을 담은 충분히 긴 문장입니다 여기 숫자 1 있음"),
            M("B", "같은 내용을 담은 충분히 긴 문장입니다 여기 숫자 1 있음")]
    assert noise_filter.annotate(msgs, _sim, set()) == [None, None]


def test_count_by_reason_ignores_none():
    assert noise_filter.count_by_reason(["advertorial", None, "advertorial", "low_signal"]) == {
        "advertorial": 2, "low_signal": 1}


def test_advertorial_known_false_positive_is_documented():
    """알려진 오탐 — 같은 형태의 진짜 뉴스도 걸린다.

    협찬성 규칙을 채널 지정 opt-in 으로 가둔 이유이자, 필터를 삭제가 아니라
    주석으로 만든 이유다. 이 동작이 조용히 바뀌면 이 테스트가 알려준다.
    """
    assert noise_filter.is_advertorial("우리銀, 아시안게임 승리 기원...연 7.5% 적금 출시") is True
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest market-briefing/tests/test_noise_filter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'noise_filter'`

- [ ] **Step 3: 구현 작성**

`market-briefing/scripts/noise_filter.py`:

```python
"""다이제스트 노이즈 주석.

LLM 이 보기 전에 파이썬이 확실한 노이즈를 표시한다. 실측상 한국경제 채널 98건 중
63건이 박람회 협찬성 제품 기사로 전체 코퍼스의 37% 였다.

**삭제가 아니라 주석이다.** 전체 인덱스는 모든 메시지를 보관하고, 오케스트레이터가
읽는 경량 뷰에서만 주석된 항목이 빠진다. 오탐이 나도 정보가 사라지지 않는다.

협찬성 규칙은 채널 지정 opt-in 이다. 전역 적용하면 같은 형태의 진짜 뉴스
(`우리銀, 아시안게임 승리 기원...연 7.5% 적금 출시`)를 걸러버린다.
"""
from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://")
_DIGIT_RE = re.compile(r"\d")
# 브랜드명(공백·쉼표 없는 1~8자) + 쉼표 + 공백으로 시작하는 한 줄 헤드라인
_ADVERTORIAL_RE = re.compile(r"^[^\s,]{1,8}, \S")

_LOW_SIGNAL_MAX_CHARS = 20
_ADVERTORIAL_MAX_CHARS = 120
_NEAR_DUPLICATE_THRESHOLD = 0.85


def is_low_signal(text: str) -> bool:
    """짧고 URL·숫자가 없는 단문. `??????`, `위켄드 오일 상승중` 같은 것."""
    stripped = text.strip()
    if len(stripped) >= _LOW_SIGNAL_MAX_CHARS:
        return False
    if _URL_RE.search(stripped) or _DIGIT_RE.search(stripped):
        return False
    return True


def is_advertorial(text: str) -> bool:
    """`브랜드명, 제품 설명` 형태의 한 줄 협찬성 기사."""
    stripped = text.strip()
    if "\n" in stripped:
        return False
    if len(stripped) >= _ADVERTORIAL_MAX_CHARS:
        return False
    return bool(_ADVERTORIAL_RE.match(stripped))


def annotate(messages: list, similarity_fn, advertorial_channels: set[str]) -> list[str | None]:
    """메시지마다 노이즈 사유를 붙인다. 사유가 없으면 None.

    근사중복은 같은 채널 안에서만 본다 — 채널 간 중복은 병합의 근거이지
    노이즈가 아니다. 먼저 등장한 쪽을 남기고 뒤에 온 쪽을 표시한다.
    """
    reasons: list[str | None] = [None] * len(messages)

    for i, message in enumerate(messages):
        if is_low_signal(message.text):
            reasons[i] = "low_signal"
        elif message.channel in advertorial_channels and is_advertorial(message.text):
            reasons[i] = "advertorial"

    for i, message in enumerate(messages):
        if reasons[i] is not None:
            continue
        for j in range(i):
            if reasons[j] is not None:
                continue
            if messages[j].channel != message.channel:
                continue
            if similarity_fn(messages[j].text, message.text) >= _NEAR_DUPLICATE_THRESHOLD:
                reasons[i] = "near_duplicate"
                break

    return reasons


def count_by_reason(annotations: list[str | None]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for reason in annotations:
        if reason is not None:
            counts[reason] = counts.get(reason, 0) + 1
    return counts
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest market-briefing/tests/test_noise_filter.py -v`
Expected: PASS (13 passed)

- [ ] **Step 5: 커밋**

```bash
git add market-briefing/scripts/noise_filter.py market-briefing/tests/test_noise_filter.py
git commit -m "feat: 다이제스트 노이즈 주석 (저신호·협찬성·채널내 근사중복)"
```

---

### Task 2: 전 채널 수집 전환

**Files:**
- Modify: `market-briefing/scripts/channels.py` (전체 교체)
- Modify: `market-briefing/scripts/telegram_digest.py`
- Modify: `market-briefing/scripts/digest_manifest.py`
- Delete: `market-briefing/scripts/channel_match.py`
- Delete: `market-briefing/tests/test_channel_match.py`
- Modify: `market-briefing/tests/test_digest_manifest.py`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `channels.EXCLUDE_TITLES: set[str]` — 수집에서 제외할 채널 제목
  - `channels.ADVERTORIAL_CHANNELS: set[str]` — 협찬성 필터를 적용할 채널
  - `digest_manifest.write_manifest(path, collected, excluded, not_checked)` — 인자 이름과 순서가 바뀐다
  - `digest_manifest.read_manifest(path) -> dict` — 키는 `collected` / `excluded` / `not_checked`

- [ ] **Step 1: channels.py 전체 교체**

```python
"""텔레그램 수집 대상 설정.

구독 중인 **브로드캐스트 채널 전체**를 수집한다. 화이트리스트를 쓰지 않는 이유는
실측 때문이다 — 미매칭 19건의 실제 원인은 구독 누락이 아니라 제목 전사 오류였다
(`하나증권 리서치 중국/신흥국 전략 김경환` 이 실제로는 `하나 중국/신흥국 전략 김경환`).
전수 수집은 이 드리프트를 구조적으로 없앤다.

그룹과 1:1 대화는 `is_channel and not is_group` 필터로 구조적으로 제외되므로
개인 대화가 수집될 여지가 없다.
"""
from __future__ import annotations

# 수집에서 뺄 채널 제목. 정확히 일치하는 것만 제외한다.
EXCLUDE_TITLES: set[str] = set()

# 협찬성 제품 기사가 섞이는 채널. 여기 등록된 채널에서만 협찬성 필터가 동작한다.
# 전역 적용하면 같은 형태의 진짜 뉴스를 걸러버린다 —
# `우리銀, 아시안게임 승리 기원...연 7.5% 적금 출시` 가 그 예다.
ADVERTORIAL_CHANNELS: set[str] = {"한국경제"}
```

- [ ] **Step 2: 매니페스트 테스트를 새 어휘로 교체**

`market-briefing/tests/test_digest_manifest.py` 전체를 아래로 교체한다:

```python
import digest_manifest


def test_manifest_path_uses_date(tmp_path):
    assert digest_manifest.manifest_path(tmp_path, "2026-09-06") == tmp_path / "2026-09-06.manifest.json"


def test_write_then_read_roundtrip(tmp_path):
    path = digest_manifest.manifest_path(tmp_path, "2026-09-06")
    digest_manifest.write_manifest(path, collected=["A", "B"], excluded=["C"], not_checked=["D"])
    data = digest_manifest.read_manifest(path)
    assert data["collected"] == ["A", "B"]
    assert data["excluded"] == ["C"]
    assert data["not_checked"] == ["D"]


def test_read_missing_file_returns_empty_lists(tmp_path):
    assert digest_manifest.read_manifest(tmp_path / "nope.json") == {
        "collected": [], "excluded": [], "not_checked": []}


def test_read_missing_file_returns_fresh_lists_each_call(tmp_path):
    first = digest_manifest.read_manifest(tmp_path / "nope.json")
    first["collected"].append("오염")
    assert digest_manifest.read_manifest(tmp_path / "nope.json")["collected"] == []


def test_read_old_manifest_without_new_keys(tmp_path):
    path = tmp_path / "old.manifest.json"
    path.write_text('{"matched": ["A"], "missing": ["B"]}', encoding="utf-8")
    data = digest_manifest.read_manifest(path)
    assert data == {"collected": [], "excluded": [], "not_checked": []}


def test_not_checked_defaults_to_empty(tmp_path):
    path = digest_manifest.manifest_path(tmp_path, "2026-09-06")
    digest_manifest.write_manifest(path, collected=["A"], excluded=[])
    assert digest_manifest.read_manifest(path)["not_checked"] == []
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `python -m pytest market-briefing/tests/test_digest_manifest.py -v`
Expected: FAIL — `write_manifest() got an unexpected keyword argument 'collected'` 계열 오류

- [ ] **Step 4: digest_manifest.py 전체 교체**

```python
"""다이제스트 실행 결과 매니페스트.

세 가지를 구분한다.
- `collected`  이번 실행에서 실제로 조회한 채널
- `excluded`   제외 목록에 걸려 건너뛴 채널
- `not_checked` 요청 제한(서킷브레이커)으로 조회하지 못한 채널

`not_checked` 를 따로 두는 이유는, 조회조차 못 한 채널을 "오늘 새 글 없음"으로
보고하면 대시보드가 읽지도 않은 채널을 없다고 단언하기 때문이다.

telethon 을 임포트하지 않아 CI 에서도 테스트된다. 매니페스트는 중간 산출물이라
`market-briefing/scripts/.state/` 아래에 쓴다.
"""
from __future__ import annotations

import json
from pathlib import Path

_KEYS = ("collected", "excluded", "not_checked")


def manifest_path(state_dir: Path, date: str) -> Path:
    return state_dir / f"{date}.manifest.json"


def write_manifest(
    path: Path,
    collected: list[str],
    excluded: list[str],
    not_checked: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "collected": collected,
        "excluded": excluded,
        "not_checked": list(not_checked or []),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_manifest(path: Path) -> dict:
    # 키가 없는 예전 매니페스트도 읽을 수 있어야 하므로 키마다 새 빈 리스트로 채운다.
    if not path.exists():
        return {key: [] for key in _KEYS}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {key: list(data.get(key, [])) for key in _KEYS}
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest market-briefing/tests/test_digest_manifest.py -v`
Expected: PASS (6 passed)

- [ ] **Step 6: telegram_digest.py 를 전수 수집으로 전환**

임포트 블록에서 `from channels import CHANNEL_TITLES` 와 `import channel_match` 를 지우고
`from channels import EXCLUDE_TITLES` 로 바꾼다.

모듈 docstring 두 번째 문단을 아래로 교체한다:

```
구독 중인 브로드캐스트 채널 전체에서 "마지막 실행 이후 ~ 지금까지" 새 메시지만
모아 market-briefing/daily/YYYY-MM-DD.md 로 저장한다. channels.EXCLUDE_TITLES 에
있는 채널만 건너뛴다.
```

`main()` 의 수집 루프를 아래로 교체한다 (`with TelegramClient(...) as client:` 블록 안):

```python
            # 브로드캐스트 채널만 — 그룹과 1:1 대화는 구조적으로 제외된다.
            dialogs = [d for d in client.iter_dialogs() if d.is_channel and not d.is_group]
            excluded_titles = [d.name for d in dialogs if d.name in EXCLUDE_TITLES]
            targets = [d for d in dialogs if d.name not in EXCLUDE_TITLES]

            for dialog in targets:
                title = dialog.name
                checked_titles.append(title)
                since_id = last_ids.get(title, 0)
                messages: list[str] = []
                max_id_seen = since_id
                try:
                    for msg in client.iter_messages(
                        dialog.entity, min_id=since_id, limit=_MAX_MESSAGES_PER_CHANNEL
                    ):
                        if msg.date < today_start_utc:
                            break
                        if msg.message:
                            messages.append(f"- ({msg.date:%H:%M}) {msg.message.strip()}")
                        max_id_seen = max(max_id_seen, msg.id)
                except FloodWaitError as e:
                    flood_wait_seconds = e.seconds
                    _log({"level": "error", "channel": title, "msg": f"flood_wait {e.seconds}s"})
                    checked_titles.pop()
                    break
                if messages:
                    collected[title] = list(reversed(messages))
                last_ids[title] = max_id_seen

            all_titles = [d.name for d in targets]
```

`main()` 앞부분에서 `missing_titles: list[str] = []` 를 지우고 아래를 넣는다:

```python
    checked_titles: list[str] = []
    excluded_titles: list[str] = []
    all_titles: list[str] = []
```

`with` 블록을 벗어난 뒤의 매니페스트 기록과 로그·출력을 아래로 교체한다:

```python
    not_checked_titles = [t for t in all_titles if t not in checked_titles]

    out_path = _write_digest(collected)
    digest_manifest.write_manifest(
        digest_manifest.manifest_path(_STATE_DIR, datetime.now().strftime("%Y-%m-%d")),
        collected=checked_titles,
        excluded=excluded_titles,
        not_checked=not_checked_titles,
    )
    _save_last_ids(last_ids)

    _log({
        "level": "error" if flood_wait_seconds else "info",
        "channels_collected": len(collected),
        "channels_checked": len(checked_titles),
        "channels_excluded": excluded_titles,
        "channels_not_checked": not_checked_titles,
        "flood_wait_seconds": flood_wait_seconds,
        "output": str(out_path),
    })

    print(f"완료: {out_path} (채널 {len(checked_titles)}개 조회, {len(collected)}개에서 새 글 수집)")
    if excluded_titles:
        print("제외:", ", ".join(excluded_titles))
    if not_checked_titles:
        print(f"조회 못 함 {len(not_checked_titles)}개:", ", ".join(not_checked_titles))
    if flood_wait_seconds:
        print(f"주의: 텔레그램 요청 제한으로 중간에 멈췄습니다 ({flood_wait_seconds}초 대기 필요).")
```

- [ ] **Step 7: 사라진 모듈 제거**

```bash
git rm market-briefing/scripts/channel_match.py market-briefing/tests/test_channel_match.py
```

- [ ] **Step 8: 전체 테스트 확인**

Run: `python -m pytest market-briefing/tests/ -v`
Expected: PASS. `test_channel_match.py` 가 사라졌고 `test_build_index.py` 의 매니페스트
관련 테스트는 아직 옛 키를 쓰므로 실패할 수 있다 — 실패하면 Task 3 에서 고치므로
여기서는 실패 목록만 보고서에 적고 넘어간다.

- [ ] **Step 9: 실제 수집 1회 검증**

Run: `PYTHONIOENCODING=utf-8 python market-briefing/scripts/telegram_digest.py`
Expected: "채널 28개 조회" 수준의 출력(구독 채널 수에 따라 다름). `제외:` 줄이 없고,
`조회 못 함` 도 없어야 정상. 매니페스트를 열어 `collected` 에 28개 안팎이 들어갔는지 확인한다.

- [ ] **Step 10: 커밋**

```bash
git add market-briefing/scripts/ market-briefing/tests/
git commit -m "feat: 구독 채널 전수 수집으로 전환, 화이트리스트 제거"
```

---

### Task 3: 인덱스에 노이즈 주석 반영 + 경량 클러스터 뷰

**Files:**
- Modify: `market-briefing/scripts/build_index.py`
- Modify: `market-briefing/tests/test_build_index.py`

**Interfaces:**
- Consumes: `noise_filter.annotate`, `noise_filter.count_by_reason` (Task 1); `channels.ADVERTORIAL_CHANNELS` (Task 2); `digest_manifest.read_manifest` 의 `collected`/`excluded`/`not_checked` 키 (Task 2)
- Produces:
  - `build_index(digest_text, manifest, date) -> dict` — 반환 dict 에 `noise_counts` 키 추가, `unsubscribed_channels` 제거, 각 메시지에 `noise` 키 추가
  - `cluster_view(index: dict) -> str` — 오케스트레이터가 읽는 경량 텍스트 뷰
  - `.state/cluster_view.txt` 를 `main()` 이 함께 쓴다

- [ ] **Step 1: 실패하는 테스트 추가**

`market-briefing/tests/test_build_index.py` 끝에 추가한다. 파일 앞부분의 기존
매니페스트 관련 테스트에서 `{"matched": [...], "missing": [...]}` 를 쓰는 곳은
`{"collected": [...], "excluded": [...]}` 로 바꾼다.

```python
def test_build_index_marks_noise_and_counts_it():
    digest = (
        "# 텔레그램 다이제스트 — 2026-09-06 정오 기준\n\n"
        "## 한국경제\n"
        "- (07:38) 무아스, 감성·편의성 다 갖춘 프리미엄 생활용품\n"
        "- (07:39) 수출 작년 실적 벌써 넘었다…사상 첫 '1조 달러' 코앞\n\n"
        "## Polaristimes\n"
        "- (08:00) ??????\n"
    )
    manifest = {"collected": ["한국경제", "Polaristimes"], "excluded": [], "not_checked": []}
    index = build_index.build_index(digest, manifest, "2026-09-06")
    kr = index["channels"]["한국경제"]["messages"]
    assert kr[0]["noise"] == "advertorial"
    assert kr[1]["noise"] is None
    assert index["channels"]["Polaristimes"]["messages"][0]["noise"] == "low_signal"
    assert index["noise_counts"] == {"advertorial": 1, "low_signal": 1}


def test_build_index_keeps_noisy_messages_in_full_index():
    digest = ("# 텔레그램 다이제스트 — 2026-09-06 정오 기준\n\n"
              "## Polaristimes\n- (08:00) ??????\n")
    manifest = {"collected": ["Polaristimes"], "excluded": [], "not_checked": []}
    index = build_index.build_index(digest, manifest, "2026-09-06")
    assert index["channels"]["Polaristimes"]["message_count"] == 1


def test_build_index_uses_collected_for_quiet_channels():
    digest = ("# 텔레그램 다이제스트 — 2026-09-06 정오 기준\n\n"
              "## A\n- (08:00) 충분히 긴 본문입니다 숫자 1 포함\n")
    manifest = {"collected": ["A", "B"], "excluded": ["C"], "not_checked": ["D"]}
    index = build_index.build_index(digest, manifest, "2026-09-06")
    assert index["quiet_channels"] == ["B"]
    assert index["not_checked_channels"] == ["D"]
    assert index["excluded_channels"] == ["C"]
    assert "unsubscribed_channels" not in index


def test_headline_limit_is_70():
    long_line = "0123456789" * 20
    digest = (f"# 텔레그램 다이제스트 — 2026-09-06 정오 기준\n\n"
              f"## A\n- (08:00) {long_line}\n")
    index = build_index.build_index(digest, {"collected": [], "excluded": [], "not_checked": []}, "2026-09-06")
    headline = index["channels"]["A"]["messages"][0]["headline"]
    assert len(headline) == 70
    assert long_line.startswith(headline)


def test_cluster_view_omits_noise_and_reports_counts():
    digest = (
        "# 텔레그램 다이제스트 — 2026-09-06 정오 기준\n\n"
        "## 한국경제\n"
        "- (07:38) 무아스, 감성·편의성 다 갖춘 프리미엄 생활용품\n"
        "- (07:39) 수출 작년 실적 벌써 넘었다…사상 첫 '1조 달러' 코앞\n"
    )
    manifest = {"collected": ["한국경제"], "excluded": [], "not_checked": []}
    view = build_index.cluster_view(build_index.build_index(digest, manifest, "2026-09-06"))
    assert "무아스" not in view
    assert "수출 작년 실적" in view
    assert "advertorial=1" in view


def test_cluster_view_is_smaller_than_full_index():
    import json as _json
    digest = ("# 텔레그램 다이제스트 — 2026-09-06 정오 기준\n\n"
              "## A\n" + "".join(f"- (08:{i:02d}) 본문 {i} 번째 메시지입니다 충분히 깁니다\n" for i in range(30)))
    manifest = {"collected": ["A"], "excluded": [], "not_checked": []}
    index = build_index.build_index(digest, manifest, "2026-09-06")
    assert len(build_index.cluster_view(index)) < len(_json.dumps(index, ensure_ascii=False))
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest market-briefing/tests/test_build_index.py -v`
Expected: FAIL — `KeyError: 'noise'` 및 `AttributeError: module 'build_index' has no attribute 'cluster_view'`

- [ ] **Step 3: build_index.py 수정**

임포트 블록에 두 줄 추가한다 (`import digest_manifest` 아래):

```python
import noise_filter
from channels import ADVERTORIAL_CHANNELS
```

`_headline` 의 기본 limit 을 70 으로 바꾼다:

```python
def _headline(text: str, limit: int = 70) -> str:
```

`build_index` 함수를 아래로 교체한다:

```python
def build_index(digest_text: str, manifest: dict, date: str) -> dict:
    messages = parse_digest(digest_text)
    groups = find_duplicate_groups(messages)
    noise = noise_filter.annotate(messages, similarity, ADVERTORIAL_CHANNELS)
    not_checked = list(manifest.get("not_checked", []))

    group_of: dict[int, str] = {}
    for number, group in enumerate(groups):
        for index in group["members"]:
            group_of[index] = f"g{number}"

    channels: dict[str, dict] = {}
    for index, message in enumerate(messages):
        entry = channels.setdefault(message.channel, {"message_count": 0, "messages": []})
        entry["message_count"] += 1
        entry["messages"].append({
            "index": index,
            "time": message.time,
            "headline": _headline(message.text),
            "chars": len(message.text),
            "urls": extract_urls(message.text),
            "tickers": extract_tickers(message.text),
            "duplicate_group": group_of.get(index),
            "noise": noise[index],
        })

    return {
        "date": date,
        "channels": channels,
        "noise_counts": noise_filter.count_by_reason(noise),
        "duplicate_groups": [
            {"group_id": f"g{number}", "reason": group["reason"],
             "members": [{"channel": messages[i].channel, "index": i} for i in group["members"]]}
            for number, group in enumerate(groups)
        ],
        # 서킷브레이커로 아예 조회하지 못한 채널은 "오늘 새 글 없음"이 아니다.
        "quiet_channels": [c for c in manifest.get("collected", [])
                           if c not in channels and c not in not_checked],
        "excluded_channels": list(manifest.get("excluded", [])),
        "not_checked_channels": not_checked,
    }


def cluster_view(index: dict) -> str:
    """오케스트레이터가 토픽을 묶을 때 읽는 경량 뷰.

    전체 인덱스 JSON 은 메시지당 메타데이터(urls/tickers/chars) 때문에 원문보다
    커진다. 클러스터링에 필요한 건 채널·번호·시각·헤드라인뿐이라 그것만 남긴다.
    노이즈로 표시된 메시지는 빠지되, 몇 건이 왜 빠졌는지는 머리말에 적는다.
    """
    lines = [f"date={index['date']} channels={len(index['channels'])} "
             f"dupgroups={len(index['duplicate_groups'])}"]
    counts = index.get("noise_counts") or {}
    if counts:
        lines.append("noise " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    for channel, data in index["channels"].items():
        shown = [m for m in data["messages"] if m.get("noise") is None]
        lines.append(f"\n## {channel} ({len(shown)}/{data['message_count']})")
        for message in shown:
            lines.append(f"  [{message['index']}] {message['time']} {message['headline']}")

    for key, label in (("quiet_channels", "quiet"),
                       ("not_checked_channels", "not_checked"),
                       ("excluded_channels", "excluded")):
        values = index.get(key) or []
        if values:
            lines.append(f"\n{label}: {', '.join(values)}")
    return "\n".join(lines)
```

`main()` 에서 인덱스를 쓴 뒤 클러스터 뷰도 함께 쓰도록, `_OUT_PATH.write_text(...)`
다음 줄에 추가한다:

```python
    view_path = _STATE_DIR / "cluster_view.txt"
    view_path.write_text(cluster_view(index), encoding="utf-8")
```

그리고 `main()` 의 출력 부분 마지막에 한 줄 추가한다:

```python
    print(f"  클러스터 뷰 {view_path.stat().st_size / 1024:.0f}KB -> {view_path}")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest market-briefing/tests/ -v`
Expected: PASS (전체)

- [ ] **Step 5: 실제 데이터로 절감 측정**

Run: `PYTHONIOENCODING=utf-8 python market-briefing/scripts/build_index.py`
Expected: 원문 KB, 인덱스 KB, 클러스터 뷰 KB 가 출력된다. 클러스터 뷰가 이전
투영본(10,153자)보다 작아야 한다. 측정값을 보고서에 기록한다.

- [ ] **Step 6: 커밋**

```bash
git add market-briefing/scripts/build_index.py market-briefing/tests/test_build_index.py
git commit -m "feat: 인덱스에 노이즈 주석 반영 + 경량 클러스터 뷰 생성"
```

---

### Task 4: 토픽별 섹션 입력 사전 추출

**Files:**
- Create: `market-briefing/scripts/extract_sections.py`
- Create: `market-briefing/tests/test_extract_sections.py`

**Interfaces:**
- Consumes: `build_index.parse_digest`, `build_index._escape_body_block` (기존).
  `_escape_body_block` 은 밑줄 접두 이름이지만 의도적으로 재사용한다 — 본문의
  `## ` 줄을 이스케이프하는 규칙이 다이제스트 포맷의 일부라 슬라이스도 같은 규칙을
  따라야 하고, 복제하면 두 곳이 갈라진다.
- Produces:
  - `slice_for_topic(messages: list, refs: list[dict]) -> str` — 토픽 하나의 원문 마크다운
  - `main() -> None` — `.state/topics.json` 을 읽어 `.state/sections/input/<topic_id>.md` 를 쓴다

`refs` 원소는 `{"channel": str, "index": int}` 이며 `index` 는 문서 전체 0-based 순번이다.

- [ ] **Step 1: 실패하는 테스트 작성**

`market-briefing/tests/test_extract_sections.py`:

```python
import build_index
import extract_sections

_DIGEST = (
    "# 텔레그램 다이제스트 — 2026-09-06 정오 기준\n\n"
    "## A\n"
    "- (08:00) 첫 번째 메시지\n"
    "- (09:00) 두 번째 메시지\n\n"
    "## B\n"
    "- (10:00) 세 번째 메시지\n"
)


def test_slice_contains_only_requested_messages():
    messages = build_index.parse_digest(_DIGEST)
    out = extract_sections.slice_for_topic(messages, [{"channel": "A", "index": 0},
                                                      {"channel": "B", "index": 2}])
    assert "첫 번째 메시지" in out
    assert "세 번째 메시지" in out
    assert "두 번째 메시지" not in out


def test_slice_groups_by_channel_with_headers():
    messages = build_index.parse_digest(_DIGEST)
    out = extract_sections.slice_for_topic(messages, [{"channel": "A", "index": 0},
                                                      {"channel": "B", "index": 2}])
    assert out.count("## ") == 2
    assert out.index("## A") < out.index("## B")


def test_slice_keeps_timestamp_prefix():
    messages = build_index.parse_digest(_DIGEST)
    out = extract_sections.slice_for_topic(messages, [{"channel": "A", "index": 1}])
    assert "- (09:00) 두 번째 메시지" in out


def test_slice_skips_out_of_range_index():
    messages = build_index.parse_digest(_DIGEST)
    out = extract_sections.slice_for_topic(messages, [{"channel": "A", "index": 99}])
    assert out.strip() == ""


def test_slice_skips_ref_whose_channel_does_not_match():
    messages = build_index.parse_digest(_DIGEST)
    out = extract_sections.slice_for_topic(messages, [{"channel": "B", "index": 0}])
    assert out.strip() == ""
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest market-briefing/tests/test_extract_sections.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'extract_sections'`

- [ ] **Step 3: 구현 작성**

`market-briefing/scripts/extract_sections.py`:

```python
"""토픽별 원문 슬라이스 추출.

섹션 작성 에이전트에는 Bash 가 없어, 90KB 다이제스트에서 자기 몫 메시지를 찾으려면
Grep 을 여러 번 돌려야 한다. 파이썬이 미리 잘라주면 그 턴이 통째로 사라진다.

`.state/topics.json` -> `.state/sections/input/<topic_id>.md`
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import build_index

_SCRIPT_DIR = Path(__file__).resolve().parent
_STATE_DIR = _SCRIPT_DIR / ".state"
_DAILY_DIR = _SCRIPT_DIR.parent / "daily"
_TOPICS_PATH = _STATE_DIR / "topics.json"
_INPUT_DIR = _STATE_DIR / "sections" / "input"


def slice_for_topic(messages: list, refs: list[dict]) -> str:
    """refs 가 가리키는 메시지만 채널별로 묶어 다이제스트 형식으로 돌려준다.

    index 는 문서 전체 0-based 순번이다. 채널이 일치하지 않거나 범위를 벗어난
    ref 는 조용히 건너뛴다 — 토픽 정의가 낡았을 때 전체가 실패하지 않게 한다.
    """
    by_channel: dict[str, list] = {}
    for ref in refs:
        index = ref.get("index")
        if not isinstance(index, int) or not 0 <= index < len(messages):
            continue
        message = messages[index]
        if message.channel != ref.get("channel"):
            continue
        by_channel.setdefault(message.channel, []).append(message)

    lines: list[str] = []
    for channel, items in by_channel.items():
        lines.append(f"## {channel}")
        for message in items:
            lines.append(build_index._escape_body_block(
                f"- ({message.time}) {message.text}"))
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    date = datetime.now().strftime("%Y-%m-%d")
    digest_path = _DAILY_DIR / f"{date}.md"
    if not digest_path.exists():
        raise SystemExit(f"오늘자 다이제스트가 없습니다: {digest_path}")
    if not _TOPICS_PATH.exists():
        raise SystemExit(f"토픽 파일이 없습니다: {_TOPICS_PATH}\n오케스트레이터가 먼저 토픽을 묶어야 합니다.")

    messages = build_index.parse_digest(digest_path.read_text(encoding="utf-8"))
    topics = json.loads(_TOPICS_PATH.read_text(encoding="utf-8"))["topics"]

    _INPUT_DIR.mkdir(parents=True, exist_ok=True)
    for topic in topics:
        body = slice_for_topic(messages, topic.get("message_refs", []))
        out = _INPUT_DIR / f"{topic['topic_id']}.md"
        out.write_text(f"# {topic['title']}\n\n{body}", encoding="utf-8")
        print(f"  {out.name}: {len(body)}자")
    print(f"완료: {len(topics)}개 토픽 슬라이스 -> {_INPUT_DIR}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest market-briefing/tests/test_extract_sections.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: 커밋**

```bash
git add market-briefing/scripts/extract_sections.py market-briefing/tests/test_extract_sections.py
git commit -m "feat: 토픽별 원문 슬라이스 사전 추출"
```

---

### Task 5: 시세 TTL 캐시

**Files:**
- Modify: `market-briefing/scripts/market_snapshot.py`
- Modify: `market-briefing/tests/test_market_snapshot.py`

**Interfaces:**
- Consumes: 없음
- Produces: `is_fresh(snapshot: dict, now: datetime, ttl_minutes: int = 30) -> bool`

- [ ] **Step 1: 실패하는 테스트 추가**

`market-briefing/tests/test_market_snapshot.py` 끝에 추가:

```python
from datetime import datetime, timedelta, timezone


def test_is_fresh_within_ttl():
    now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    snap = {"fetched_at": (now - timedelta(minutes=10)).isoformat()}
    assert market_snapshot.is_fresh(snap, now) is True


def test_is_stale_past_ttl():
    now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    snap = {"fetched_at": (now - timedelta(minutes=45)).isoformat()}
    assert market_snapshot.is_fresh(snap, now) is False


def test_is_not_fresh_without_timestamp():
    now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    assert market_snapshot.is_fresh({}, now) is False


def test_is_not_fresh_with_unparseable_timestamp():
    now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    assert market_snapshot.is_fresh({"fetched_at": "어제"}, now) is False
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest market-briefing/tests/test_market_snapshot.py -v`
Expected: FAIL — `AttributeError: module 'market_snapshot' has no attribute 'is_fresh'`

- [ ] **Step 3: 구현 작성**

`market-briefing/scripts/market_snapshot.py` 의 `build_snapshot` 뒤에 추가:

```python
_TTL_MINUTES = 30


def is_fresh(snapshot: dict, now: datetime, ttl_minutes: int = _TTL_MINUTES) -> bool:
    """같은 날 /brief 를 여러 번 불러도 시세를 매번 다시 받지 않게 한다."""
    stamp = snapshot.get("fetched_at")
    if not stamp:
        return False
    try:
        fetched = datetime.fromisoformat(stamp)
    except ValueError:
        return False
    return (now - fetched) < timedelta(minutes=ttl_minutes)
```

임포트 줄을 `from datetime import datetime, timedelta, timezone` 로 바꾼다.

`main()` 을 아래로 교체한다:

```python
def main() -> None:
    now = datetime.now(timezone.utc)
    if _OUT_PATH.exists():
        try:
            cached = json.loads(_OUT_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cached = {}
        if is_fresh(cached, now):
            ok = [i for i in cached["indicators"] if "error" not in i]
            print(f"캐시 사용: {_OUT_PATH} ({len(ok)}개 지표, {_TTL_MINUTES}분 이내)")
            return

    snapshot = build_snapshot()
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = [i for i in snapshot["indicators"] if "error" not in i]
    print(f"완료: {_OUT_PATH} ({len(ok)}/{len(snapshot['indicators'])}개 지표 수집)")
    for item in snapshot["indicators"]:
        if "error" in item:
            print(f"  실패: {item['name']} ({item['ticker']}) — {item['error']}")
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest market-briefing/tests/test_market_snapshot.py -v`
Expected: PASS (8 passed)

- [ ] **Step 5: 캐시 동작 확인**

```bash
PYTHONIOENCODING=utf-8 python market-briefing/scripts/market_snapshot.py
PYTHONIOENCODING=utf-8 python market-briefing/scripts/market_snapshot.py
```
Expected: 첫 실행은 "완료:", 두 번째는 "캐시 사용:" 이 출력된다.

- [ ] **Step 6: 커밋**

```bash
git add market-briefing/scripts/market_snapshot.py market-briefing/tests/test_market_snapshot.py
git commit -m "feat: 시세 스냅샷 30분 TTL 캐시"
```

---

### Task 6: 대시보드 템플릿화

**Files:**
- Create: `market-briefing/templates/dashboard.html`
- Delete: `market-briefing/templates/dashboard_reference.html`

**Interfaces:**
- Consumes: 스펙의 payload 계약
- Produces: `__PAYLOAD__` 자리표시자를 가진 고정 템플릿. 렌더 단계는 이 파일을 읽어 자리표시자를 payload JSON 으로 치환한 뒤 발행한다.

이 태스크는 파이썬 테스트가 아니라 **브라우저에서 눈으로 확인**해 검증한다.

- [ ] **Step 1: 기존 대시보드를 템플릿으로 변환**

`market-briefing/templates/dashboard_reference.html` 을 `dashboard.html` 로 복사한 뒤
아래를 바꾼다.

1. `<body>` 안의 하드코딩된 7개 `<article class="topic">` 블록을 전부 지우고
   `<section id="topics"></section>` 하나로 대체한다.
2. 하드코딩된 masthead 숫자·날짜·roster 내용을 지우고 각각
   `<div class="sub" id="sub"></div>`, `<div class="stamp" id="stamp"></div>`,
   `<section class="roster" id="roster"></section>` 로 비운다.
3. 파일 끝 `<script>` 의 `const DATA = {...}` 줄을 아래로 바꾼다.

```javascript
const PAYLOAD = __PAYLOAD__;
```

4. 기존 지표 렌더 코드는 `PAYLOAD.indicators` 를 쓰도록 바꾸고, 아래 렌더 함수들을 더한다.

```javascript
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

function renderMasthead(p){
  document.getElementById("sub").textContent =
    `KECOBUGS 26th · 텔레그램 ${p.channel_count}개 채널 ${p.message_count}건에서 ${p.topics.length}개 이슈로 정리`;
  document.getElementById("stamp").innerHTML =
    `<b>${esc(p.date)}</b>${esc(p.collected_at)}`;
}

function renderTopics(p){
  document.getElementById("topics").innerHTML = p.topics.map(t => {
    const merged = (t.merged || []).length
      ? `<p class="merged">↳ ${t.merged.map(m =>
          `<b>${esc(m.channel)}</b>의 ${esc(m.what)}`).join(", ")}이 이 섹션에 병합됨</p>`
      : "";
    const chips = (t.indicators || []).map(i =>
      `<span class="chip">${esc(i.label)} <b>${esc(i.value)}</b>${
        i.change ? ` <span class="${String(i.change).startsWith("-") || String(i.change).startsWith("−") ? "down" : "up"}">${esc(i.change)}</span>` : ""
      }</span>`).join("");
    return `<article class="topic">
      <div class="thead"><span class="chan">${esc(t.primary_channel)}</span>
        <h2>${esc(t.title)}</h2></div>
      ${merged}
      <div class="body">
        <div><div class="lbl">정리</div><p>${esc(t.organized)}</p></div>
        <div><div class="lbl">요약</div><p class="summary">${esc(t.summary)}</p></div>
        ${chips ? `<div><div class="lbl">지표</div><div class="metrics">${chips}</div></div>` : ""}
        <div class="draft"><div class="lbl">인사이트 초안 — 검토 필요</div>
          <p>${esc(t.draft_insight)}</p></div>
      </div></article>`;
  }).join("");
}

function renderRoster(p){
  const rows = [];
  const add = (k, v) => { if (v && v.length) rows.push(
    `<div class="rrow"><span class="k">${k}</span><span class="v">${esc(v.join(" · "))}</span></div>`); };
  add("오늘 새 글 없음", p.quiet_channels);
  add("조회 못 함", p.not_checked_channels);
  add("제외 채널", p.excluded_channels);
  if (p.excluded_note) rows.push(
    `<div class="rrow"><span class="k">필터</span><span class="v">${esc(p.excluded_note)}</span></div>`);
  document.getElementById("roster").innerHTML = rows.join("");
}

renderMasthead(PAYLOAD);
renderTopics(PAYLOAD);
renderRoster(PAYLOAD);
```

- [ ] **Step 2: 샘플 payload 로 렌더 확인**

`__PAYLOAD__` 를 아래 샘플로 치환한 임시 파일을 만들어 브라우저로 연다.

```bash
PYTHONIOENCODING=utf-8 python -c "
import json
from pathlib import Path
sample = {
 'date':'2026-09-06','collected_at':'정오 기준 · 수집 18:13 KST',
 'channel_count':9,'message_count':171,
 'indicators':[{'name':'KOSPI','label':'코스피','last':6687.21,'change_pct':1.64,'as_of':'2026-09-04','spark':None,'note':'추이 데이터 신뢰 불가'},
               {'name':'VIX','label':'VIX','last':14.53,'change_pct':1.47,'as_of':'2026-09-04','spark':[15.5,16.1,14.5]}],
 'topics':[{'topic_id':'t1','title':'테스트 토픽','primary_channel':'미국 주식 인사이더',
            'merged':[{'channel':'급등일보 미국주식','what':'유조선 타격 건'}],
            'organized':'정리 본문','summary':'요약 한 줄',
            'indicators':[{'label':'VIX','value':'14.53','change':'+1.47%'}],
            'draft_insight':'초안 인사이트'}],
 'quiet_channels':['신한 리서치'],'not_checked_channels':[],'excluded_channels':[],
 'excluded_note':'협찬성 기사 63건 제외'}
tpl = Path('market-briefing/templates/dashboard.html').read_text(encoding='utf-8')
Path('market-briefing/scripts/.state/preview.html').write_text(
    tpl.replace('__PAYLOAD__', json.dumps(sample, ensure_ascii=False)), encoding='utf-8')
print('wrote market-briefing/scripts/.state/preview.html')
"
```

Expected: 브라우저에서 열었을 때 지표 카드 2개(코스피는 차트 없이 안내 문구, VIX 는
스파크라인), 토픽 1개(병합 표기 포함), 하단 roster 가 정상 렌더된다. 콘솔 오류 없음.

- [ ] **Step 3: 참고본 제거**

```bash
git rm market-briefing/templates/dashboard_reference.html
```

- [ ] **Step 4: 커밋**

```bash
git add market-briefing/templates/dashboard.html
git commit -m "feat: 대시보드를 고정 템플릿 + payload 주입 구조로 전환"
```

---

### Task 7: 스킬 갱신 + 문구 정리

**Files:**
- Modify: `.agents/skills/brief/SKILL.md`
- Modify: `market-briefing/README.md`

**Interfaces:**
- Consumes: Task 1~6 전부

- [ ] **Step 1: brief 스킬의 파이프라인 절차 교체**

`### 1. 수집 (파이썬)` 부터 `### 4. 섹션 작성` 직전까지를 아래로 교체한다.

````markdown
### 1. 수집 (파이썬)

```bash
PYTHONIOENCODING=utf-8 python market-briefing/scripts/telegram_digest.py
PYTHONIOENCODING=utf-8 python market-briefing/scripts/market_snapshot.py
PYTHONIOENCODING=utf-8 python market-briefing/scripts/build_index.py
```

구독 중인 브로드캐스트 채널 전체를 수집한다. 제외하려면
`market-briefing/scripts/channels.py` 의 `EXCLUDE_TITLES` 에 정확한 제목을 넣는다.

시세는 30분 TTL 캐시가 있어 같은 날 재호출하면 다시 받지 않는다.
세션 오류가 나면 사용자에게 별도 터미널에서 `telegram_login_setup.py` 실행을 요청한다.

### 2. 토픽 클러스터링 (직접 수행)

`market-briefing/scripts/.state/cluster_view.txt` **만** 읽는다. 인덱스 JSON 이나
원문을 읽지 마라 — 뷰가 이미 노이즈를 빼고 헤드라인을 70자로 자른 결과다.

토픽 4~7개로 묶어 `market-briefing/scripts/.state/topics.json` 에 쓴다.

```json
{"date": "2026-09-06", "topics": [{
  "topic_id": "t1", "title": "이란-미국 군사 충돌 격화",
  "primary_channel": "미국 주식 인사이더",
  "merged_channels": ["급등일보 미국주식"],
  "message_refs": [{"channel": "미국 주식 인사이더", "index": 0},
                   {"channel": "급등일보 미국주식", "index": 12}]}]}
```

primary 채널은 그 이슈를 가장 자세히 다룬 채널로 정한다. 결정적 중복 탐지는 0건인
날이 많다 — 채널마다 출처와 문장이 달라서다. 실제 병합은 이 단계의 판단이 한다.

### 3. 섹션 입력 추출 (파이썬)

```bash
PYTHONIOENCODING=utf-8 python market-briefing/scripts/extract_sections.py
```

`.state/sections/input/<topic_id>.md` 가 생긴다. 섹션 작성 에이전트는 이 파일만
읽으면 되고 원문을 Grep 하지 않는다.
````

- [ ] **Step 2: 섹션 작성 단계에서 입력 경로 교체**

`### 4. 섹션 작성` 절의 경로 목록에서 `digest_path` 줄을 아래로 바꾼다.

```
- input_path: `market-briefing/scripts/.state/sections/input/<topic_id>.md` (자기 토픽 원문, 이미 잘려 있음)
```

그리고 그 아래 "`message_refs` 는 인덱스 번호와 함께 …" 문단을 아래로 교체한다.

```
원문은 이미 토픽별로 잘려 있으므로 인덱스 번호나 타임스탬프를 넘길 필요가 없다.
에이전트는 `input_path` 파일 하나만 읽는다.
```

- [ ] **Step 3: 렌더 단계를 템플릿 주입으로 교체**

`### 6. 렌더` 절 전체를 아래로 교체한다.

````markdown
### 6. 렌더

HTML 을 쓰지 마라. **payload JSON 만 만들고 템플릿에 주입한다.** 레이아웃은
`market-briefing/templates/dashboard.html` 에 고정돼 있어 실행마다 변하지 않는다.

payload 스키마는 `docs/superpowers/specs/2026-09-06-brief-efficiency-design.md`
의 "데이터 계약" 절에 있다. 섹션 JSON 들과 시세 스냅샷을 그 형태로 합쳐
`.state/payload.json` 에 쓴 뒤 아래로 발행본을 만든다.

```bash
PYTHONIOENCODING=utf-8 python -c "
import json
from pathlib import Path
tpl = Path('market-briefing/templates/dashboard.html').read_text(encoding='utf-8')
payload = Path('market-briefing/scripts/.state/payload.json').read_text(encoding='utf-8')
Path('market-briefing/scripts/.state/dashboard.html').write_text(
    tpl.replace('__PAYLOAD__', payload), encoding='utf-8')
print('ok')"
```

코스피·코스닥은 `spark` 를 `null` 로 두고 `note` 에 신뢰 불가 사유를 넣는다.
야후 `^KS11`/`^KQ11` 시계열에 비정상 변동이 섞여 있다. 매 실행 데이터 품질 확인:

```bash
PYTHONIOENCODING=utf-8 python -c "
import json; from pathlib import Path
s = json.loads(Path('market-briefing/scripts/.state/market_snapshot.json').read_text(encoding='utf-8'))
for i in s['indicators']:
    h = i.get('history') or []
    if len(h) < 2: continue
    j = [abs(h[k+1]['close']/h[k]['close']-1)*100 for k in range(len(h)-1) if h[k]['close']]
    print(f\"{i['name']:10} maxjump={max(j):5.1f}%  >5%일수={sum(1 for x in j if x>5):2d}/{len(j)}\")"
```

VIX 는 원래 일간 변동이 커서 `>5%일수`가 많다 — 정상이다.

발행: 최초 1회는 `favicon` 을 붙여 새로 발행하고 URL 을
`market-briefing/DASHBOARD_URL.txt` 에 기록한다. 이후에는 그 URL 을 `url` 인자로
넘겨 같은 주소를 갱신하고 `favicon` 은 넘기지 않는다.

채팅에는 링크와 3~5줄 요약만 남긴다. 섹션 전문을 복사하지 마라.
````

- [ ] **Step 4: 스킬에 문구 규칙 추가**

`## 하지 않는 것` 절 끝에 아래 항목을 더한다.

```markdown
- 군더더기 문구를 쓰지 않는다. 단계 설명·대시보드 카피·채팅 보고 모두 사실과
  판단만 남기고, 상투어("~해보겠습니다", "훌륭합니다")와 이미 아는 내용의 재진술을
  뺀다. 근거 없는 형용사보다 측정값을 쓴다.
```

- [ ] **Step 5: README 갱신**

`### 알려진 한계` 절의 첫 항목(코스피·코스닥) 뒤에 아래를 넣는다.

```markdown
- **협찬성 필터는 채널 지정 opt-in**: 전역 기계 판정이 성립하지 않아
  (`우리銀, 아시안게임 승리 기원...연 7.5% 적금 출시` 같은 진짜 뉴스가 같은 형태다)
  `channels.py` 의 `ADVERTORIAL_CHANNELS` 에 등록된 채널에서만 적용한다.
  필터는 삭제가 아니라 주석이라 전체 인덱스에는 모든 메시지가 남는다.
  알려진 오탐은 `test_advertorial_known_false_positive_is_documented` 에 박아뒀다 —
  오탐이 늘면 해당 채널을 `ADVERTORIAL_CHANNELS` 에서 빼면 된다.
```

`## 폴더 구성` 표에서 `scripts/channel_match.py` 행과
`templates/dashboard_reference.html` 행을 지우고 아래를 넣는다.

```markdown
| `scripts/noise_filter.py` | 저신호·협찬성·채널내 근사중복 주석 |
| `scripts/extract_sections.py` | 토픽별 원문 슬라이스 추출 |
| `templates/dashboard.html` | 대시보드 고정 템플릿 (`__PAYLOAD__` 주입) |
```

`scripts/channels.py` 행의 설명을 `수집 제외 목록 + 협찬성 필터 대상 채널` 로 바꾼다.

- [ ] **Step 6: 전체 테스트 + 커밋**

Run: `python -m pytest tests/ equity-report/tests/ market-briefing/tests/ -v`
Expected: PASS (전체)

```bash
git add .agents/skills/brief/SKILL.md market-briefing/README.md
git commit -m "docs: 전 채널 수집·템플릿 렌더 반영, 문구 규칙 추가"
```

---

## 실행 후 검토 대상 (이번 범위 밖)

- 코스피·코스닥 데이터 소스를 네이버 금융으로 교체
- 증분 클러스터링 — 새 메시지만 기존 토픽에 편입
- React 도입 (채널 필터·섹션 토글 등 상태가 생기면)
