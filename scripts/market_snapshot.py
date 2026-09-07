"""고정 시장 지표 스냅샷 — 브리핑 대시보드의 수치 근거.

숫자를 해석하지 않고 조회한 값만 기록한다. yfinance 는 지연 임포트라
테스트와 CI 는 이 패키지 없이도 돌아간다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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


if __name__ == "__main__":
    main()
