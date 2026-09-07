import build_index


def test_merge_digest_returns_new_content_when_no_existing_file():
    collected = {"채널A": ["- (09:00) 첫 소식"]}
    merged = build_index.merge_digest(None, collected, "2026-09-06")
    assert "# 텔레그램 다이제스트 — 2026-09-06 정오 기준" in merged
    messages = build_index.parse_digest(merged)
    assert len(messages) == 1
    assert messages[0].channel == "채널A"
    assert messages[0].text == "첫 소식"


def test_merge_digest_combines_existing_and_new_without_duplicates():
    existing = (
        "# 텔레그램 다이제스트 — 2026-09-06 정오 기준\n\n"
        "## 채널A\n"
        "- (09:00) 첫 소식\n\n"
        "## 채널B\n"
        "- (09:30) 채널B 소식\n\n"
    )
    collected = {
        # 채널A 에는 새 메시지가 추가되고, 이미 있던 09:00 메시지도 다시 들어온다
        # (재실행 시 겹칠 수 있는 경계 케이스) — 중복 없이 합쳐져야 한다.
        "채널A": ["- (09:00) 첫 소식", "- (10:15) 새로 온 소식"],
    }
    merged = build_index.merge_digest(existing, collected, "2026-09-06")
    messages = build_index.parse_digest(merged)

    channel_a = [m for m in messages if m.channel == "채널A"]
    channel_b = [m for m in messages if m.channel == "채널B"]

    assert len(channel_a) == 2
    assert [m.text for m in channel_a] == ["첫 소식", "새로 온 소식"]
    assert channel_a[0].time == "09:00"
    assert channel_a[1].time == "10:15"

    assert len(channel_b) == 1
    assert channel_b[0].text == "채널B 소식"


def test_merge_digest_reports_no_messages_when_both_sides_empty():
    merged = build_index.merge_digest(None, {}, "2026-09-06")
    assert "_새 메시지 없음_" in merged
    assert build_index.parse_digest(merged) == []


# 텔레그램 원문이 "빈 줄 + `## 소제목`" 을 담고 있으면, merge_digest 가 그대로 받아
# 적을 때 진짜 채널 헤더와 똑같은 모양이 만들어진다 — 쓰는 쪽에서 이스케이프한다.
_BODY_WITH_HEADING = "- (09:00) 헤드라인\n\n## 소제목\n본문 계속"


def test_merge_digest_keeps_body_heading_after_blank_line():
    merged = build_index.merge_digest(None, {"ChanA": [_BODY_WITH_HEADING]}, "2026-09-06")
    messages = build_index.parse_digest(merged)
    assert len(messages) == 1
    assert messages[0].channel == "ChanA"
    for fragment in ("헤드라인", "## 소제목", "본문 계속"):
        assert fragment in messages[0].text


def test_merge_digest_escaping_is_idempotent():
    first = build_index.merge_digest(None, {"ChanA": [_BODY_WITH_HEADING]}, "2026-09-06")
    second = build_index.merge_digest(first, {}, "2026-09-06")
    third = build_index.merge_digest(second, {}, "2026-09-06")
    # 이미 이스케이프된 줄은 `^#{1,6} ` 에 걸리지 않아 공백이 덧붙지 않는다.
    assert second == third
    assert build_index.parse_digest(second) == build_index.parse_digest(third)


def test_merge_digest_escape_does_not_break_real_channel_headers():
    collected = {
        "ChanA": [_BODY_WITH_HEADING],
        "ChanB": ["- (10:00) 다른 채널 소식"],
    }
    merged = build_index.merge_digest(None, collected, "2026-09-06")
    messages = build_index.parse_digest(merged)
    assert [m.channel for m in messages] == ["ChanA", "ChanB"]
    assert messages[1].text == "다른 채널 소식"


def test_merge_digest_escapes_all_markdown_heading_levels():
    body = "- (09:00) 헤드라인\n\n# 1단\n\n###### 6단\n마무리"
    merged = build_index.merge_digest(None, {"ChanA": [body]}, "2026-09-06")
    for line in merged.splitlines()[3:]:
        assert not line.startswith("#") or line == "## ChanA"
    text = build_index.parse_digest(merged)[0].text
    for fragment in ("헤드라인", "# 1단", "###### 6단", "마무리"):
        assert fragment in text
