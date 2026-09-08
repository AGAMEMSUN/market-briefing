import build_payload


def _snapshot(closes, name="KOSPI", source="toss"):
    return {"indicators": [{
        "name": name, "unit": "index", "source": source, "last": closes[-1],
        "change_pct": 0.5, "as_of": "2026-09-08",
        "history": [{"date": f"d{i}", "close": c} for i, c in enumerate(closes)],
    }]}


def test_spark_is_kept_for_a_clean_series():
    payload = build_payload.strip_indicators(_snapshot([100, 101, 102, 103]))
    assert payload[0]["spark"] == [100, 101, 102, 103]
    assert payload[0]["note"] is None


def test_volatile_but_real_series_keeps_its_spark():
    """급변동은 오류가 아니다.

    토스(KRX)와 야후를 65일 대조했더니 값이 다른 날은 당일 하나뿐이었다 — 코스피의
    일간 17.9% 변동은 실제 장세였다. 변동성을 이유로 차트를 빼고 '비정상 변동'이라
    적으면 독자에게 틀린 말을 하는 것이다.
    """
    volatile = [5593, 6595, 6257, 6358, 6598, 6296]  # 실제 코스피 구간(최대 +17.9%)
    ind = build_payload.strip_indicators(_snapshot(volatile))[0]
    assert ind["spark"] == volatile
    assert ind["note"] is None


def test_spark_is_dropped_for_a_physically_impossible_jump():
    ind = build_payload.strip_indicators(_snapshot([100, 100, 100, 900]))[0]
    assert ind["spark"] is None
    assert "데이터 오류" in ind["note"]
    assert ind["last"] == 900  # 값 자체는 남는다


def test_spark_is_dropped_for_non_positive_closes():
    ind = build_payload.strip_indicators(_snapshot([100, 0, 100]))[0]
    assert ind["spark"] is None
    assert "0 이하" in ind["note"]


def test_vix_volatility_is_not_treated_as_an_error():
    wild = [15, 30, 15, 30, 15, 30]  # 일간 100% 변동이지만 50% 규칙엔 걸린다
    assert build_payload.strip_indicators(_snapshot(wild, name="VIX"))[0]["spark"] is None
    normal = [15.3, 14.5, 16.3, 15.2, 14.3]
    assert build_payload.strip_indicators(_snapshot(normal, name="VIX"))[0]["spark"] is not None


def test_failed_indicator_is_omitted():
    snap = {"indicators": [{"name": "KOSPI", "error": "network down"}]}
    assert build_payload.strip_indicators(snap) == []


def test_topic_uses_merged_from_the_section_writer():
    section = {
        "topic_id": "t1", "title": "제목", "primary_channel": "A",
        "merged": [{"channel": "B", "what": "유가 급등 건"}],
        "organized": "본문", "summary": "요약", "draft_insight": "초안",
        "indicators": [{"label": "VIX", "value": "15.30", "change": "+5.3%",
                        "source": "market_snapshot"}],
    }
    out = build_payload.topic_payload(section)
    assert out["merged"] == [{"channel": "B", "what": "유가 급등 건"}]
    assert out["indicators"] == [{"label": "VIX", "value": "15.30", "change": "+5.3%"}]


def test_topic_falls_back_to_merged_channels_for_older_sections():
    section = {"topic_id": "t1", "title": "제목", "merged_channels": ["B", "C"]}
    out = build_payload.topic_payload(section)
    assert [m["channel"] for m in out["merged"]] == ["B", "C"]


def test_verification_notes_never_reach_the_payload():
    """대시보드에 안 쓰이는데 payload 에 실으면 렌더·발행 비용만 커진다."""
    section = {"topic_id": "t1", "title": "제목",
               "verification_notes": "검증노트_제외대상", "merged_channels": []}
    assert "검증노트_제외대상" not in str(build_payload.topic_payload(section))


def test_build_summarises_noise_counts_into_a_note():
    index = {"date": "2026-09-08", "channels": {"A": {"messages": [{}, {}]}},
             "noise_counts": {"advertorial": 20, "low_signal": 2, "near_duplicate": 0},
             "quiet_channels": ["B"]}
    out = build_payload.build([], [], {"indicators": []}, index)
    assert out["message_count"] == 2
    assert out["channel_count"] == 1
    assert "협찬성 기사 20건" in out["excluded_note"]
    assert "저신호 단문 2건" in out["excluded_note"]
    assert "근사중복" not in out["excluded_note"]  # 0 건은 적지 않는다
    assert out["quiet_channels"] == ["B"]


def test_build_note_is_empty_when_nothing_was_filtered():
    index = {"channels": {}, "noise_counts": {}}
    assert build_payload.build([], [], {"indicators": []}, index)["excluded_note"] == ""
