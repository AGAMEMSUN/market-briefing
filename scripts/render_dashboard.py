"""payload JSON 을 대시보드 템플릿에 주입한다.

주입은 `<script>` 블록 안에서 일어나고 payload 에는 텔레그램 채널이 쓴 제3자
본문이 그대로 들어간다. 본문에 `</script>` 가 있으면 스크립트 블록이 거기서
끝나고 나머지가 문서에 HTML 로 주입된다 — 실측으로 재현했다. 그래서 주입은
문자열 치환 한 줄이 아니라 이 모듈이 담당한다.

`<` 를 `\\u003c` 로 바꾸면 `</script>` 와 `<!--` 가 모두 무력화된다. JSON 문자열
안의 `\\u003c` 는 파싱하면 다시 `<` 가 되므로 값은 그대로다.
"""
from __future__ import annotations

import json
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_STATE_DIR = _SCRIPT_DIR / ".state"
_TEMPLATE_PATH = _SCRIPT_DIR.parent / "templates" / "dashboard.html"
_PAYLOAD_PATH = _STATE_DIR / "payload.json"
_OUT_PATH = _STATE_DIR / "dashboard.html"

_PLACEHOLDER = "__PAYLOAD__"
_TITLE_PLACEHOLDER = "__TITLE__"

# 탭 제목은 payload 가 아니라 HTML 에 박혀야 한다 — Artifact 는 파일 앞부분의
# <title> 태그를 읽어 아티팩트 이름을 정하므로 JS 로 바꾸면 반영되지 않는다.
#
# 예전에는 `.env` 의 BRIEFING_BRAND 를 앞에 붙였다. 이 브리핑의 목적이 특정 모임의
# 제출물에서 "텔레그램에 쌓인 정보를 정리해 시황·거시를 파악하는 것"으로 바뀌면서
# 소속 표기가 의미를 잃어 제목을 고정 상수로 되돌렸다.
TITLE = "마켓 브리핑"

# `<` 로 시작하는 태그 종료·주석 시퀀스를 막고, JS 문자열 리터럴에서 줄 종결자로
# 해석될 수 있는 두 문자를 이스케이프한다.
_ESCAPES = {
    "<": "\\u003c",
    " ": "\\u2028",
    " ": "\\u2029",
}


def escape_for_script(payload_json: str) -> str:
    """JSON 문자열을 `<script>` 블록에 넣어도 안전한 형태로 바꾼다.

    값은 보존된다 — `\\u003c` 는 JSON 파서가 다시 `<` 로 되돌린다.
    """
    for raw, escaped in _ESCAPES.items():
        payload_json = payload_json.replace(raw, escaped)
    return payload_json


def render(template: str, payload_json: str) -> str:
    if _PLACEHOLDER not in template:
        raise ValueError(f"템플릿에 {_PLACEHOLDER} 자리표시자가 없습니다.")
    rendered = template.replace(_PLACEHOLDER, escape_for_script(payload_json))
    return rendered.replace(_TITLE_PLACEHOLDER, TITLE)


def main() -> None:
    if not _PAYLOAD_PATH.exists():
        raise SystemExit(f"payload 가 없습니다: {_PAYLOAD_PATH}")

    payload_json = _PAYLOAD_PATH.read_text(encoding="utf-8")
    json.loads(payload_json)  # 깨진 JSON 을 템플릿에 넣지 않는다

    _OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(
        render(_TEMPLATE_PATH.read_text(encoding="utf-8"), payload_json),
        encoding="utf-8",
    )
    print(f"완료: {_OUT_PATH} ({_OUT_PATH.stat().st_size / 1024:.0f}KB, 제목 '{TITLE}')")


if __name__ == "__main__":
    main()
