# 마켓브리핑 온디맨드 대시보드 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 호출할 때마다 텔레그램 채널 소식을 중복 제거해 정리하고 시장 지표 차트와 함께 대시보드 Artifact로 갱신하는 파이프라인을 만든다.

**Architecture:** 파이썬이 결정적 작업(텔레그램 수집·시세 조회·중복 탐지·인덱스 축약)을 맡고, 오케스트레이터(메인 세션)가 축약 인덱스만 읽어 토픽을 클러스터링한 뒤, 토픽별 서브에이전트를 병렬로 띄워 섹션을 작성시키고, 결과를 모아 대시보드 Artifact를 갱신한다. 중복 제거는 전역 시야가 필요하므로 채널 단위 팬아웃을 쓰지 않는다.

**Tech Stack:** Python 3.12, yfinance, Telethon(기존), pytest, Claude Code 서브에이전트/스킬, HTML Artifact

**Spec:** `docs/superpowers/specs/2026-09-06-market-briefing-agent-design.md`

## Global Constraints

- 수치는 `market_snapshot.json`과 다이제스트 원문에 있는 값만 인용한다. 생성 금지 (기존 파이프라인 원칙).
- 중간 산출물은 전부 `market-briefing/scripts/.state/` 아래에 둔다. 이 경로는 이미 `.gitignore`에 등록돼 있다.
- 수집 원문(`market-briefing/daily/`)은 제3자 저작물이므로 커밋하지 않는다.
- 텔레그램 접근은 읽기 전용. 메시지 전송·채널 참여·설정 변경 금지.
- 새 테스트는 네트워크 없이 실행돼야 한다. `yfinance`/`telethon` 임포트는 지연 임포트하거나 테스트가 건드리지 않는 모듈에 격리한다.
- 병합은 토픽 단위, 레이아웃은 채널 단위. 토픽을 뺏긴 채널에는 병합 사실을 명시한다.

---

### Task 1: 시장 지표 스냅샷 스크립트

**Files:**
- Create: `market-briefing/scripts/market_snapshot.py`
- Create: `market-briefing/tests/conftest.py`
- Create: `market-briefing/tests/test_market_snapshot.py`
- Modify: `market-briefing/requirements.txt`
- Modify: `.github/workflows/tests.yml`

**Interfaces:**
- Produces: `INDICATORS: list[tuple[str, str, str]]` — (표시명, 야후 티커, 단위)
- Produces: `build_snapshot(fetch=_fetch_history) -> dict` — `fetch(ticker) -> list[tuple[str, float]]`를 주입받아 스냅샷 dict 반환
- Produces: `main() -> None` — `.state/market_snapshot.json` 기록

- [ ] **Step 1: conftest.py 작성 (scripts/ 를 임포트 경로에 추가)**

`market-briefing/tests/conftest.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
```

- [ ] **Step 2: 실패하는 테스트 작성**

`market-briefing/tests/test_market_snapshot.py`:

