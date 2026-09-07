"""토픽별 원문 슬라이스 추출.

섹션 작성 에이전트에는 Bash 가 없어, 90KB 다이제스트에서 자기 몫 메시지를 찾으려면
Grep 을 여러 번 돌려야 한다. 파이썬이 미리 잘라주면 그 턴이 통째로 사라진다.

`.state/topics.json` -> `.state/sections/input/<topic_id>.md`
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import build_index

_SCRIPT_DIR = Path(__file__).resolve().parent
_STATE_DIR = _SCRIPT_DIR / ".state"
_DAILY_DIR = _SCRIPT_DIR.parent / "daily"
_TOPICS_PATH = _STATE_DIR / "topics.json"
_INPUT_DIR = _STATE_DIR / "sections" / "input"


def slice_for_topic(messages: list, refs: list[dict]) -> str:
    """refs 가 가리키는 메시지만 채널별로 묶어 다이제스트 형식으로 돌려준다.

    index 는 문서 전체 0-based 순번이다. 채널이 일치하지 않거나 범위를 벗어난
    ref 는 조용히 건너뛴다 — 토픽 정의가 낡았을 때 전체가 실패하지 않게 한다.
    """
    by_channel: dict[str, list] = {}
    for ref in refs:
        index = ref.get("index")
        if not isinstance(index, int) or not 0 <= index < len(messages):
            continue
        message = messages[index]
        if message.channel != ref.get("channel"):
            continue
        by_channel.setdefault(message.channel, []).append(message)

    lines: list[str] = []
    for channel, items in by_channel.items():
        lines.append(f"## {channel}")
        for message in items:
            lines.append(build_index._escape_body_block(
                f"- ({message.time}) {message.text}"))
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    date = datetime.now().strftime("%Y-%m-%d")
    digest_path = _DAILY_DIR / f"{date}.md"
    if not digest_path.exists():
        raise SystemExit(f"오늘자 다이제스트가 없습니다: {digest_path}")
    if not _TOPICS_PATH.exists():
        raise SystemExit(
            f"토픽 파일이 없습니다: {_TOPICS_PATH}\n오케스트레이터가 먼저 토픽을 묶어야 합니다.")

    messages = build_index.parse_digest(digest_path.read_text(encoding="utf-8"))
    topics = json.loads(_TOPICS_PATH.read_text(encoding="utf-8"))["topics"]

    _INPUT_DIR.mkdir(parents=True, exist_ok=True)
    for topic in topics:
        body = slice_for_topic(messages, topic.get("message_refs", []))
        out = _INPUT_DIR / f"{topic['topic_id']}.md"
        out.write_text(f"# {topic['title']}\n\n{body}", encoding="utf-8")
        print(f"  {out.name}: {len(body)}자")
    print(f"완료: {len(topics)}개 토픽 슬라이스 -> {_INPUT_DIR}")


if __name__ == "__main__":
    main()
