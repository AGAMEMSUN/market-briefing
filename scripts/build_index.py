"""다이제스트 원문 -> 축약 인덱스 + 중복 그룹.

오케스트레이터가 원문 전체(수십만 토큰) 대신 이 인덱스만 읽고 토픽을
클러스터링한다. 중복 탐지 중 확실한 신호(같은 URL, 거의 같은 문장)는
여기서 결정적으로 처리하고, 의미 수준 병합만 LLM 이 판단한다.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import digest_manifest
import noise_filter
from channels import ADVERTORIAL_CHANNELS

_SCRIPT_DIR = Path(__file__).resolve().parent
_STATE_DIR = _SCRIPT_DIR / ".state"
_DAILY_DIR = _SCRIPT_DIR.parent / "daily"
_OUT_PATH = _STATE_DIR / "briefing_index.json"

_CHANNEL_RE = re.compile(r"^## (.+)$")
_MESSAGE_RE = re.compile(r"^- \((\d{2}:\d{2})\) (.*)$")
# 한국어 본문은 URL 을 「 」·『 』·（ ） 같은 전각 괄호로 감싸는 일이 잦다 —
# 닫는 괄호를 URL 에 빨아들이면 다른 채널의 같은 URL 과 매칭되지 않는다.
_URL_RE = re.compile(r"https?://[^\s)>\]」』〉》】〕｝”’）]+")
_TICKER_RE = re.compile(r"\$[A-Z]{1,5}\b")
_HEADING_LINE_RE = re.compile(r"^#{1,6} ")


@dataclass
class Message:
    channel: str
    time: str
    text: str


def parse_digest(text: str) -> list[Message]:
    """다이제스트 마크다운을 메시지 리스트로 되돌린다.

    본문에 `## 소제목` 같은 줄이 있어도 채널 헤더로 오인하지 않는다. 다이제스트를
    쓰는 쪽(`merge_digest`)이 채널 헤더 앞에 항상 빈 줄을 넣으므로, "바로 앞 줄이
    빈 줄일 때의 `## `" 만 채널 헤더로 본다. 문서 첫 줄은 앞이 비어있는 것으로 본다.
    """
    messages: list[Message] = []
    channel: str | None = None
    current: Message | None = None
    prev_blank = True
    for line in text.splitlines():
        header = _CHANNEL_RE.match(line) if prev_blank else None
        prev_blank = not line.strip()
        if header:
            channel = header.group(1).strip()
            current = None
            continue
        body = _MESSAGE_RE.match(line)
        if body and channel:
            current = Message(channel=channel, time=body.group(1), text=body.group(2))
            messages.append(current)
            continue
        if current is not None:
            current.text += "\n" + line
    for message in messages:
        message.text = message.text.strip()
    return messages


def extract_urls(text: str) -> list[str]:
    urls: list[str] = []
    for raw in _URL_RE.findall(text):
        url = re.sub(r"[?#].*$", "", raw.rstrip(".,"))
        if url not in urls:
            urls.append(url)
    return urls


def extract_tickers(text: str) -> list[str]:
    tickers: list[str] = []
    for ticker in _TICKER_RE.findall(text):
        if ticker not in tickers:
            tickers.append(ticker)
    return tickers


def _normalize(text: str) -> str:
    stripped = _URL_RE.sub(" ", text)
    stripped = re.sub(r"[^0-9A-Za-z가-힣]+", " ", stripped)
    return re.sub(r"\s+", " ", stripped).strip().lower()


def _shingles(text: str, size: int = 5) -> set[str]:
    packed = _normalize(text).replace(" ", "")
    if not packed:
        return set()
    if len(packed) < size:
        return {packed}
    return {packed[i:i + size] for i in range(len(packed) - size + 1)}


def similarity(a: str, b: str) -> float:
    """문자 5-gram 자카드 유사도. 한국어 형태소 분석기 없이도 동작한다."""
    sa, sb = _shingles(a), _shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def find_duplicate_groups(messages: list[Message], threshold: float = 0.6) -> list[dict]:
    """같은 이슈를 다룬 메시지를 묶는다. 같은 채널 안의 반복은 묶지 않는다."""
    parent = list(range(len(messages)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    url_linked: set[int] = set()
    url_seen: dict[str, list[int]] = {}
    for i, message in enumerate(messages):
        for url in extract_urls(message.text):
            for prev in url_seen.get(url, []):
                if messages[prev].channel != message.channel:
                    union(prev, i)
                    url_linked.update({prev, i})
            url_seen.setdefault(url, []).append(i)

    for i in range(len(messages)):
        for j in range(i + 1, len(messages)):
            if messages[i].channel == messages[j].channel:
                continue
            if find(i) == find(j):
                continue
            if similarity(messages[i].text, messages[j].text) >= threshold:
                union(i, j)

    buckets: dict[int, list[int]] = {}
    for i in range(len(messages)):
        buckets.setdefault(find(i), []).append(i)

    groups = []
    for members in buckets.values():
        if len(members) < 2:
            continue
        reason = "shared_url" if url_linked & set(members) else "similar_text"
        groups.append({"members": sorted(members), "reason": reason})
    return sorted(groups, key=lambda g: g["members"][0])


def _headline(text: str, limit: int = 70) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:limit]
    return ""


def build_index(digest_text: str, manifest: dict, date: str) -> dict:
    messages = parse_digest(digest_text)
    groups = find_duplicate_groups(messages)
    noise = noise_filter.annotate(messages, similarity, ADVERTORIAL_CHANNELS)
    not_checked = list(manifest.get("not_checked", []))

    group_of: dict[int, str] = {}
    for number, group in enumerate(groups):
        for index in group["members"]:
            group_of[index] = f"g{number}"

    channels: dict[str, dict] = {}
    for index, message in enumerate(messages):
        entry = channels.setdefault(message.channel, {"message_count": 0, "messages": []})
        entry["message_count"] += 1
        entry["messages"].append({
            "index": index,
            "time": message.time,
            "headline": _headline(message.text),
            "chars": len(message.text),
            "urls": extract_urls(message.text),
            "tickers": extract_tickers(message.text),
            "duplicate_group": group_of.get(index),
            "noise": noise[index],
        })

    return {
        "date": date,
        "channels": channels,
        "noise_counts": noise_filter.count_by_reason(noise),
        "duplicate_groups": [
            {"group_id": f"g{number}", "reason": group["reason"],
             "members": [{"channel": messages[i].channel, "index": i} for i in group["members"]]}
            for number, group in enumerate(groups)
        ],
        # 서킷브레이커로 아예 조회하지 못한 채널은 "오늘 새 글 없음"이 아니다 —
        # 조용한 채널과 섞이면 대시보드가 읽지도 않은 채널을 없다고 단언하게 된다.
        "quiet_channels": [c for c in manifest.get("collected", [])
                           if c not in channels and c not in not_checked],
        "excluded_channels": list(manifest.get("excluded", [])),
        "not_checked_channels": not_checked,
    }


def cluster_view(index: dict) -> str:
    """오케스트레이터가 토픽을 묶을 때 읽는 경량 뷰.

    전체 인덱스 JSON 은 메시지당 메타데이터(urls/tickers/chars) 때문에 원문보다
    커진다. 클러스터링에 필요한 건 채널·번호·시각·헤드라인뿐이라 그것만 남긴다.
    노이즈로 표시된 메시지는 빠지되, 몇 건이 왜 빠졌는지는 머리말에 적는다.
    """
    lines = [f"date={index['date']} channels={len(index['channels'])} "
             f"dupgroups={len(index['duplicate_groups'])}"]
    counts = index.get("noise_counts") or {}
    if counts:
        lines.append("noise " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    for channel, data in index["channels"].items():
        shown = [m for m in data["messages"] if m.get("noise") is None]
        lines.append(f"\n## {channel} ({len(shown)}/{data['message_count']})")
        for message in shown:
            lines.append(f"  [{message['index']}] {message['time']} {message['headline']}")

    for key, label in (("quiet_channels", "quiet"),
                       ("not_checked_channels", "not_checked"),
                       ("excluded_channels", "excluded")):
        values = index.get(key) or []
        if values:
            lines.append(f"\n{label}: {', '.join(values)}")
    return "\n".join(lines)


def _escape_body_block(block: str) -> str:
    """메시지 한 건을 다이제스트에 쓸 수 있는 형태로 이스케이프한다.

    본문의 "이어지는 줄"이 `## 소제목` 처럼 마크다운 헤딩으로 시작하면 앞에 공백
    한 칸을 붙인다. `_CHANNEL_RE` 는 `^## ` 로 앵커돼 있으므로 공백 한 칸이면
    채널 헤더와의 충돌이 구조적으로 불가능해진다 — 앞 줄이 빈 줄인지 보는
    `parse_digest` 의 방어(예전 파일용)와 달리, 여기서는 애초에 헷갈릴 모양을
    만들지 않는다.

    첫 줄은 항상 `- (HH:MM) ...` 라 헤딩이 될 수 없으므로 건드리지 않는다.
    이미 공백으로 시작하는 줄은 `^#{1,6} ` 에 걸리지 않아 다시 써도 공백이
    덧붙지 않는다(멱등).
    """
    lines = block.split("\n")
    return "\n".join(
        [lines[0]] + [" " + line if _HEADING_LINE_RE.match(line) else line for line in lines[1:]]
    )


def merge_digest(existing_text: str | None, collected: dict[str, list[str]], date: str) -> str:
    """기존 다이제스트 텍스트와 이번 실행에서 새로 수집된 메시지를 합쳐 다이제스트
    마크다운 전체를 다시 만든다.

    telegram_digest.py 의 `last_ids.json` 은 실행마다 "그 이후 새 메시지"만 돌려주므로,
    같은 날 재실행하면 새 수집분으로 덮어쓰지 않고 기존 파일과 합쳐야 그날의 누적
    수집이 보존된다. (channel, time, text) 조합이 같으면 중복으로 보고 한 번만 남기고,
    각 채널 안에서는 원래 순서(=시간순)를 유지한다.
    """
    parts: list[str] = []
    if existing_text:
        # 채널 헤더 앞에는 반드시 빈 줄이 와야 한다 (parse_digest 가 그것으로
        # 본문 속 `## ` 줄과 진짜 채널 헤더를 구분한다).
        parts.append(existing_text.rstrip("\n"))
        parts.append("")
    for title, msgs in collected.items():
        parts.append(f"## {title}")
        # 텔레그램 원문 그대로 넣으면 본문 속 헤딩 줄이 아래 parse_digest 단계에서
        # 채널 헤더로 읽혀 그 뒤 본문이 통째로 사라진다 — 넣기 전에 이스케이프한다.
        parts.extend(_escape_body_block(msg) for msg in msgs)
        parts.append("")
    combined_text = "\n".join(parts)

    channel_order: list[str] = []
    channel_messages: dict[str, list[Message]] = {}
    seen: set[tuple[str, str, str]] = set()
    for message in parse_digest(combined_text):
        key = (message.channel, message.time, message.text)
        if key in seen:
            continue
        seen.add(key)
        if message.channel not in channel_messages:
            channel_messages[message.channel] = []
            channel_order.append(message.channel)
        channel_messages[message.channel].append(message)

    lines = [f"# 텔레그램 다이제스트 — {date} 정오 기준", ""]
    if not channel_order:
        lines.append("_새 메시지 없음_")
    for channel in channel_order:
        lines.append(f"## {channel}")
        for message in channel_messages[channel]:
            body_lines = _escape_body_block(message.text).splitlines() or [""]
            lines.append(f"- ({message.time}) {body_lines[0]}")
            lines.extend(body_lines[1:])
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    date = datetime.now().strftime("%Y-%m-%d")
    digest_path = _DAILY_DIR / f"{date}.md"
    if not digest_path.exists():
        raise SystemExit(f"오늘자 다이제스트가 없습니다: {digest_path}\ntelegram_digest.py 를 먼저 실행하세요.")

    manifest = digest_manifest.read_manifest(digest_manifest.manifest_path(_STATE_DIR, date))
    index = build_index(digest_path.read_text(encoding="utf-8"), manifest, date)

    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _OUT_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    view_path = _STATE_DIR / "cluster_view.txt"
    view_path.write_text(cluster_view(index), encoding="utf-8")

    total = sum(c["message_count"] for c in index["channels"].values())
    raw_kb = len(digest_path.read_text(encoding="utf-8")) / 1024
    index_kb = _OUT_PATH.stat().st_size / 1024
    print(f"완료: {_OUT_PATH}")
    print(f"  채널 {len(index['channels'])}개 / 메시지 {total}건 / 중복그룹 {len(index['duplicate_groups'])}개")
    print(f"  원문 {raw_kb:.0f}KB -> 인덱스 {index_kb:.0f}KB")
    print(f"  클러스터 뷰 {view_path.stat().st_size / 1024:.0f}KB -> {view_path}")


if __name__ == "__main__":
    main()
