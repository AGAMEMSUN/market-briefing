"""다이제스트 노이즈 주석.

LLM 이 보기 전에 파이썬이 확실한 노이즈를 표시한다. 실측상 한국경제 채널 98건 중
63건이 박람회 협찬성 제품 기사로 전체 코퍼스의 37% 였다.

**삭제가 아니라 주석이다.** 전체 인덱스는 모든 메시지를 보관하고, 오케스트레이터가
읽는 경량 뷰에서만 주석된 항목이 빠진다. 오탐이 나도 정보가 사라지지 않는다.

협찬성 규칙은 채널 지정 opt-in 이다. 전역 적용하면 같은 형태의 진짜 뉴스
(`우리銀, 아시안게임 승리 기원...연 7.5% 적금 출시`)를 걸러버린다.
"""
from __future__ import annotations

import re

_URL_RE = re.compile(r"https?://")
_DIGIT_RE = re.compile(r"\d")
# 브랜드명(공백·쉼표 없는 1~8자) + 쉼표 + 공백으로 시작하는 한 줄 헤드라인
_ADVERTORIAL_RE = re.compile(r"^[^\s,]{1,8}, \S")
# 줄 전체가 URL 하나뿐인 줄. 기사 링크가 본문 아래 줄에 붙는 형태를 걸러내기 위한 것.
_URL_ONLY_LINE_RE = re.compile(r"^https?://\S+$")

_LOW_SIGNAL_MIN_CONTENT_CHARS = 5
_ADVERTORIAL_MAX_CHARS = 120
_NEAR_DUPLICATE_THRESHOLD = 0.85


def is_low_signal(text: str) -> bool:
    """URL·숫자도 없고 실질 내용도 거의 없는 단문. `??????` 같은 것.

    길이가 아니라 내용 유무로 판단한다 — 공백·기호를 걷어내고 한글·영숫자
    (`ch.isalnum()`)만 셌을 때 5자 미만이면 저신호로 본다. `코스피 상승 마감`,
    `삼성전자 신고가`처럼 짧아도 실질 내용이 있는 헤드라인은 이 기준으로는
    저신호가 아니다 — 대신 남기는 쪽을 택했다(정밀도 우선).
    숫자나 URL 을 담은 메시지는 길이·내용량과 무관하게 저신호가 아니다.
    """
    stripped = text.strip()
    if _URL_RE.search(stripped) or _DIGIT_RE.search(stripped):
        return False
    content_chars = sum(1 for ch in stripped if ch.isalnum())
    return content_chars < _LOW_SIGNAL_MIN_CONTENT_CHARS


def _strip_url_only_lines(text: str) -> str:
    """줄 전체가 URL 뿐인 줄을 걷어낸다.

    실제 언론사 채널 메시지는 `헤드라인\\n기사URL` 두 줄 구조다. 이걸 그대로
    두면 "한 줄 헤드라인" 조건이 영원히 거짓이 되어 협찬성 규칙이 한 번도
    동작하지 않는다 — 실측에서 한국경제 113건이 전부 여러 줄이었다.
    """
    lines = [line for line in text.strip().splitlines()
             if not _URL_ONLY_LINE_RE.match(line.strip())]
    return "\n".join(lines).strip()


def is_advertorial(text: str) -> bool:
    """`브랜드명, 제품 설명` 형태의 한 줄 협찬성 기사.

    기사 링크만 있는 줄은 본문 줄로 세지 않는다. 그 줄을 걷어낸 뒤에도 여러
    줄이 남으면 협찬성 기사가 아니다.
    """
    stripped = _strip_url_only_lines(text)
    if "\n" in stripped:
        return False
    if len(stripped) >= _ADVERTORIAL_MAX_CHARS:
        return False
    return bool(_ADVERTORIAL_RE.match(stripped))


def annotate(messages: list, similarity_fn, advertorial_channels: set[str]) -> list[str | None]:
    """메시지마다 노이즈 사유를 붙인다. 사유가 없으면 None.

    근사중복은 같은 채널 안에서만 본다 — 채널 간 중복은 병합의 근거이지
    노이즈가 아니다. 먼저 등장한 쪽을 남기고 뒤에 온 쪽을 표시한다.

    이미 low_signal/advertorial 로 주석된 메시지도 근사중복 판단의 기준(anchor)
    으로는 계속 쓰인다 — 그 메시지 자체의 반복을 잡아내야 하기 때문이다. 다만
    이미 near_duplicate 로 주석된 메시지는 기준에서 제외한다(연쇄가 첫 항목으로
    수렴하도록). 판단은 항상 "더 앞의 메시지와의 쌍"으로만 이루어지므로, 유사도
    함수가 추이적(transitive)이지 않으면 — A≈B, B≈C 이지만 A≈C 로는 안 잡히는
    경우 — 긴 연쇄의 뒤쪽 끝은 여전히 놓칠 수 있다.
    """
    reasons: list[str | None] = [None] * len(messages)

    for i, message in enumerate(messages):
        if is_low_signal(message.text):
            reasons[i] = "low_signal"
        elif message.channel in advertorial_channels and is_advertorial(message.text):
            reasons[i] = "advertorial"

    for i, message in enumerate(messages):
        if reasons[i] is not None:
            continue
        for j in range(i):
            if reasons[j] == "near_duplicate":
                continue
            if messages[j].channel != message.channel:
                continue
            if similarity_fn(messages[j].text, message.text) >= _NEAR_DUPLICATE_THRESHOLD:
                reasons[i] = "near_duplicate"
                break

    return reasons


def count_by_reason(annotations: list[str | None]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for reason in annotations:
        if reason is not None:
            counts[reason] = counts.get(reason, 0) + 1
    return counts