```python
import market_snapshot


def _fake_fetch(ticker):
    return [("2026-09-04", 100.0), ("2026-09-05", 110.0)]


def test_build_snapshot_computes_change():
    snap = market_snapshot.build_snapshot(fetch=_fake_fetch)
    kospi = next(i for i in snap["indicators"] if i["name"] == "KOSPI")
    assert kospi["last"] == 110.0
    assert kospi["prev_close"] == 100.0
    assert kospi["change"] == 10.0
    assert kospi["change_pct"] == 10.0
    assert kospi["as_of"] == "2026-09-05"
    assert len(kospi["history"]) == 2


def test_build_snapshot_covers_all_indicators():
    snap = market_snapshot.build_snapshot(fetch=_fake_fetch)
    names = [i["name"] for i in snap["indicators"]]
    assert names == ["KOSPI", "KOSDAQ", "S&P 500", "NASDAQ", "USD/KRW", "US 10Y", "VIX"]


def test_fetch_failure_is_isolated_per_indicator():
    def boom(ticker):
        if ticker == "^VIX":
            raise RuntimeError("network down")
        return _fake_fetch(ticker)

    snap = market_snapshot.build_snapshot(fetch=boom)
    vix = next(i for i in snap["indicators"] if i["name"] == "VIX")
    assert "error" in vix
    kospi = next(i for i in snap["indicators"] if i["name"] == "KOSPI")
    assert kospi["last"] == 110.0


def test_insufficient_history_marks_error():
    snap = market_snapshot.build_snapshot(fetch=lambda t: [("2026-09-05", 100.0)])
    assert all("error" in i for i in snap["indicators"])
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `python -m pytest market-briefing/tests/test_market_snapshot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'market_snapshot'`

- [ ] **Step 4: 구현 작성**

`market-briefing/scripts/market_snapshot.py`:

```python
"""고정 시장 지표 스냅샷 — 브리핑 대시보드의 수치 근거.

숫자를 해석하지 않고 조회한 값만 기록한다. yfinance 는 지연 임포트라
테스트와 CI 는 이 패키지 없이도 돌아간다.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

_STATE_DIR = Path(__file__).resolve().parent / ".state"
_OUT_PATH = _STATE_DIR / "market_snapshot.json"

# (표시명, 야후 파이낸스 티커, 단위)
INDICATORS: list[tuple[str, str, str]] = [
    ("KOSPI", "^KS11", "index"),
    ("KOSDAQ", "^KQ11", "index"),
    ("S&P 500", "^GSPC", "index"),
    ("NASDAQ", "^IXIC", "index"),
    ("USD/KRW", "KRW=X", "fx"),
    ("US 10Y", "^TNX", "percent"),
    ("VIX", "^VIX", "index"),
]


def _fetch_history(ticker: str, period: str = "3mo") -> list[tuple[str, float]]:
    import yfinance as yf  # 지연 임포트 — 테스트는 fetch 를 주입한다

    frame = yf.Ticker(ticker).history(period=period)
    return [(idx.strftime("%Y-%m-%d"), float(row["Close"])) for idx, row in frame.iterrows()]


def build_snapshot(fetch=_fetch_history) -> dict:
    indicators = []
    for name, ticker, unit in INDICATORS:
        entry = {"name": name, "ticker": ticker, "unit": unit}
        try:
            history = fetch(ticker)
        except Exception as exc:  # noqa: BLE001 — 지표 하나 실패가 전체를 막지 않는다
            entry["error"] = str(exc)
            indicators.append(entry)
            continue
        if len(history) < 2:
            entry["error"] = "insufficient history"
            indicators.append(entry)
            continue
        last, prev = history[-1][1], history[-2][1]
        entry.update({
            "last": round(last, 2),
            "prev_close": round(prev, 2),
            "change": round(last - prev, 2),
            "change_pct": round((last - prev) / prev * 100, 2) if prev else None,
            "as_of": history[-1][0],
            "history": [{"date": d, "close": round(c, 2)} for d, c in history],
        })
        indicators.append(entry)
    return {"fetched_at": datetime.now(timezone.utc).isoformat(), "indicators": indicators}


def main() -> None:
    snapshot = build_snapshot()
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = [i for i in snapshot["indicators"] if "error" not in i]
    print(f"완료: {_OUT_PATH} ({len(ok)}/{len(snapshot['indicators'])}개 지표 수집)")
    for item in snapshot["indicators"]:
        if "error" in item:
            print(f"  실패: {item['name']} ({item['ticker']}) — {item['error']}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest market-briefing/tests/test_market_snapshot.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: requirements.txt 에 yfinance 추가**

`market-briefing/requirements.txt` 전체 내용:

```
telethon
yfinance
```

- [ ] **Step 7: CI 에 market-briefing 테스트 등록**

`.github/workflows/tests.yml` 의 `on.push.paths` 와 `on.pull_request.paths` 각각에 `- "market-briefing/**"` 를 `- "equity-report/**"` 다음 줄에 추가하고, 마지막 스텝 뒤에 아래를 붙인다:

```yaml
      - name: Run market-briefing tests
        run: python -m pytest market-briefing/tests/ -v
```

- [ ] **Step 8: 실제 네트워크로 1회 검증 (US 10Y 단위 확인)**

Run: `pip install -r market-briefing/requirements.txt && python market-briefing/scripts/market_snapshot.py`
Expected: 7개 지표 수집 성공. `US 10Y` 의 `last` 값이 3~6 범위면 퍼센트 그대로이므로 조치 불필요. 30~60 범위로 나오면 야후가 10배 스케일로 주는 것이므로 `unit` 을 `"percent_x10"` 으로 바꾸고 렌더 단계에서 10으로 나눈다는 주석을 `INDICATORS` 위에 남긴다.

- [ ] **Step 9: 커밋**

```bash
git add market-briefing/scripts/market_snapshot.py market-briefing/tests/ market-briefing/requirements.txt .github/workflows/tests.yml
git commit -m "feat: 고정 시장 지표 스냅샷 스크립트"
```

---

### Task 2: 다이제스트 파서

**Files:**
- Create: `market-briefing/scripts/build_index.py`
- Create: `market-briefing/tests/fixtures/sample_digest.md`
- Create: `market-briefing/tests/test_build_index.py`

**Interfaces:**
- Consumes: 없음
- Produces: `Message` 데이터클래스 (`channel: str`, `time: str`, `text: str`)
- Produces: `parse_digest(text: str) -> list[Message]`
- Produces: `extract_urls(text: str) -> list[str]`, `extract_tickers(text: str) -> list[str]`

- [ ] **Step 1: 픽스처 작성**

`market-briefing/tests/fixtures/sample_digest.md`:

```markdown
# 텔레그램 다이제스트 — 2026-09-06 정오 기준

## 미국 주식 인사이더
- (22:39) 미국이 이란 유조선 3척을 타격했다고 중부사령부가 발표.
  추가 대응 가능성을 경고.
  https://n.news.naver.com/article/001/0016291975?sid=104
- (23:06) 애플이 9월 9일 행사에서 폴더블 아이폰을 공개할 가능성. $AAPL 주목.

## 급등일보 미국주식
- (02:52) 미국이 이란 유조선 3척을 타격했다고 중부사령부가 발표.
  추가 대응 가능성을 경고.
  https://n.news.naver.com/article/001/0016291975
- (02:55) 골드만삭스 파트너, 채권시장 변동성 대비 VIX 가 낮다고 지적.

## 하나증권 금융팀
- (06:47) 원/달러 환율 1,350원대 하락으로 은행 외화환산익 개선 전망. $KB
```

- [ ] **Step 2: 실패하는 테스트 작성**

`market-briefing/tests/test_build_index.py`:

```python
from pathlib import Path

import build_index

_FIXTURE = Path(__file__).parent / "fixtures" / "sample_digest.md"


def _sample() -> str:
    return _FIXTURE.read_text(encoding="utf-8")


def test_parse_digest_splits_channels_and_messages():
    messages = build_index.parse_digest(_sample())
    assert len(messages) == 5
    assert messages[0].channel == "미국 주식 인사이더"
    assert messages[0].time == "22:39"
    assert messages[4].channel == "하나증권 금융팀"


def test_parse_digest_keeps_multiline_body():
    messages = build_index.parse_digest(_sample())
    assert "추가 대응 가능성을 경고" in messages[0].text
    assert "n.news.naver.com" in messages[0].text


def test_parse_digest_ignores_document_title():
    messages = build_index.parse_digest(_sample())
    assert all(m.channel != "텔레그램 다이제스트 — 2026-09-06 정오 기준" for m in messages)


def test_extract_urls_strips_query_params():
    urls = build_index.extract_urls("보도 https://n.news.naver.com/article/001/0016291975?sid=104 참고")
    assert urls == ["https://n.news.naver.com/article/001/0016291975"]


def test_extract_tickers_finds_dollar_symbols():
    assert build_index.extract_tickers("$AAPL 와 $KB 를 본다") == ["$AAPL", "$KB"]
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `python -m pytest market-briefing/tests/test_build_index.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'build_index'`

- [ ] **Step 4: 파서 구현**

`market-briefing/scripts/build_index.py` (이 태스크 범위까지만 작성):

```python
"""다이제스트 원문 -> 축약 인덱스 + 중복 그룹.

