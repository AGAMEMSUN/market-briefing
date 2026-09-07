"""다이제스트 실행 결과 매니페스트.

세 가지를 구분한다.
- `collected`  이번 실행에서 실제로 조회한 채널
- `excluded`   제외 목록에 걸려 건너뛴 채널
- `not_checked` 요청 제한(서킷브레이커)으로 조회하지 못한 채널

`not_checked` 를 따로 두는 이유는, 조회조차 못 한 채널을 "오늘 새 글 없음"으로
보고하면 대시보드가 읽지도 않은 채널을 없다고 단언하기 때문이다.

telethon 을 임포트하지 않아 CI 에서도 테스트된다. 매니페스트는 중간 산출물이라
`scripts/.state/` 아래에 쓴다.
"""
from __future__ import annotations

import json
from pathlib import Path

_KEYS = ("collected", "excluded", "not_checked")


def manifest_path(state_dir: Path, date: str) -> Path:
    return state_dir / f"{date}.manifest.json"


def write_manifest(
    path: Path,
    collected: list[str],
    excluded: list[str],
    not_checked: list[str] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "collected": collected,
        "excluded": excluded,
        "not_checked": list(not_checked or []),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_manifest(path: Path) -> dict:
    # 키가 없는 예전 매니페스트도 읽을 수 있어야 하므로 키마다 새 빈 리스트로 채운다.
    if not path.exists():
        return {key: [] for key in _KEYS}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {key: list(data.get(key, [])) for key in _KEYS}
