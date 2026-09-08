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
_SECTIONS_DIR = _STATE_DIR / "sections"
_INPUT_DIR = _SECTIONS_DIR / "input"


def slice_for_topic(messages: list, refs: list[dict]) -> str:
    """refs 가 가리키는 메시지만 채널별로 묶어 다이제스트 형식으로 돌려준다.

    index 는 **문서 전체 0-based 순번**이며 `cluster_view.txt` 의 `[N]` 값과 같다.
    채널별 순번이 아니다 — 헷갈리면 모든 ref 가 조용히 걸러져 빈 파일이 나온다.

    채널이 일치하지 않거나 범위를 벗어난 ref 는 건너뛴다 — 토픽 정의가 낡았을 때
    전체가 실패하지 않게 한다. 다만 하나도 안 맞으면 호출부가 경고를 낸다.
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


def purge_stale(directories: list[Path], keep: set[str], suffix: str) -> list[str]:
    """오늘 토픽에 없는 파일을 지운다.

    지난 실행의 t7.md 가 남아 있으면 검수 에이전트가 그걸 읽고 "t7 섹션 누락"
    이라는 없는 문제를 보고한다 — 실측으로 겪었다. 상태 디렉터리는 실행마다
    오늘 토픽만 담고 있어야 한다.
    """
    removed = []
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.glob(f"*{suffix}"):
            if path.stem not in keep:
                path.unlink()
                removed.append(f"{directory.name}/{path.name}")
    return removed


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
    ids = {t["topic_id"] for t in topics}

    _INPUT_DIR.mkdir(parents=True, exist_ok=True)
    removed = (purge_stale([_INPUT_DIR], ids, ".md")
               + purge_stale([_SECTIONS_DIR], ids, ".json"))
    if removed:
        print(f"이전 실행 잔여 파일 {len(removed)}개 삭제: {', '.join(removed)}")

    empty = []
    for topic in topics:
        body = slice_for_topic(messages, topic.get("message_refs", []))
        out = _INPUT_DIR / f"{topic['topic_id']}.md"
        out.write_text(f"# {topic['title']}\n\n{body}", encoding="utf-8")
        if not body.strip():
            empty.append(topic["topic_id"])
        print(f"  {out.name}: {len(body)}자")

    if empty:
        raise SystemExit(
            f"\n오류: {', '.join(empty)} 의 슬라이스가 비었습니다.\n"
            f"message_refs 의 index 는 채널별 순번이 아니라 문서 전체 0-based 순번"
            f"(cluster_view.txt 의 [N] 값)입니다. 채널명도 정확히 일치해야 합니다.")
    print(f"완료: {len(topics)}개 토픽 슬라이스 -> {_INPUT_DIR}")


if __name__ == "__main__":
    main()
