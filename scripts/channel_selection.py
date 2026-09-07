"""제외 목록 적용 — 순수 함수.

`channels.EXCLUDE_TITLES` 는 채널 "제목" 문자열이라 개명에 취약하다. 화이트리스트를
없앤 이유가 바로 이 취약성인데, 제외 목록에 그대로 남기면 같은 문제가 다른 자리로
옮겨갈 뿐이다 — 제외 대상 채널이 개명하면 더 이상 어떤 제목과도 일치하지 않아
조용히 다시 수집 대상에 들어간다.

이 함수는 그 상황을 삼키지 않고 `unmatched_exclusions` 로 드러낸다. 호출자
(`telegram_digest.py`)가 이를 로그와 콘솔 출력에 반영해 사람이 알아채게 한다.

telethon 을 임포트하지 않아 네트워크 없이 테스트된다.
"""
from __future__ import annotations


def resolve_exclusions(
    exclude_titles: set[str], dialog_names: list[str]
) -> tuple[list[str], list[str], list[str]]:
    """제외 목록을 실제 대화 이름 목록에 적용한다.

    Returns:
        targets: 제외되지 않은 대화 이름 (dialog_names 순서 유지)
        excluded: 제외 목록에 걸려 건너뛴 대화 이름 (dialog_names 순서 유지)
        unmatched_exclusions: 제외 목록에는 있으나 어떤 대화와도 일치하지 않은 제목
            (개명·구독 해지 가능성 — 호출자가 경고로 표면화해야 한다)
    """
    names = set(dialog_names)
    targets = [n for n in dialog_names if n not in exclude_titles]
    excluded = [n for n in dialog_names if n in exclude_titles]
    unmatched_exclusions = sorted(t for t in exclude_titles if t not in names)
    return targets, excluded, unmatched_exclusions
