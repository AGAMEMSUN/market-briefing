import review_view


def test_numeric_claims_requires_a_unit():
    """연도·순번 같은 맨숫자는 잡지 않는다 — 잡으면 뷰가 잡음으로 찬다."""
    claims = review_view.numeric_claims("2026년 9월 8일 WTI 92.65달러, 금리 4.78%")
    assert claims == {"92.65달러", "4.78%"}


def test_numeric_claims_normalizes_thousands_separator():
    assert review_view.numeric_claims("코스피 7,046.74포인트") == {"7046.74포인트"}
    a = review_view.numeric_claims("7,046.74포인트")
    b = review_view.numeric_claims("7046.74포인트")
    assert a == b


def test_shared_numbers_lists_only_cross_section_values():
    sections = [
        {"topic_id": "t1", "organized": "미 10년물 4.78% 와 WTI 92달러", "summary": "", "draft_insight": ""},
        {"topic_id": "t3", "organized": "금리는 4.78% 수준", "summary": "", "draft_insight": ""},
        {"topic_id": "t4", "organized": "구리 14533달러", "summary": "", "draft_insight": ""},
    ]
    shared = review_view.shared_numbers(sections)
    assert shared == {"4.78%": ["t1", "t3"]}


def test_shared_numbers_scans_summary_and_insight_too():
    sections = [
        {"topic_id": "t1", "organized": "", "summary": "구리 14533달러", "draft_insight": ""},
        {"topic_id": "t4", "organized": "", "summary": "", "draft_insight": "구리 14533달러"},
    ]
    assert "14533달러" in review_view.shared_numbers(sections)


def _index():
    return {"channels": {"A": {"messages": [
        {"index": 0, "headline": "쓰인 것", "noise": None},
        {"index": 1, "headline": "안 쓰인 것", "noise": None},
        {"index": 2, "headline": "광고", "noise": "advertorial"},
    ]}}}


def test_uncovered_lists_unclaimed_non_noise_messages():
    topics = [{"topic_id": "t1", "message_refs": [{"channel": "A", "index": 0}]}]
    assert review_view.uncovered_messages(_index(), topics) == [("A", 1, "안 쓰인 것")]


def test_uncovered_excludes_noise_filtered_messages():
    topics = [{"topic_id": "t1", "message_refs": []}]
    headlines = [h for _, _, h in review_view.uncovered_messages(_index(), topics)]
    assert "광고" not in headlines


def _cluster_index():
    """두 채널이 함께 다룬 '원전' 묶음 + 무관한 단발 기사."""
    return {"channels": {
        "A": {"messages": [
            {"index": 0, "headline": "미국 원전 8기 건설 제안", "noise": None},
            {"index": 1, "headline": "랍스터 뷔페 등장", "noise": None},
        ]},
        "B": {"messages": [
            {"index": 2, "headline": "원전 기자재 수주 기대", "noise": None},
        ]},
        "C": {"messages": [
            {"index": 3, "headline": "원전 관련주 급등", "noise": None},
        ]},
    }}


def test_tokenizer_does_not_split_compounds():
    """형태소 분석 없이는 '원전주'가 '원전'으로 쪼개지지 않는다.

    클러스터 재현율의 알려진 한계다 — 같은 주제라도 표기가 다르면 따로 잡힌다.
    검수 뷰는 후보를 제시할 뿐이고 최종 판단은 에이전트가 하므로 감수한다.
    """
    assert review_view.tokenize("원전주 급등") == {"원전주", "급등"}


def test_missed_clusters_groups_multi_channel_topic():
    topics = [{"topic_id": "t1", "message_refs": []}]
    clusters = dict(review_view.missed_clusters(_cluster_index(), topics))
    assert "원전" in clusters
    assert {idx for _, idx, _ in clusters["원전"]} == {0, 2, 3}


def test_missed_clusters_ignores_single_channel_topic():
    """한 채널만 다룬 주제는 병합 대상이 아니다."""
    index = {"channels": {"A": {"messages": [
        {"index": i, "headline": "원전 소식", "noise": None} for i in range(4)]}}}
    assert review_view.missed_clusters(index, [{"topic_id": "t1", "message_refs": []}]) == []


def test_missed_clusters_drops_tokens_common_in_the_corpus():
    """'글로벌' 처럼 흔한 말로 묶으면 무관한 기사가 한 덩어리가 된다."""
    topics = [{"topic_id": "t1", "message_refs": []}]
    common = {"원전": 99}  # 본문 기준으로는 흔한 말이었다고 가정
    assert review_view.missed_clusters(_cluster_index(), topics, common) == []


def test_missed_clusters_skips_already_covered_messages():
    topics = [{"topic_id": "t1", "message_refs": [
        {"channel": "A", "index": 0}, {"channel": "B", "index": 2}]}]
    assert review_view.missed_clusters(_cluster_index(), topics) == []


def test_document_frequency_counts_documents_not_occurrences():
    df = review_view.document_frequency(["원전 원전 원전", "원전 기자재"])
    assert df["원전"] == 2 and df["기자재"] == 1


def test_tokenize_strips_urls():
    assert review_view.tokenize("https://n.news.naver.com/article/052 원전") == {"원전"}


def test_render_omits_verification_notes_and_sources():
    """검수 뷰에 원문·검증노트·출처가 새어 들어가면 절감 효과가 사라진다."""
    sections = [{
        "topic_id": "t1", "title": "제목", "primary_channel": "채널",
        "merged_channels": [], "indicators": [], "summary": "요약",
        "organized": "정리 본문", "draft_insight": "인사이트",
        "verification_notes": "검증노트는_제외되어야_함",
        "sources": [{"type": "web", "url": "https://example.com/제외대상"}],
    }]
    out = review_view.render(sections, {"channels": {}}, [{"topic_id": "t1", "message_refs": []}])
    assert "검증노트는_제외되어야_함" not in out
    assert "example.com" not in out
    assert "요약" in out and "제목" in out