오케스트레이터가 원문 전체(수십만 토큰) 대신 이 인덱스만 읽고 토픽을
클러스터링한다. 중복 탐지 중 확실한 신호(같은 URL, 거의 같은 문장)는
여기서 결정적으로 처리하고, 의미 수준 병합만 LLM 이 판단한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_CHANNEL_RE = re.compile(r"^## (.+)$")
_MESSAGE_RE = re.compile(r"^- \((\d{2}:\d{2})\) (.*)$")
_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_TICKER_RE = re.compile(r"\$[A-Z]{1,5}\b")


@dataclass
class Message:
    channel: str
    time: str
    text: str


def parse_digest(text: str) -> list[Message]:
    messages: list[Message] = []
    channel: str | None = None
    current: Message | None = None
    for line in text.splitlines():
        header = _CHANNEL_RE.match(line)
        if header:
            channel = header.group(1).strip()
            current = None
            continue
        body = _MESSAGE_RE.match(line)
        if body and channel:
            current = Message(channel=channel, time=body.group(1), text=body.group(2))
            messages.append(current)
            continue
        if current is not None:
            current.text += "\n" + line
    for message in messages:
        message.text = message.text.strip()
    return messages


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    for raw in _URL_RE.findall(text):
        url = re.sub(r"[?#].*$", "", raw.rstrip(".,"))
        if url not in urls:
            urls.append(url)
    return urls


def extract_tickers(text: str) -> list[str]:
    tickers: list[str] = []
    for ticker in _TICKER_RE.findall(text):
        if ticker not in tickers:
            tickers.append(ticker)
    return tickers
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `python -m pytest market-briefing/tests/test_build_index.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: 커밋**

```bash
git add market-briefing/scripts/build_index.py market-briefing/tests/
git commit -m "feat: 다이제스트 파서 (채널·메시지 분해, URL·티커 추출)"
```

---

### Task 3: 중복 탐지

**Files:**
- Modify: `market-briefing/scripts/build_index.py` (파서 뒤에 추가)
- Modify: `market-briefing/tests/test_build_index.py` (테스트 추가)

**Interfaces:**
- Consumes: `Message`, `parse_digest`, `extract_urls` (Task 2)
- Produces: `similarity(a: str, b: str) -> float` — 문자 5-gram 자카드 유사도
- Produces: `find_duplicate_groups(messages: list[Message], threshold: float = 0.6) -> list[dict]` — `[{"members": [int, ...], "reason": "shared_url" | "similar_text"}]`, 2건 이상인 그룹만 반환

- [ ] **Step 1: 실패하는 테스트 추가**

`market-briefing/tests/test_build_index.py` 끝에 추가:

```python
def test_similarity_is_high_for_near_identical_korean_text():
    a = "미국이 이란 유조선 3척을 타격했다고 중부사령부가 발표했다"
    b = "미국이 이란 유조선 3척을 타격했다고 중부사령부가 발표"
    assert build_index.similarity(a, b) > 0.6


def test_similarity_is_low_for_unrelated_text():
    a = "미국이 이란 유조선 3척을 타격했다고 중부사령부가 발표했다"
    b = "원/달러 환율 하락으로 은행 외화환산익이 개선될 전망이다"
    assert build_index.similarity(a, b) < 0.3


def test_find_duplicate_groups_merges_shared_url_across_channels():
    messages = build_index.parse_digest(_sample())
    groups = build_index.find_duplicate_groups(messages)
    merged = [g for g in groups if 0 in g["members"]]
    assert len(merged) == 1
    assert set(merged[0]["members"]) == {0, 2}
    assert merged[0]["reason"] == "shared_url"


def test_find_duplicate_groups_leaves_unrelated_messages_alone():
    messages = build_index.parse_digest(_sample())
    groups = build_index.find_duplicate_groups(messages)
    grouped = {idx for g in groups for idx in g["members"]}
    assert 4 not in grouped


def test_find_duplicate_groups_ignores_same_channel_repeats():
    messages = [
        build_index.Message(channel="A", time="01:00", text="같은 채널의 비슷한 문장입니다"),
        build_index.Message(channel="A", time="02:00", text="같은 채널의 비슷한 문장입니다!"),
    ]
    assert build_index.find_duplicate_groups(messages) == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest market-briefing/tests/test_build_index.py -v`
Expected: FAIL — `AttributeError: module 'build_index' has no attribute 'similarity'`

- [ ] **Step 3: 중복 탐지 구현**

`market-briefing/scripts/build_index.py` 의 `extract_tickers` 뒤에 추가:

```python
def _normalize(text: str) -> str:
    stripped = _URL_RE.sub(" ", text)
    stripped = re.sub(r"[^0-9A-Za-z가-힣]+", " ", stripped)
    return re.sub(r"\s+", " ", stripped).strip().lower()


def _shingles(text: str, size: int = 5) -> set[str]:
    packed = _normalize(text).replace(" ", "")
    if not packed:
        return set()
    if len(packed) < size:
        return {packed}
    return {packed[i:i + size] for i in range(len(packed) - size + 1)}


