"""고정 시장 지표 스냅샷 — 브리핑 대시보드의 수치 근거.

숫자를 해석하지 않고 조회한 값만 기록한다. yfinance 는 지연 임포트라
테스트와 CI 는 이 패키지 없이도 돌아간다.

출처는 지표마다 다르다. 코스피·코스닥·한국 국채는 토스증권 Open API(KRX 원천)를,
토스 심볼 카탈로그에 없는 미국 지수·금리·VIX 는 야후를 쓴다.

토스를 쓰는 이유는 **당일 종가 정확도**다. 야후는 장중에 조회하면 그 시점 값을
`as_of=오늘` 로 돌려줘 종가처럼 보인다 — 2026-09-08 15:08 KST 조회에서 야후는
코스피 7,046.74 를 줬지만 실제 종가는 6,954.52 였고, 그 92포인트 차이 때문에
브리핑이 "7000선 회복"이라는 틀린 제목을 달았다.

과거 시계열 자체는 야후도 정확하다. 두 출처를 65거래일 대조한 결과 값이 다른 날은
당일 하나뿐이었다. 예전 주석의 "야후 시계열에 비정상 변동이 섞여 있다"는 서술은
틀렸다 — 코스피의 일간 17.9% 변동은 데이터 오류가 아니라 실제 장세였다.

두 파일을 쓴다.
- market_snapshot.json : 시계열 포함 (렌더용, 약 40KB)
- snapshot_brief.json  : 시계열 제외 (에이전트가 읽는 용도, 약 1KB)
시계열은 스파크라인에만 쓰이는데 에이전트가 전체를 읽으면 호출당 8K 토큰이 낭비된다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

_STATE_DIR = Path(__file__).resolve().parent / ".state"
_OUT_PATH = _STATE_DIR / "market_snapshot.json"
_BRIEF_PATH = _STATE_DIR / "snapshot_brief.json"

TOSS = "toss"
YAHOO = "yahoo"

# (표시명, 조회 키, 단위, 기본 출처, 야후 폴백 티커)
# 폴백 티커가 None 이면 토스 없이는 조회할 방법이 없는 지표다 — 조용히 건너뛴다.
INDICATORS: list[tuple[str, str, str, str, str | None]] = [
    ("KOSPI", "KOSPI", "index", TOSS, "^KS11"),
    ("KOSDAQ", "KOSDAQ", "index", TOSS, "^KQ11"),
    ("KR 10Y", "KR_BOND_10Y", "percent", TOSS, None),
    ("S&P 500", "^GSPC", "index", YAHOO, "^GSPC"),
    ("NASDAQ", "^IXIC", "index", YAHOO, "^IXIC"),
    ("USD/KRW", "KRW=X", "fx", YAHOO, "KRW=X"),
    ("US 10Y", "^TNX", "percent", YAHOO, "^TNX"),
    ("VIX", "^VIX", "index", YAHOO, "^VIX"),
]


def _fetch_history(ticker: str, period: str = "3mo") -> list[tuple[str, float]]:
    import yfinance as yf  # 지연 임포트 — 테스트는 fetch 를 주입한다

    frame = yf.Ticker(ticker).history(period=period)
    return [(idx.strftime("%Y-%m-%d"), float(row["Close"])) for idx, row in frame.iterrows()]


def _fetch_toss(symbol: str) -> list[tuple[str, float]]:
    import toss_client

    return toss_client.candles(symbol, count=70)


def build_snapshot(fetch=_fetch_history, toss_fetch=None) -> dict:
    """지표별로 출처에 맞는 fetch 를 호출한다.

    `toss_fetch` 가 None 이면 모든 지표를 `fetch` 로 조회한다 — 자격증명이 없거나
    테스트일 때 파이프라인이 멈추지 않게 하기 위한 폴백이다.
    """
    indicators = []
    for name, key, unit, source, fallback in INDICATORS:
        use_toss = source == TOSS and toss_fetch is not None
        if not use_toss and fallback is None:
            # 토스에만 있는 지표다. 야후 티커가 없으니 넣어봐야 에러 항목만 남는다.
            continue
        ticker = key if use_toss else fallback
        entry = {"name": name, "ticker": ticker, "unit": unit,
                 "source": TOSS if use_toss else YAHOO}
        try:
            history = (toss_fetch if use_toss else fetch)(ticker)
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


def brief_of(snapshot: dict) -> dict:
    """시계열을 뺀 요약본. 에이전트는 이 파일만 읽으면 된다."""
    return {
        "fetched_at": snapshot.get("fetched_at"),
        "indicators": [{k: v for k, v in ind.items() if k != "history"}
                       for ind in snapshot.get("indicators", [])],
    }


def anomalies(indicator: dict, threshold_pct: float = 5.0) -> dict:
    """시계열의 일간 변동 이상치를 센다 — 스파크라인을 그릴지 판단하는 근거."""
    history = [h["close"] for h in (indicator.get("history") or []) if h.get("close")]
    if len(history) < 2:
        return {"max_jump_pct": None, "over_threshold": 0, "days": 0}
    jumps = [abs(history[i + 1] / history[i] - 1) * 100
             for i in range(len(history) - 1) if history[i]]
    return {"max_jump_pct": round(max(jumps), 1),
            "over_threshold": sum(1 for j in jumps if j > threshold_pct),
            "days": len(jumps)}


_TTL_MINUTES = 30


def is_fresh(snapshot: dict, now: datetime, ttl_minutes: int = _TTL_MINUTES) -> bool:
    """같은 날 /brief 를 여러 번 불러도 시세를 매번 다시 받지 않게 한다."""
    stamp = snapshot.get("fetched_at")
    if not stamp:
        return False
    try:
        fetched = datetime.fromisoformat(stamp)
        return (now - fetched) < timedelta(minutes=ttl_minutes)
    except (ValueError, TypeError):
        # 파싱 불가(ValueError)뿐 아니라 tz 없는 값과의 뺄셈(TypeError)도 잡는다.
        # 캐시가 이상하면 크래시가 아니라 다시 받아오는 쪽이 맞다.
        return False


def _write(snapshot: dict) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    _BRIEF_PATH.write_text(json.dumps(brief_of(snapshot), ensure_ascii=False, indent=2),
                           encoding="utf-8")


def main() -> None:
    now = datetime.now(timezone.utc)
    if _OUT_PATH.exists():
        try:
            cached = json.loads(_OUT_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cached = {}
        if is_fresh(cached, now):
            if not _BRIEF_PATH.exists():
                _write(cached)
            ok = [i for i in cached["indicators"] if "error" not in i]
            print(f"캐시 사용: {_OUT_PATH} ({len(ok)}개 지표, {_TTL_MINUTES}분 이내)")
            return

    toss_fetch = None
    try:
        import toss_client
        if toss_client.available():
            toss_fetch = _fetch_toss
        else:
            print("경고: TOSS_CLIENT_ID·TOSS_CLIENT_SECRET 미설정 — 코스피·코스닥을 야후로 대체합니다.")
    except ImportError:
        print("경고: toss_client 를 불러오지 못했습니다 — 전 지표를 야후로 조회합니다.")

    snapshot = build_snapshot(toss_fetch=toss_fetch)
    _write(snapshot)
    ok = [i for i in snapshot["indicators"] if "error" not in i]
    print(f"완료: {_OUT_PATH} ({len(ok)}/{len(snapshot['indicators'])}개 지표 수집)")
    for item in snapshot["indicators"]:
        if "error" in item:
            print(f"  실패: {item['name']} ({item['ticker']}, {item['source']}) — {item['error']}")
            continue
        stat = anomalies(item)
        # 50% 초과는 장세가 아니라 데이터 오류다. 그 아래는 변동성일 뿐이라 표시만 한다.
        flag = "  ← 데이터 오류 의심" if (stat["max_jump_pct"] or 0) > 50 else ""
        print(f"  {item['name']:9} {item['source']:6} {item['last']:>12,.2f}  "
              f"as_of={item['as_of']}  일간최대변동={stat['max_jump_pct']}%{flag}")


if __name__ == "__main__":
    main()
