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


def test_find_duplicate_groups_merges_repeated_url_from_same_channel():
    url = "https://n.news.naver.com/article/001/0016291975"
    messages = [
        build_index.Message(channel="A", time="01:00", text=f"속보 {url}"),
        build_index.Message(channel="A", time="01:05", text=f"재전송 {url}"),
        build_index.Message(channel="B", time="02:00", text=f"인용 {url}"),
    ]
    groups = build_index.find_duplicate_groups(messages)
    assert len(groups) == 1
    assert set(groups[0]["members"]) == {0, 1, 2}
    assert groups[0]["reason"] == "shared_url"


def test_build_index_groups_messages_by_channel():
    manifest = {"collected": ["미국 주식 인사이더", "급등일보 미국주식", "하나증권 금융팀", "Polaristimes"],
                "excluded": ["KB시황 하인환"]}
    index = build_index.build_index(_sample(), manifest, "2026-09-06")
    assert index["date"] == "2026-09-06"
    assert index["channels"]["미국 주식 인사이더"]["message_count"] == 2
    assert index["channels"]["하나증권 금융팀"]["message_count"] == 1


def test_build_index_marks_duplicate_group_on_messages():
    manifest = {"collected": [], "excluded": []}
    index = build_index.build_index(_sample(), manifest, "2026-09-06")
    first = index["channels"]["미국 주식 인사이더"]["messages"][0]
    second = index["channels"]["급등일보 미국주식"]["messages"][0]
    assert first["duplicate_group"] is not None
    assert first["duplicate_group"] == second["duplicate_group"]


def test_build_index_separates_quiet_and_excluded_channels():
    manifest = {"collected": ["미국 주식 인사이더", "Polaristimes"],
                "excluded": ["KB시황 하인환"]}
    index = build_index.build_index(_sample(), manifest, "2026-09-06")
    assert index["quiet_channels"] == ["Polaristimes"]
    assert index["excluded_channels"] == ["KB시황 하인환"]


def test_build_index_headline_is_truncated():
    manifest = {"collected": [], "excluded": []}
    index = build_index.build_index(_sample(), manifest, "2026-09-06")
    for channel in index["channels"].values():
        for message in channel["messages"]:
            assert len(message["headline"]) <= 120


def test_build_index_headline_truncates_long_line_to_70_chars():
    long_line = "".join(str(i % 10) for i in range(200))
    digest_text = f"# 텔레그램 다이제스트 — 2026-09-06 정오 기준\n\n## 테스트채널\n- (10:00) {long_line}\n"
    manifest = {"collected": [], "excluded": []}
    index = build_index.build_index(digest_text, manifest, "2026-09-06")
    headline = index["channels"]["테스트채널"]["messages"][0]["headline"]
    assert len(headline) == 70
    assert long_line.startswith(headline)


def test_parse_digest_ignores_hash_line_inside_message_body():
    """본문 속 `## 소제목` 을 채널 헤더로 오인하면 그 뒤 본문이 통째로 사라진다."""
    digest = (
        "# 텔레그램 다이제스트 — 2026-09-06 정오 기준\n\n"
        "## ChanA\n"
        "- (09:00) 헤드라인\n## 소제목\n본문 계속\n\n"
        "## ChanB\n"
        "- (10:00) 다른 채널 소식\n"
    )
    messages = build_index.parse_digest(digest)
    assert [m.channel for m in messages] == ["ChanA", "ChanB"]
    assert messages[0].text == "헤드라인\n## 소제목\n본문 계속"


def test_merge_digest_round_trip_keeps_hash_line_in_body():
    collected = {"ChanA": ["- (09:00) 헤드라인\n## 소제목\n본문 계속"]}
    merged = build_index.merge_digest(None, collected, "2026-09-06")
    messages = build_index.parse_digest(merged)
    assert len(messages) == 1
    assert messages[0].channel == "ChanA"
    # 쓰는 쪽에서 헤딩 줄 앞에 공백 한 칸을 붙이므로 내용은 남고 모양만 안전해진다.
    assert messages[0].text == "헤드라인\n ## 소제목\n본문 계속"
    # 한 번 더 돌려도 그대로여야 한다 (같은 날 재실행 = 기존 파일 + 새 수집분)
    again = build_index.merge_digest(merged, {}, "2026-09-06")
    assert again == merged
    assert [(m.channel, m.text) for m in build_index.parse_digest(again)] == [
        ("ChanA", "헤드라인\n ## 소제목\n본문 계속")
    ]


def test_merge_digest_round_trip_still_detects_second_channel():
    collected = {
        "ChanA": ["- (09:00) 헤드라인\n## 소제목\n본문 계속"],
        "ChanB": ["- (10:00) 다른 채널 소식"],
    }
    merged = build_index.merge_digest(None, collected, "2026-09-06")
    messages = build_index.parse_digest(merged)
    assert [m.channel for m in messages] == ["ChanA", "ChanB"]
    assert messages[1].text == "다른 채널 소식"


def test_extract_urls_excludes_korean_closing_brackets():
    assert build_index.extract_urls("「https://a.com/x」 참고") == ["https://a.com/x"]


def test_extract_urls_excludes_other_cjk_closers():
    text = "『https://a.com/1』 《https://a.com/2》 （https://a.com/3）"
    assert build_index.extract_urls(text) == [
        "https://a.com/1", "https://a.com/2", "https://a.com/3",
    ]


def test_build_index_keeps_not_checked_channels_out_of_quiet_channels():
    manifest = {
        "collected": ["미국 주식 인사이더", "Polaristimes", "KB시황 하인환"],
        "excluded": [],
        "not_checked": ["KB시황 하인환"],
    }
    index = build_index.build_index(_sample(), manifest, "2026-09-06")
    assert index["not_checked_channels"] == ["KB시황 하인환"]
    assert index["quiet_channels"] == ["Polaristimes"]


def test_build_index_not_checked_defaults_to_empty_for_old_manifest():
    manifest = {"collected": ["Polaristimes"], "excluded": []}
    index = build_index.build_index(_sample(), manifest, "2026-09-06")
    assert index["not_checked_channels"] == []
    assert index["quiet_channels"] == ["Polaristimes"]


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
