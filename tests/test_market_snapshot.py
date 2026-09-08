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


def _toss_fetch(symbol):
    return [("2026-09-04", 7000.0), ("2026-09-05", 7050.0)]


def test_build_snapshot_covers_all_indicators():
    snap = market_snapshot.build_snapshot(fetch=_fake_fetch, toss_fetch=_toss_fetch)
    names = [i["name"] for i in snap["indicators"]]
    assert names == ["KOSPI", "KOSDAQ", "KR 10Y", "S&P 500", "NASDAQ",
                     "USD/KRW", "US 10Y", "VIX"]


def test_toss_only_indicator_is_skipped_without_credentials():
    """국고채 10Y 는 야후 티커가 없다 — 폴백에서 에러 항목을 만들지 말고 빼야 한다."""
    snap = market_snapshot.build_snapshot(fetch=_fake_fetch, toss_fetch=None)
    assert "KR 10Y" not in [i["name"] for i in snap["indicators"]]


def test_fallback_uses_the_yahoo_ticker_not_the_toss_symbol():
    seen = []

    def spy(ticker):
        seen.append(ticker)
        return _fake_fetch(ticker)

    market_snapshot.build_snapshot(fetch=spy, toss_fetch=None)
    assert "^KS11" in seen and "KOSPI" not in seen


def test_toss_indicators_route_to_toss_fetch_when_available():
    def toss(symbol):
        return [("2026-09-04", 7000.0), ("2026-09-05", 7050.0)]

    snap = market_snapshot.build_snapshot(fetch=_fake_fetch, toss_fetch=toss)
    by_name = {i["name"]: i for i in snap["indicators"]}
    assert by_name["KOSPI"]["source"] == "toss"
    assert by_name["KOSPI"]["last"] == 7050.0
    assert by_name["VIX"]["source"] == "yahoo"
    assert by_name["VIX"]["last"] == 110.0


def test_falls_back_to_yahoo_when_toss_unavailable():
    """자격증명이 없으면 파이프라인이 멈추는 대신 야후로 조회한다."""
    snap = market_snapshot.build_snapshot(fetch=_fake_fetch, toss_fetch=None)
    kospi = next(i for i in snap["indicators"] if i["name"] == "KOSPI")
    assert kospi["source"] == "yahoo"
    assert kospi["last"] == 110.0


def test_toss_failure_falls_through_to_error_not_crash():
    def boom(symbol):
        raise RuntimeError("token expired")

    snap = market_snapshot.build_snapshot(fetch=_fake_fetch, toss_fetch=boom)
    kospi = next(i for i in snap["indicators"] if i["name"] == "KOSPI")
    assert "error" in kospi
    vix = next(i for i in snap["indicators"] if i["name"] == "VIX")
    assert vix["last"] == 110.0


def test_brief_drops_history_but_keeps_values():
    """에이전트가 읽는 요약본에서 시계열이 빠져야 한다 — 호출당 8K 토큰 절감."""
    snap = market_snapshot.build_snapshot(fetch=_fake_fetch)
    brief = market_snapshot.brief_of(snap)
    assert all("history" not in i for i in brief["indicators"])
    kospi = next(i for i in brief["indicators"] if i["name"] == "KOSPI")
    assert kospi["last"] == 110.0 and kospi["as_of"] == "2026-09-05"


def test_anomalies_counts_daily_jumps():
    ind = {"history": [{"date": "d1", "close": 100.0}, {"date": "d2", "close": 120.0},
                       {"date": "d3", "close": 121.0}]}
    stat = market_snapshot.anomalies(ind)
    assert stat["days"] == 2
    assert stat["over_threshold"] == 1  # 100->120 만 5% 초과
    assert stat["max_jump_pct"] == 20.0


def test_anomalies_handles_short_history():
    assert market_snapshot.anomalies({"history": []})["over_threshold"] == 0


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


def _now():
    from datetime import datetime, timezone
    return datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def test_is_fresh_within_ttl():
    from datetime import timedelta
    snap = {"fetched_at": (_now() - timedelta(minutes=10)).isoformat()}
    assert market_snapshot.is_fresh(snap, _now()) is True


def test_is_stale_past_ttl():
    from datetime import timedelta
    snap = {"fetched_at": (_now() - timedelta(minutes=45)).isoformat()}
    assert market_snapshot.is_fresh(snap, _now()) is False


def test_is_not_fresh_without_timestamp():
    assert market_snapshot.is_fresh({}, _now()) is False


def test_is_not_fresh_with_unparseable_timestamp():
    assert market_snapshot.is_fresh({"fetched_at": "어제"}, _now()) is False


def test_is_not_fresh_with_naive_timestamp():
    """tz 없는 타임스탬프가 들어와도 크래시하지 않고 다시 받아온다.

    aware - naive 뺄셈은 TypeError 를 던진다. 가드의 목적이 복원력이므로
    파싱 실패와 같이 취급해야 한다.
    """
    assert market_snapshot.is_fresh({"fetched_at": "2026-09-06T12:00:00"}, _now()) is False
