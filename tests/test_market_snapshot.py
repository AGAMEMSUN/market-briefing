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
