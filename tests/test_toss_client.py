import json

import pytest

import toss_client


def test_credentials_needs_both_values():
    assert toss_client.credentials({"TOSS_CLIENT_ID": "a", "TOSS_CLIENT_SECRET": "b"}) == ("a", "b")
    assert toss_client.credentials({"TOSS_CLIENT_ID": "a", "TOSS_CLIENT_SECRET": ""}) is None
    assert toss_client.credentials({"TOSS_CLIENT_SECRET": "b"}) is None


def test_load_env_skips_comments_and_blank_lines(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# 주석\n\nA=1\nB=값 = 포함\n잘못된줄\n", encoding="utf-8")
    env = toss_client.load_env(path)
    assert env == {"A": "1", "B": "값 = 포함"}


def test_candles_rejects_symbols_outside_the_catalogue():
    """카탈로그에 없는 심볼은 서버에 묻기 전에 막는다 — 400 을 왕복할 이유가 없다."""
    with pytest.raises(toss_client.TossError, match="심볼 카탈로그"):
        toss_client.candles("005930", token="fake")


def test_candles_normalises_to_oldest_first_date_close_pairs(monkeypatch):
    monkeypatch.setattr(toss_client, "_auth_get", lambda *a, **k: {"candles": [
        {"timestamp": "2026-09-08T09:00:00+09:00", "closePrice": "7046.74"},
        {"timestamp": "2026-09-07T09:00:00+09:00", "closePrice": "6995.39"},
    ]})
    assert toss_client.candles("KOSPI", token="fake") == [
        ("2026-09-07", 6995.39), ("2026-09-08", 7046.74)]


def test_candles_skips_rows_missing_a_close(monkeypatch):
    monkeypatch.setattr(toss_client, "_auth_get", lambda *a, **k: {"candles": [
        {"timestamp": "2026-09-08T09:00:00+09:00", "closePrice": None},
        {"timestamp": "2026-09-07T09:00:00+09:00", "closePrice": "6995.39"},
    ]})
    assert toss_client.candles("KOSPI", token="fake") == [("2026-09-07", 6995.39)]


def test_token_cache_is_reused_before_expiry(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    cache = tmp_path / "toss_token.json"
    cache.write_text(json.dumps({
        "access_token": "cached", "expires_at": (now + timedelta(minutes=5)).isoformat()}),
        encoding="utf-8")

    def boom(*a, **k):
        raise AssertionError("만료 전인데 재발급을 시도했다")

    monkeypatch.setattr(toss_client, "fetch_token", boom)
    assert toss_client.get_token(now=now, cache_path=cache) == "cached"


def test_expired_token_triggers_reissue(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    cache = tmp_path / "toss_token.json"
    cache.write_text(json.dumps({
        "access_token": "stale", "expires_at": (now - timedelta(minutes=1)).isoformat()}),
        encoding="utf-8")
    monkeypatch.setattr(toss_client, "credentials", lambda: ("id", "secret"))
    monkeypatch.setattr(toss_client, "fetch_token",
                        lambda *a: {"access_token": "fresh", "expires_in": 600})
    assert toss_client.get_token(now=now, cache_path=cache) == "fresh"


def test_corrupt_token_cache_is_ignored(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    cache = tmp_path / "toss_token.json"
    cache.write_text("{망가진 json", encoding="utf-8")
    monkeypatch.setattr(toss_client, "credentials", lambda: ("id", "secret"))
    monkeypatch.setattr(toss_client, "fetch_token",
                        lambda *a: {"access_token": "fresh", "expires_in": 600})
    assert toss_client.get_token(
        now=datetime(2026, 9, 8, tzinfo=timezone.utc), cache_path=cache) == "fresh"


def test_envelope_without_result_is_an_error(monkeypatch):
    monkeypatch.setattr(toss_client, "_request", lambda *a, **k: {"unexpected": 1})
    with pytest.raises(toss_client.TossError, match="예상 밖 응답"):
        toss_client._auth_get("/api/v1/x", {}, "token")


def test_hint_explains_the_ip_allowlist_failure():
    hint = toss_client._hint('{"error":"access_denied","error_description":"IP address not allowed"}')
    assert "허용 IP" in hint and "ipify" in hint


def test_hint_explains_a_wrong_secret():
    assert "재발급" in toss_client._hint("Client authentication failed: client_secret")


def test_hint_is_empty_for_unknown_errors():
    assert toss_client._hint("HTTP 500: something else") == ""
