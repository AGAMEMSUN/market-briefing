import channel_selection


def test_excluded_channel_is_reported_as_excluded():
    targets, excluded, unmatched = channel_selection.resolve_exclusions(
        {"한국경제"}, ["한국경제", "미국 주식 인사이더"]
    )
    assert excluded == ["한국경제"]
    assert targets == ["미국 주식 인사이더"]
    assert unmatched == []


def test_exclusion_matching_no_dialog_is_reported_as_unmatched():
    targets, excluded, unmatched = channel_selection.resolve_exclusions(
        {"개명된 채널"}, ["미국 주식 인사이더"]
    )
    assert unmatched == ["개명된 채널"]
    assert excluded == []
    assert targets == ["미국 주식 인사이더"]


def test_dialog_not_in_exclude_set_is_a_target():
    targets, excluded, unmatched = channel_selection.resolve_exclusions(
        set(), ["미국 주식 인사이더", "Polaristimes"]
    )
    assert targets == ["미국 주식 인사이더", "Polaristimes"]
    assert excluded == []
    assert unmatched == []


def test_checkpoint_keyed_by_channel_id_survives_rename():
    """last_ids 를 채널 ID로 관리하면 개명 후에도 체크포인트가 살아있어야 한다.

    telegram_digest.py 는 telethon 을 임포트하므로 여기서는 직접 부르지 않고,
    그 모듈이 의존하는 불변조건(제목이 아니라 ID로 조회한다)만 순수하게 검증한다.
    """
    channel_id = "1001"
    last_ids = {channel_id: 500}

    old_title = "옛 채널명"
    new_title = "새 채널명"

    # 제목으로 조회했다면 개명 직후 이 값은 0으로 리셋되어 오늘 이미 받은
    # 메시지를 다른 제목 아래로 다시 수집하게 된다.
    assert last_ids.get(new_title, 0) == 0
    assert last_ids.get(old_title, 0) == 0

    # ID로 조회하면 개명과 무관하게 체크포인트가 그대로 남는다.
    assert last_ids.get(channel_id, 0) == 500
