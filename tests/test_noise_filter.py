from dataclasses import dataclass

import build_index
import noise_filter


@dataclass
class M:
    channel: str
    text: str


def _sim(a: str, b: str) -> float:
    """테스트용 유사도 — 정규화 후 완전히 같으면 1.0, 아니면 0.0."""
    norm = lambda s: "".join(ch for ch in s if ch.isalnum())
    return 1.0 if norm(a) == norm(b) else 0.0


def test_low_signal_short_text_without_url_or_digit():
    assert noise_filter.is_low_signal("??????") is True


def test_low_signal_spares_short_headline_with_enough_content():
    assert noise_filter.is_low_signal("위켄드 오일 상승중") is False
    assert noise_filter.is_low_signal("코스피 상승 마감") is False
    assert noise_filter.is_low_signal("삼성전자 신고가") is False


def test_low_signal_spares_short_text_carrying_a_number():
    assert noise_filter.is_low_signal("코스피 6687 마감") is False


def test_low_signal_spares_short_text_carrying_a_url():
    assert noise_filter.is_low_signal("속보 https://a.com/x") is False


def test_low_signal_spares_long_text():
    assert noise_filter.is_low_signal("가" * 200) is False


def test_advertorial_matches_brand_comma_one_liner():
    assert noise_filter.is_advertorial("무아스, 감성·편의성 다 갖춘 프리미엄 생활용품") is True
    assert noise_filter.is_advertorial("케어센스, 5분마다 혈당 재고 앱 전송…114개국 진출") is True


def test_advertorial_spares_multiline_body():
    assert noise_filter.is_advertorial("무아스, 감성·편의성 다 갖춘 제품\n두 번째 줄") is False


def test_advertorial_spares_long_one_liner():
    assert noise_filter.is_advertorial("무아스, " + "가" * 200) is False


def test_advertorial_spares_headline_without_brand_comma():
    assert noise_filter.is_advertorial("수출 작년 실적 벌써 넘었다…사상 첫 '1조 달러' 코앞") is False


def test_annotate_flags_advertorial_only_in_optin_channel():
    msgs = [M("한국경제", "무아스, 감성·편의성 다 갖춘 프리미엄 생활용품"),
            M("Polaristimes", "무아스, 감성·편의성 다 갖춘 프리미엄 생활용품")]
    got = noise_filter.annotate(msgs, _sim, {"한국경제"})
    assert got == ["advertorial", None]


def test_annotate_flags_near_duplicate_within_channel_keeping_first():
    msgs = [M("A", "수출 작년 실적 벌써 넘었다 사상 첫 1조 달러 코앞"),
            M("A", "수출 작년 실적 벌써 넘었다 사상 첫 1조 달러 코앞!")]
    got = noise_filter.annotate(msgs, _sim, set())
    assert got == [None, "near_duplicate"]


def test_annotate_does_not_flag_duplicate_across_channels():
    msgs = [M("A", "같은 내용을 담은 충분히 긴 문장입니다 여기 숫자 1 있음"),
            M("B", "같은 내용을 담은 충분히 긴 문장입니다 여기 숫자 1 있음")]
    assert noise_filter.annotate(msgs, _sim, set()) == [None, None]


def test_annotate_uses_already_suppressed_message_as_near_duplicate_anchor():
    """이미 advertorial 로 주석된 메시지도 근사중복 판단의 기준으로 살아있어야 한다.

    두 번째 메시지는 줄바꿈이 섞여 있어 그 자체로는 advertorial 로 안 걸리지만,
    (기호를 제외하면) 첫 메시지와 내용이 같으므로 첫 메시지를 기준으로 근사중복
    으로 잡혀야 한다.
    """
    msgs = [M("A", "무아스, 감성 편의성 다 갖춘 제품"),
            M("A", "무아스, 감성 편의성 다 갖춘 제품\n!!!")]
    got = noise_filter.annotate(msgs, _sim, {"A"})
    assert got == ["advertorial", "near_duplicate"]


def test_annotate_flags_near_duplicate_via_real_similarity():
    msgs = [M("A", "수출 작년 실적 벌써 넘었다 사상 첫 1조 달러 코앞"),
            M("A", "수출 작년 실적 벌써 넘었다 사상 첫 1조 달러 코앞!")]
    got = noise_filter.annotate(msgs, build_index.similarity, set())
    assert got == [None, "near_duplicate"]


def test_annotate_real_similarity_does_not_flag_distinct_topics():
    msgs = [M("A", "코스피 6687 마감 상승세 지속"),
            M("A", "삼성전자 신고가 경신 반도체 훈풍")]
    got = noise_filter.annotate(msgs, build_index.similarity, set())
    assert got == [None, None]


def test_count_by_reason_ignores_none():
    assert noise_filter.count_by_reason(["advertorial", None, "advertorial", "low_signal"]) == {
        "advertorial": 2, "low_signal": 1}


def test_advertorial_known_false_positive_is_documented():
    """알려진 오탐 — 같은 형태의 진짜 뉴스도 걸린다.

    협찬성 규칙을 채널 지정 opt-in 으로 가둔 이유이자, 필터를 삭제가 아니라
    주석으로 만든 이유다. 이 동작이 조용히 바뀌면 이 테스트가 알려준다.
    """
    assert noise_filter.is_advertorial("우리銀, 아시안게임 승리 기원...연 7.5% 적금 출시") is True


def test_advertorial_ignores_trailing_url_line():
    """실제 언론사 메시지는 `헤드라인\n기사URL` 두 줄이다.

    이 줄을 본문 줄로 세면 협찬성 규칙이 한 번도 동작하지 않는다 —
    실측에서 한국경제 113건이 전부 이 형태였고 적발 건수가 0이었다.
    """
    text = ("무아스, 감성·편의성 다 갖춘 프리미엄 생활용품\n"
            "https://www.hankyung.com/article/2026090664431")
    assert noise_filter.is_advertorial(text) is True


def test_advertorial_still_spares_genuine_multiline_body():
    text = "무아스, 감성·편의성 다 갖춘 제품\n두 번째 줄입니다"
    assert noise_filter.is_advertorial(text) is False


def test_advertorial_length_cap_survives_url_stripping():
    text = "무아스, " + "가" * 200 + "\nhttps://www.hankyung.com/article/1"
    assert noise_filter.is_advertorial(text) is False
