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


def test_slice_output_reparses_to_the_same_messages():
    """슬라이스가 다시 파싱 가능한 다이제스트 형식이어야 한다.

    섹션 작성 에이전트가 읽는 파일이므로 원문과 같은 규칙(채널 헤더, 타임스탬프
    접두, 본문 헤딩 이스케이프)을 지켜야 한다.
    """
    messages = build_index.parse_digest(_DIGEST)
    out = extract_sections.slice_for_topic(messages, [{"channel": "A", "index": 0},
                                                      {"channel": "B", "index": 2}])
    again = build_index.parse_digest(out)
    assert [(m.channel, m.time, m.text) for m in again] == [
        ("A", "08:00", "첫 번째 메시지"), ("B", "10:00", "세 번째 메시지")]


def test_purge_stale_removes_files_not_in_todays_topics(tmp_path):
    """지난 실행의 슬라이스가 남으면 검수 에이전트가 없는 누락을 보고한다."""
    (tmp_path / "t1.md").write_text("오늘", encoding="utf-8")
    (tmp_path / "t7.md").write_text("어제 잔여물", encoding="utf-8")
    removed = extract_sections.purge_stale([tmp_path], {"t1"}, ".md")
    assert [r.split("/")[-1] for r in removed] == ["t7.md"]
    assert (tmp_path / "t1.md").exists()
    assert not (tmp_path / "t7.md").exists()


def test_purge_stale_ignores_other_suffixes(tmp_path):
    (tmp_path / "notes.txt").write_text("건드리지 말 것", encoding="utf-8")
    assert extract_sections.purge_stale([tmp_path], {"t1"}, ".md") == []
    assert (tmp_path / "notes.txt").exists()


def test_purge_stale_tolerates_missing_directory(tmp_path):
    assert extract_sections.purge_stale([tmp_path / "없음"], {"t1"}, ".md") == []


def test_slice_escapes_heading_line_in_body():
    digest = ("# 텔레그램 다이제스트 — 2026-09-06 정오 기준\n\n"
              "## A\n- (08:00) 헤드라인\n ## 소제목\n본문 계속\n")
    messages = build_index.parse_digest(digest)
    out = extract_sections.slice_for_topic(messages, [{"channel": "A", "index": 0}])
    again = build_index.parse_digest(out)
    assert len(again) == 1
    assert "소제목" in again[0].text
    assert "본문 계속" in again[0].text