def similarity(a: str, b: str) -> float:
    """문자 5-gram 자카드 유사도. 한국어 형태소 분석기 없이도 동작한다."""
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def find_duplicate_groups(messages: list[Message], threshold: float = 0.6) -> list[dict]:
    """같은 이슈를 다룬 메시지를 묶는다. 같은 채널 안의 반복은 묶지 않는다."""
    parent = list(range(len(messages)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    url_linked: set[int] = set()
    url_owner: dict[str, int] = {}
    for i, message in enumerate(messages):
        for url in extract_urls(message.text):
            owner = url_owner.get(url)
            if owner is None:
                url_owner[url] = i
            elif messages[owner].channel != message.channel:
                union(owner, i)
                url_linked.update({owner, i})

    for i in range(len(messages)):
        for j in range(i + 1, len(messages)):
            if messages[i].channel == messages[j].channel:
                continue
            if find(i) == find(j):
                continue
            if similarity(messages[i].text, messages[j].text) >= threshold:
                union(i, j)

    buckets: dict[int, list[int]] = {}
    for i in range(len(messages)):
        buckets.setdefault(find(i), []).append(i)

    groups = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        reason = "shared_url" if url_linked & set(members) else "similar_text"
        groups.append({"members": sorted(members), "reason": reason})
    return sorted(groups, key=lambda g: g["members"][0])
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `python -m pytest market-briefing/tests/test_build_index.py -v`
Expected: PASS (10 passed)

- [ ] **Step 5: 커밋**

```bash
git add market-briefing/scripts/build_index.py market-briefing/tests/test_build_index.py
git commit -m "feat: URL·문자 n-gram 기반 중복 탐지"
```

---

### Task 4: 다이제스트 매니페스트 + 인덱스 조립

**Files:**
- Create: `market-briefing/scripts/digest_manifest.py`
- Modify: `market-briefing/scripts/telegram_digest.py` (매니페스트 기록 추가)
- Modify: `market-briefing/scripts/build_index.py` (인덱스 조립 + CLI 추가)
- Create: `market-briefing/tests/test_digest_manifest.py`
- Modify: `market-briefing/tests/test_build_index.py`

**Interfaces:**
- Produces: `digest_manifest.manifest_path(daily_dir: Path, date: str) -> Path`
- Produces: `digest_manifest.write_manifest(path: Path, matched: list[str], missing: list[str], collected: list[str]) -> None`
- Produces: `digest_manifest.read_manifest(path: Path) -> dict` — 파일이 없으면 `{"matched": [], "missing": [], "collected": []}`
- Produces: `build_index.build_index(digest_text: str, manifest: dict, date: str) -> dict`
- Produces: `build_index.main() -> None` — 오늘자 다이제스트를 읽어 `.state/briefing_index.json` 기록

`telethon` 을 임포트하지 않는 별도 모듈(`digest_manifest.py`)에 매니페스트 입출력을 두는 이유는, CI 가 텔레그램 패키지 없이 테스트를 돌릴 수 있게 하기 위해서다.

- [ ] **Step 1: 매니페스트 테스트 작성**

`market-briefing/tests/test_digest_manifest.py`:

```python
from pathlib import Path

import digest_manifest


def test_manifest_path_uses_date(tmp_path):
    path = digest_manifest.manifest_path(tmp_path, "2026-09-06")
    assert path == tmp_path / "2026-09-06.manifest.json"


def test_write_then_read_roundtrip(tmp_path):
    path = digest_manifest.manifest_path(tmp_path, "2026-09-06")
    digest_manifest.write_manifest(path, matched=["A", "B"], missing=["C"], collected=["A"])
    data = digest_manifest.read_manifest(path)
    assert data["matched"] == ["A", "B"]
    assert data["missing"] == ["C"]
    assert data["collected"] == ["A"]


def test_read_missing_file_returns_empty_lists(tmp_path):
    data = digest_manifest.read_manifest(tmp_path / "nope.json")
    assert data == {"matched": [], "missing": [], "collected": []}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `python -m pytest market-briefing/tests/test_digest_manifest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'digest_manifest'`

- [ ] **Step 3: 매니페스트 모듈 구현**

`market-briefing/scripts/digest_manifest.py`:

```python
"""다이제스트 실행 결과 매니페스트.

"오늘 새 글 없음"(가입돼 있으나 조용한 채널)과 "미가입"을 구분하려면 수집
시점의 매칭 결과가 필요하다. telethon 을 임포트하지 않아 CI 에서도 테스트된다.
"""
from __future__ import annotations

import json
from pathlib import Path

_EMPTY = {"matched": [], "missing": [], "collected": []}


def manifest_path(daily_dir: Path, date: str) -> Path:
    return daily_dir / f"{date}.manifest.json"


def write_manifest(path: Path, matched: list[str], missing: list[str], collected: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"matched": matched, "missing": missing, "collected": collected}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_manifest(path: Path) -> dict:
    if not path.exists():
        return dict(_EMPTY)
    data = json.loads(path.read_text(encoding="utf-8"))
    return {key: data.get(key, []) for key in _EMPTY}
```

- [ ] **Step 4: 매니페스트 테스트 통과 확인**

Run: `python -m pytest market-briefing/tests/test_digest_manifest.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: telegram_digest.py 가 매니페스트를 기록하도록 수정**

`market-briefing/scripts/telegram_digest.py` 의 임포트 블록에서 `from channels import CHANNEL_TITLES` 아래에 추가:

```python
import digest_manifest
```

`main()` 안에서 `out_path = _write_digest(collected)` 바로 다음 줄에 추가:

```python
    digest_manifest.write_manifest(
        digest_manifest.manifest_path(_DAILY_DIR, datetime.now().strftime("%Y-%m-%d")),
        matched=[t for t in CHANNEL_TITLES if t not in missing_titles],
        missing=missing_titles,
        collected=list(collected),
    )
```

- [ ] **Step 6: 인덱스 조립 테스트 추가**

`market-briefing/tests/test_build_index.py` 끝에 추가:

```python
def test_build_index_groups_messages_by_channel():
    manifest = {"matched": ["미국 주식 인사이더", "급등일보 미국주식", "하나증권 금융팀", "Polaristimes"],
                "missing": ["KB시황 하인환"], "collected": []}
    index = build_index.build_index(_sample(), manifest, "2026-09-06")
    assert index["date"] == "2026-09-06"
    assert index["channels"]["미국 주식 인사이더"]["message_count"] == 2
    assert index["channels"]["하나증권 금융팀"]["message_count"] == 1


def test_build_index_marks_duplicate_group_on_messages():
    manifest = {"matched": [], "missing": [], "collected": []}
    index = build_index.build_index(_sample(), manifest, "2026-09-06")
    first = index["channels"]["미국 주식 인사이더"]["messages"][0]
    second = index["channels"]["급등일보 미국주식"]["messages"][0]
    assert first["duplicate_group"] is not None
    assert first["duplicate_group"] == second["duplicate_group"]


def test_build_index_separates_quiet_and_unsubscribed_channels():
    manifest = {"matched": ["미국 주식 인사이더", "Polaristimes"],
                "missing": ["KB시황 하인환"], "collected": []}
    index = build_index.build_index(_sample(), manifest, "2026-09-06")
    assert index["quiet_channels"] == ["Polaristimes"]
    assert index["unsubscribed_channels"] == ["KB시황 하인환"]


def test_build_index_headline_is_truncated():
    manifest = {"matched": [], "missing": [], "collected": []}
    index = build_index.build_index(_sample(), manifest, "2026-09-06")
    for channel in index["channels"].values():
        for message in channel["messages"]:
            assert len(message["headline"]) <= 120
```

- [ ] **Step 7: 테스트 실패 확인**

Run: `python -m pytest market-briefing/tests/test_build_index.py -v`
Expected: FAIL — `AttributeError: module 'build_index' has no attribute 'build_index'`

- [ ] **Step 8: 인덱스 조립 + CLI 구현**

`market-briefing/scripts/build_index.py` 상단 임포트를 아래로 교체:

```python
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import digest_manifest

_SCRIPT_DIR = Path(__file__).resolve().parent
_STATE_DIR = _SCRIPT_DIR / ".state"
_DAILY_DIR = _SCRIPT_DIR.parent / "daily"
_OUT_PATH = _STATE_DIR / "briefing_index.json"
```

파일 끝에 추가:

```python
def _headline(text: str, limit: int = 120) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:limit]
    return ""


def build_index(digest_text: str, manifest: dict, date: str) -> dict:
    messages = parse_digest(digest_text)
    groups = find_duplicate_groups(messages)

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
        })

    return {
        "date": date,
        "channels": channels,
        "duplicate_groups": [
            {"group_id": f"g{number}", "reason": group["reason"],
             "members": [{"channel": messages[i].channel, "index": i} for i in group["members"]]}
            for number, group in enumerate(groups)
        ],
        "quiet_channels": [c for c in manifest.get("matched", []) if c not in channels],
        "unsubscribed_channels": list(manifest.get("missing", [])),
    }


def main() -> None:
    date = datetime.now().strftime("%Y-%m-%d")
    digest_path = _DAILY_DIR / f"{date}.md"
    if not digest_path.exists():
        raise SystemExit(f"오늘자 다이제스트가 없습니다: {digest_path}\ntelegram_digest.py 를 먼저 실행하세요.")

    manifest = digest_manifest.read_manifest(digest_manifest.manifest_path(_DAILY_DIR, date))
    index = build_index(digest_path.read_text(encoding="utf-8"), manifest, date)

    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

    total = sum(c["message_count"] for c in index["channels"].values())
    raw_kb = len(digest_path.read_text(encoding="utf-8")) / 1024
    index_kb = _OUT_PATH.stat().st_size / 1024
    print(f"완료: {_OUT_PATH}")
    print(f"  채널 {len(index['channels'])}개 / 메시지 {total}건 / 중복그룹 {len(index['duplicate_groups'])}개")
    print(f"  원문 {raw_kb:.0f}KB -> 인덱스 {index_kb:.0f}KB")


if __name__ == "__main__":
    main()
```

- [ ] **Step 9: 전체 테스트 통과 확인**

Run: `python -m pytest market-briefing/tests/ -v`
Expected: PASS (21 passed — 지표 4 + 인덱스 14 + 매니페스트 3)

- [ ] **Step 10: 실제 데이터로 검증**

Run: `python market-briefing/scripts/build_index.py`
Expected: 오늘자 다이제스트 기준으로 채널/메시지/중복그룹 수가 출력되고, 인덱스 크기가 원문의 20% 이하. 중복그룹이 0개면 `threshold` 를 0.5로 낮춰 재실행해 비교한 뒤, 사람이 보기에 같은 이슈가 묶이는 값을 고른다.

- [ ] **Step 11: 커밋**

```bash
git add market-briefing/scripts/ market-briefing/tests/
git commit -m "feat: 다이제스트 매니페스트 + 축약 인덱스 생성"
```

---

### Task 5: 서브에이전트 정의 2개

**Files:**
- Modify: `.gitignore` (`.claude/agents/` 추적 허용)
- Create: `.claude/agents/briefing-section-writer.md`
- Create: `.claude/agents/briefing-reviewer.md`

**Interfaces:**
- Consumes: `.state/briefing_index.json` (Task 4), `.state/market_snapshot.json` (Task 1)
- Produces: 섹션 JSON 스키마 — `topic_id`, `title`, `primary_channel`, `merged_channels`, `organized`, `summary`, `indicators`, `draft_insight`, `sources`, `verification_notes`
- Produces: 서브에이전트 타입 `briefing-section-writer`, `briefing-reviewer`

- [ ] **Step 1: .gitignore 수정 — 에이전트 정의만 추적**

`.gitignore` 의 `.claude/` 한 줄을 아래 두 줄로 교체한다. git 은 부모 디렉터리가 제외되면 하위를 다시 포함시킬 수 없으므로, 디렉터리 자체가 아니라 내용물을 제외해야 한다.

```
.claude/*
!.claude/agents/
```

- [ ] **Step 2: 수정이 먹혔는지 확인**

Run: `mkdir -p .claude/agents && touch .claude/agents/.probe && git check-ignore -v .claude/agents/.probe; rm .claude/agents/.probe`
Expected: 아무 출력 없음 (무시되지 않는다는 뜻). `.gitignore:19:.claude/*` 같은 출력이 나오면 Step 1 을 다시 확인한다.

- [ ] **Step 3: 섹션 작성 에이전트 정의**

`.claude/agents/briefing-section-writer.md`:

```markdown
---
name: briefing-section-writer
description: 마켓브리핑 토픽 1개를 맡아 원문과 시장 지표를 읽고 정리·요약·지표·초안 인사이트를 담은 섹션 JSON 을 작성한다. 오케스트레이터가 토픽마다 병렬로 띄운다.
model: sonnet
tools: Read, Grep, Glob, Write, WebSearch, WebFetch
---

너는 마켓브리핑 섹션 작성자다. 토픽 하나만 담당한다.

## 받는 것

프롬프트로 아래를 받는다.
- `topic_id`, `title` — 이 토픽의 식별자와 제목
- `primary_channel` — 이 섹션의 대표 채널
- `merged_channels` — 같은 이슈를 다뤄 이 섹션으로 병합된 다른 채널들
- `message_refs` — `{"channel": ..., "index": ...}` 목록. 다이제스트 원문에서 읽을 메시지 위치
- `digest_path` — 원문 마크다운 경로
- `snapshot_path` — 시장 지표 JSON 경로
- `out_path` — 결과를 쓸 경로

## 하는 일

1. `digest_path` 를 읽어 `message_refs` 가 가리키는 메시지들을 찾는다. 채널 헤더는
   `## 채널명`, 메시지는 `- (HH:MM) ` 로 시작하며 다음 메시지 전까지가 한 건이다.
   `index` 는 문서 전체에서 메시지가 등장한 순서(0부터)다.
2. `snapshot_path` 를 읽어 이 토픽과 관련 있는 지표만 고른다. 예를 들어 채권·금리
   이슈면 US 10Y 와 VIX, 환율 이슈면 USD/KRW, 미장 이슈면 S&P 500 과 NASDAQ.
3. 원문에 있는 사실 중 확인이 필요한 핵심 1~3건을 WebSearch 로 검증한다. 검색은
   최대 5회. 확인된 것과 확인 못 한 것을 구분해 기록한다.
4. `out_path` 에 아래 스키마의 JSON 을 쓴다.

```json
{
  "topic_id": "t1",
  "title": "이란-미국 군사 충돌 격화",
  "primary_channel": "미국 주식 인사이더",
  "merged_channels": ["급등일보 미국주식"],
  "organized": "사실관계를 시간순으로. 날짜·수치·주체를 명시.",
  "summary": "이 이슈가 시장에서 갖는 의미 1~2문장.",
  "indicators": [
    {"label": "VIX", "value": "17.32", "change": "+4.1%", "source": "market_snapshot"}
  ],
  "draft_insight": "투자·금융에 미치는 영향과 취해야 할 태도에 대한 초안. 사용자가 고쳐 쓸 재료다.",
  "sources": [
    {"type": "telegram", "channel": "미국 주식 인사이더", "time": "22:39"},
    {"type": "web", "url": "https://...", "title": "..."}
  ],
  "verification_notes": "확인된 사실 / 확인하지 못한 주장"
}
```

## 규칙

- **수치를 지어내지 않는다.** `indicators` 의 값은 `snapshot_path` 에서, 본문의 수치는
  원문이나 검색으로 확인한 출처에서만 가져온다. 출처 없는 숫자는 쓰지 않는다.
- 텔레그램 채널의 주장과 확인된 사실을 구분한다. 확인 못 한 주장은 "OO 채널 주장"
  으로 명시한다.
- `draft_insight` 는 초안이다. 단정적으로 결론 내지 말고 판단 근거와 함께 제시한다.
- `merged_channels` 가 있으면 그 채널들의 메시지도 함께 읽고 반영한다. 같은 이슈를
  다른 각도로 다뤘다면 그 차이를 `organized` 에 녹인다.
- 다른 토픽은 건드리지 않는다. 네 토픽 범위를 넘는 내용이 보이면 무시한다.

## 끝내고 보고할 것

`out_path` 에 파일을 쓴 뒤, 세 줄로 보고한다 — 토픽 제목, 검증한 항목 수, 지표
몇 개를 붙였는지. 섹션 본문을 보고에 복사하지 마라.
```

- [ ] **Step 4: 검수 에이전트 정의**

`.claude/agents/briefing-reviewer.md`:

```markdown
---
name: briefing-reviewer
description: 작성된 마켓브리핑 섹션들을 모아 읽고 섹션 간 잔여 중복·수치 불일치·누락을 찾아 보고한다. 섹션이 6개 이상일 때 오케스트레이터가 띄운다.
model: sonnet
tools: Read, Grep, Glob
---

너는 마켓브리핑 검수자다. 고치지 말고 찾아서 보고만 한다.

## 받는 것

- `sections_dir` — 섹션 JSON 들이 있는 디렉터리
- `snapshot_path` — 시장 지표 JSON 경로
- `index_path` — 축약 인덱스 JSON 경로

## 확인할 것

1. **잔여 중복** — 섹션 작성자들은 서로를 보지 못한다. 두 섹션이 사실상 같은 이슈를
   다루고 있으면 어느 쪽으로 합쳐야 하는지 지적한다.
2. **수치 일관성** — 섹션의 `indicators` 값이 `snapshot_path` 의 값과 일치하는가.
   같은 지표를 두 섹션이 다르게 적었는가.
3. **출처 없는 수치** — 본문에 등장하는 숫자 중 `sources` 나 스냅샷으로 뒷받침되지
   않는 것이 있는가.
4. **누락** — `index_path` 의 중복그룹 중 어느 섹션에도 반영되지 않은 것이 있는가.

## 보고 형식

발견한 문제만 심각한 순서로 나열한다. 문제가 없으면 "이상 없음"이라고만 답한다.
각 항목은 `[섹션 파일명] 문제 한 줄 + 근거` 형태로 쓴다. 섹션 전문을 복사하지 마라.
```

- [ ] **Step 5: 에이전트 인식 확인**

Run: `git status --short .claude/agents/`
Expected: 두 파일이 `??` 로 표시됨 (추적 가능 상태). 표시되지 않으면 Step 1 로 돌아간다.

- [ ] **Step 6: 커밋**

```bash
git add .gitignore .claude/agents/
git commit -m "feat: 브리핑 섹션 작성·검수 서브에이전트 정의"
```

---

### Task 6: 오케스트레이터 스킬 + CLI 스킬 3개

**Files:**
- Create: `.agents/skills/brief/SKILL.md`
- Create: `.agents/skills/brief-collect/SKILL.md`
- Create: `.agents/skills/brief-topics/SKILL.md`
- Create: `.agents/skills/brief-dig/SKILL.md`
- Modify: `.agents/skills/market-briefing/SKILL.md`

**Interfaces:**
- Consumes: Task 1~5 전부 (`market_snapshot.py`, `build_index.py`, 서브에이전트 2종)
- Produces: `/brief`, `/brief-collect`, `/brief-topics`, `/brief-dig` 진입점

- [ ] **Step 1: 오케스트레이터 스킬 작성**

`.agents/skills/brief/SKILL.md`:

```markdown
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
python market-briefing/scripts/telegram_digest.py
python market-briefing/scripts/market_snapshot.py
```

윈도우에서 한글 출력이 깨지면 `PYTHONIOENCODING=utf-8` 을 앞에 붙인다.
텔레그램 세션이 없다는 오류가 나면 사용자에게 별도 터미널에서
`python market-briefing/scripts/telegram_login_setup.py` 실행을 요청한다.

### 2. 축약 인덱스 (파이썬)

```bash
python market-briefing/scripts/build_index.py
```

산출: `market-briefing/scripts/.state/briefing_index.json`

### 3. 토픽 클러스터링 (직접 수행 — 서브에이전트에 넘기지 않는다)

`briefing_index.json` **만** 읽는다. 원문 전체를 읽지 마라. 인덱스의 헤드라인과
`duplicate_groups` 를 근거로 토픽을 4~7개로 묶고 `.state/topics.json` 에 쓴다.

```json
{
  "date": "2026-09-06",
  "topics": [
    {
      "topic_id": "t1",
      "title": "이란-미국 군사 충돌 격화",
      "primary_channel": "미국 주식 인사이더",
      "merged_channels": ["급등일보 미국주식"],
      "message_refs": [{"channel": "미국 주식 인사이더", "index": 0},
                       {"channel": "급등일보 미국주식", "index": 12}]
    }
  ]
}
```

primary 채널은 그 이슈를 가장 자세히 다룬 채널로 정한다. `duplicate_groups` 는
확실한 중복만 잡은 것이므로, 표현이 달라 안 잡힌 같은 이슈는 헤드라인을 보고
직접 병합한다.

### 4. 섹션 작성 (서브에이전트 병렬)

먼저 출력 디렉터리를 만든다.

```bash
mkdir -p market-briefing/scripts/.state/sections
```

토픽마다 `briefing-section-writer` 를 하나씩, **한 번의 응답에서 전부** 띄운다.
각 프롬프트에 topic_id·title·primary_channel·merged_channels·message_refs 와
아래 경로들을 넣는다.

- digest_path: `market-briefing/daily/<날짜>.md`
- snapshot_path: `market-briefing/scripts/.state/market_snapshot.json`
- out_path: `market-briefing/scripts/.state/sections/<topic_id>.json`

### 5. 검수 (조건부)

섹션이 6개 이상이면 `briefing-reviewer` 를 띄운다. 그보다 적으면 직접 확인한다.
지적된 문제는 해당 섹션 JSON 을 고쳐서 반영한다.

### 6. 렌더

`artifact-design` 과 `dataviz` 스킬을 먼저 로드한 뒤 HTML 을 쓴다.

레이아웃:
- 상단: 고정 지표 카드 7개 (현재가·등락률·3개월 스파크라인)
- 중단: 채널별 섹션. 각 섹션에 정리·요약·지표·초안 인사이트.
  병합된 채널에는 `→ OO 이슈는 「XX」 섹션으로 병합됨` 을 먼저 적고 고유 내용을 잇는다.
- 하단: `오늘 새 글 없음: A, B, C` 와 `미가입: D, E` 를 각각 한 줄로

발행:
- **최초 1회**: `favicon` 을 붙여 새로 발행하고, 받은 URL 을
  `market-briefing/DASHBOARD_URL.txt` 에 기록한다.
- **이후**: `DASHBOARD_URL.txt` 의 URL 을 `url` 인자로 넘겨 같은 주소를 갱신한다.
  `favicon` 은 넘기지 않는다. 발행 전에 `action: "read"` 로 현재 버전을 먼저 읽는다.

마지막으로 채팅에 링크와 3~5줄 핵심 요약을 남긴다. 섹션 전문을 채팅에 복사하지 마라.

## 하지 않는 것

- 원문 다이제스트 전체를 오케스트레이터 컨텍스트에 적재하지 않는다. 3단계는
  인덱스만, 원문은 섹션 작성 에이전트가 각자 자기 몫만 읽는다.
- 채널당 에이전트 1개로 팬아웃하지 않는다. 그 구조로는 채널 간 중복을 볼 수 없다.
- 수집·중복탐지를 에이전트에게 시키지 않는다. 결정적 작업은 파이썬이 한다.
```

- [ ] **Step 2: 얇은 CLI 스킬 3개 작성**

`.agents/skills/brief-collect/SKILL.md`:

```markdown
---
name: brief-collect
description: 텔레그램 다이제스트와 시장 지표만 수집하고 합성은 하지 않는다. "수집만 해줘", "데이터만 받아둬" 같은 요청에 사용한다.
---

# 수집 전용

`brief` 스킬의 1~2단계만 실행한다.

```bash
python market-briefing/scripts/telegram_digest.py
python market-briefing/scripts/market_snapshot.py
python market-briefing/scripts/build_index.py
```

각 스크립트의 출력 요약(채널 수·메시지 수·중복그룹 수·실패한 지표)만 채팅에
보고하고 끝낸다. 토픽 클러스터링·섹션 작성·Artifact 발행은 하지 않는다.
자세한 절차와 문제 해결은 `brief` 스킬을 참조한다.
```

`.agents/skills/brief-topics/SKILL.md`:

```markdown
---
name: brief-topics
description: 오늘 수집된 소식의 토픽 목록만 빠르게 확인한다. 섹션 작성이나 대시보드 발행 없이 무슨 이슈가 있는지만 보고 싶을 때 사용한다.
---

# 토픽 인덱스만 확인

`brief` 스킬의 1~3단계만 실행한다. 수집 스크립트를 돌리고
`.state/briefing_index.json` 만 읽어 토픽을 묶은 뒤, 채팅에 표로 보여준다.

| 토픽 | 대표 채널 | 병합된 채널 | 메시지 수 |
|---|---|---|---|

`.state/topics.json` 에도 저장해 두면 이어서 `/brief` 를 부를 때 재사용된다.
서브에이전트는 띄우지 않는다.
```

`.agents/skills/brief-dig/SKILL.md`:

```markdown
---
name: brief-dig
description: 특정 토픽 하나를 깊게 파고든다. "OO 이슈 자세히", "그 토픽 더 알아봐" 같은 요청에 사용한다.
---

# 토픽 심층 분석

인자로 받은 토픽(또는 사용자가 지목한 이슈)에 대해 `briefing-section-writer`
서브에이전트를 **웹 검색 상한을 15회로 올려** 한 개만 띄운다.

`.state/topics.json` 이 있으면 거기서 해당 토픽의 `message_refs` 를 가져오고,
없으면 `brief` 스킬의 1~3단계를 먼저 실행한다.

결과는 대시보드에 반영하지 않고 채팅에 정리해 보여준다. 대시보드까지 갱신하려면
`/brief` 를 쓴다.
```

- [ ] **Step 3: 주간 마켓브리핑 스킬이 새 산출물을 참조하도록 수정**

`.agents/skills/market-briefing/SKILL.md` 의 `## 1단계 — 원문 수집` 첫 문단을 아래로 교체한다:

```markdown
먼저 `market-briefing/scripts/.state/sections/*.json` 에 온디맨드 브리핑(`/brief`)이
남긴 섹션이 있는지 확인한다. 있으면 이미 중복 제거·검증까지 끝난 재료이므로
그대로 활용한다. 없으면 `market-briefing/daily/*.md` 의 원문 다이제스트를 읽는다.
어느 쪽이든 **최종본이 아니라 원자재**이므로 그대로 인용하지 말고 사실관계만
참고한다.
```

- [ ] **Step 4: 스킬 인식 확인**

Run: `ls .agents/skills/`
Expected: `brief`, `brief-collect`, `brief-dig`, `brief-topics`, `equity-report`, `find-skills`, `market-briefing` 이 보인다.

- [ ] **Step 5: 커밋**

```bash
git add .agents/skills/
git commit -m "feat: 브리핑 오케스트레이터 스킬 + CLI 진입점 3개"
```

---

### Task 7: 첫 대시보드 발행 및 문서 갱신

**Files:**
- Create: `market-briefing/DASHBOARD_URL.txt`
- Modify: `market-briefing/README.md`

**Interfaces:**
- Consumes: Task 1~6 전부
- Produces: 대시보드 Artifact URL (이후 호출은 이 URL 을 갱신)

- [ ] **Step 1: 파이프라인 전체를 실제로 1회 실행**

`brief` 스킬의 1~5단계를 순서대로 실행한다. 각 단계 산출물이 만들어졌는지 확인한다.

Run: `ls market-briefing/scripts/.state/ market-briefing/scripts/.state/sections/`
Expected: `market_snapshot.json`, `briefing_index.json`, `topics.json`, `sections/t*.json`

- [ ] **Step 2: 대시보드 HTML 작성 및 발행**

`artifact-design` 과 `dataviz` 스킬을 로드한 뒤 HTML 을 쓰고, `favicon` 을 붙여
Artifact 로 발행한다. 레이아웃은 `brief` 스킬의 6단계 규정을 따르되, 아래 요소는
반드시 포함한다.

- `<title>` — 짧은 고유 이름 (예: `마켓 브리핑 대시보드`). 이후 갱신에서 바꾸지 않는다.
- 지표 카드 7개 — 각각 표시명, 현재가, 등락률(상승/하락 색 구분), `as_of` 날짜,
  3개월 종가 스파크라인(인라인 SVG). `error` 가 있는 지표는 값 자리에 "조회 실패"
  를 표시하고 카드를 지우지 않는다.
- 채널 섹션 — 채널명(h2), 정리·요약·지표·초안 인사이트를 라벨과 함께. 초안
  인사이트는 시각적으로 구분해 "초안" 임을 명시한다.
- 병합 표기 — 토픽을 뺏긴 채널에는 `→ OO 이슈는 「XX」 섹션으로 병합됨` 을 섹션
  본문 앞에 눈에 띄게 배치한다.
- 하단 — `오늘 새 글 없음: …` 과 `미가입: …` 각 한 줄, 그리고 생성 시각.
- 라이트/다크 양쪽에서 읽히도록 색 토큰을 `:root` 에 정의한다.
- 표·차트는 `overflow-x: auto` 컨테이너 안에 넣어 본문이 가로로 밀리지 않게 한다.

- [ ] **Step 3: URL 기록**

발행 결과로 받은 URL 을 `market-briefing/DASHBOARD_URL.txt` 에 한 줄로 저장한다.
이 파일이 이후 갱신의 기준이 되므로 커밋한다.

- [ ] **Step 4: README 갱신**

`market-briefing/README.md` 의 `## 진행 방식 요약` 앞에 아래 절을 삽입한다:

```markdown
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
```

`## 폴더 구성` 표에 아래 행들을 추가한다:

```markdown
| `scripts/market_snapshot.py` | 고정 시장 지표 조회 (yfinance) |
| `scripts/build_index.py` | 다이제스트 축약 + 중복 탐지 |
| `scripts/digest_manifest.py` | 수집 매니페스트 입출력 |
| `DASHBOARD_URL.txt` | 대시보드 Artifact 주소 (갱신 대상) |
```

- [ ] **Step 5: 사용자 피드백 받기**

대시보드 링크를 사용자에게 전달하고 정보량·레이아웃·지표 구성에 대한 피드백을
요청한다. 지적된 부분은 `brief` 스킬의 6단계 규정을 고쳐서 다음 호출부터
반영되게 한다.

- [ ] **Step 6: 커밋**

```bash
git add market-briefing/README.md market-briefing/DASHBOARD_URL.txt
git commit -m "feat: 브리핑 대시보드 첫 발행 + 문서 갱신"
```

---

## 실행 후 검토 대상 (이번 범위 밖)

- 캐싱 — 같은 날 재호출 시 시세·인덱스 재사용
- 증분 클러스터링 — 새 메시지만 기존 토픽에 편입
- 이슈별 동적 종목 차트
- `find_duplicate_groups` 의 O(n²) 비교 — 메시지 1,000건 넘으면 최적화 검토
