"""검수 에이전트 전용 경량 뷰.

실측: 검수 에이전트가 190K 토큰을 썼다. 섹션 JSON 7개(71KB)에 더해 스스로
briefing_index.json(156KB)·cluster_view.txt(44KB)·섹션 입력 7개(148KB)까지
읽었기 때문이다. 그중 '누락 찾기'와 '수치 대조'는 결정적 작업이라 파이썬이
하면 된다 — 판단이 필요한 건 그 결과를 읽고 무엇이 문제인지 정하는 일뿐이다.

이 모듈은 세 가지를 미리 계산해 한 파일로 낸다.
1. 섹션 요지 (제목·요약·지표칩·정리 앞부분)
2. 여러 섹션에 걸쳐 나온 수치 — 불일치 후보
3. 어느 토픽에도 안 들어간 메시지 — 누락 후보
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path

_STATE_DIR = Path(__file__).resolve().parent / ".state"
_DAILY_DIR = Path(__file__).resolve().parent.parent / "daily"
_SECTIONS_DIR = _STATE_DIR / "sections"
_TOPICS_PATH = _STATE_DIR / "topics.json"
_INDEX_PATH = _STATE_DIR / "briefing_index.json"
_OUT_PATH = _STATE_DIR / "review_view.txt"

# 숫자 + 단위. 연도(2026)·순번 같은 잡음을 걸러내려고 단위를 필수로 둔다.
_NUMBER = re.compile(
    r"(\d[\d,]*\.?\d*)\s*(%p|%|달러|억달러|조원|억원|만원|원|엔|포인트|bp|GW|톤|배럴)")

_ORGANIZED_HEAD = 500


def numeric_claims(text: str) -> set[str]:
    """본문에서 '수치+단위' 쌍을 뽑는다. 쉼표는 정규화해 7,046.74 와 7046.74 를 같게 본다."""
    claims = set()
    for value, unit in _NUMBER.findall(text or ""):
        claims.add(f"{value.replace(',', '')}{unit}")
    return claims


def shared_numbers(sections: list[dict], min_topics: int = 2) -> dict[str, list[str]]:
    """둘 이상의 섹션에 등장한 수치 -> 등장 토픽 목록. 불일치 대조의 출발점."""
    where: dict[str, set[str]] = defaultdict(set)
    for sec in sections:
        body = f"{sec.get('organized', '')} {sec.get('summary', '')} {sec.get('draft_insight', '')}"
        for claim in numeric_claims(body):
            where[claim].add(sec["topic_id"])
    return {claim: sorted(ids) for claim, ids in sorted(where.items())
            if len(ids) >= min_topics}


def uncovered_messages(index: dict, topics: list[dict]) -> list[tuple[str, int, str]]:
    """노이즈가 아닌데 어느 토픽의 message_refs 에도 없는 메시지."""
    claimed = {ref.get("index") for topic in topics
               for ref in topic.get("message_refs", [])}
    out = []
    for channel, meta in (index.get("channels") or {}).items():
        for message in meta.get("messages", []):
            if message.get("noise") or message.get("index") in claimed:
                continue
            out.append((channel, message["index"], message.get("headline", "")[:70]))
    return out


# 조사·접미어가 붙어도 남는 2글자 이상 덩어리만 본다. 이모지·기호는 자연히 빠진다.
_TOKEN = re.compile(r"[가-힣]{2,}|[A-Za-z]{3,}")
_URL = re.compile(r"https?://\S+")


def tokenize(headline: str) -> set[str]:
    return set(_TOKEN.findall(_URL.sub(" ", headline or "")))


def _all_messages(index: dict):
    for channel, meta in (index.get("channels") or {}).items():
        for message in meta.get("messages", []):
            if not message.get("noise"):
                yield channel, message


def document_frequency(texts: list[str]) -> dict[str, int]:
    """토큰별 등장 문서 수. 흔한 말을 클러스터 키에서 빼기 위한 변별력 척도."""
    df: dict[str, int] = defaultdict(int)
    for text in texts:
        for token in tokenize(text):
            df[token] += 1
    return df


def missed_clusters(index: dict, topics: list[dict], corpus_df: dict[str, int] | None = None,
                    *, min_messages: int = 3, min_channels: int = 2,
                    max_corpus_df: int = 6, limit: int = 10) -> list[tuple[str, list]]:
    """놓친 메시지들이 공유하는 **희소** 키워드로 '묶였어야 할 덩어리'를 찾는다.

    누락 후보를 그냥 나열하면 291건이 나오고 대부분 랍스터 뷔페 같은 잡음이다.
    여러 채널이 동시에 다룬 주제만 진짜 누락일 확률이 높다 — 실측으로 놓쳤던
    '대미투자·원전' 클러스터가 정확히 이 형태였다.

    다만 '미국'·'글로벌' 처럼 흔한 말로 묶으면 아무 관계없는 기사가 한 덩어리가
    된다. 그래서 등장 문서수가 max_corpus_df 이하인 희소 토큰만 키로 쓴다.
    `corpus_df` 는 **헤드라인이 아니라 본문 전체**로 세야 한다 — 헤드라인만 보면
    '글로벌'도 희소해 보여서 필터가 뚫린다(실측).
    """
    if corpus_df is None:
        corpus_df = document_frequency(
            [m.get("headline", "") for _, m in _all_messages(index)])

    by_token: dict[str, list] = defaultdict(list)
    for channel, idx, headline in uncovered_messages(index, topics):
        for token in tokenize(headline):
            if corpus_df.get(token, 0) <= max_corpus_df:
                by_token[token].append((channel, idx, headline))

    clusters = [(token, items) for token, items in by_token.items()
                if len(items) >= min_messages
                and len({c for c, _, _ in items}) >= min_channels]
    # 희소할수록·채널이 넓을수록 먼저.
    clusters.sort(key=lambda kv: (-len({c for c, _, _ in kv[1]}),
                                  corpus_df.get(kv[0], 0), kv[0]))

    # 같은 메시지가 여러 키워드로 중복 보고되지 않게 한 번 쓴 메시지는 뺀다.
    seen: set[int] = set()
    deduped = []
    for token, items in clusters:
        fresh = [it for it in items if it[1] not in seen]
        if len(fresh) < min_messages or len({c for c, _, _ in fresh}) < min_channels:
            continue
        seen.update(idx for _, idx, _ in fresh)
        deduped.append((token, fresh))
        if len(deduped) >= limit:
            break
    return deduped


def render(sections: list[dict], index: dict, topics: list[dict],
           corpus_df: dict[str, int] | None = None) -> str:
    lines = [f"섹션 {len(sections)}개 검수 뷰 — 원문·검증노트·출처는 제외했다.",
             "필요하면 scripts/.state/sections/<id>.json 을 직접 열어라.", ""]

    lines.append("## 섹션 요지")
    for sec in sections:
        lines.append(f"\n### {sec['topic_id']} {sec['title']}")
        lines.append(f"  primary: {sec.get('primary_channel', '')}")
        merged = sec.get("merged_channels") or [m.get("channel") for m in sec.get("merged", [])]
        lines.append(f"  merged: {', '.join(c for c in merged if c) or '없음'}")
        chips = ", ".join(f"{i.get('label')}={i.get('value')}({i.get('change')})"
                          for i in sec.get("indicators", []))
        lines.append(f"  지표칩: {chips or '없음'}")
        lines.append(f"  요약: {sec.get('summary', '')}")
        head = (sec.get("organized") or "")[:_ORGANIZED_HEAD]
        lines.append(f"  정리(앞 {_ORGANIZED_HEAD}자): {head}…")

    shared = shared_numbers(sections)
    lines.append(f"\n## 여러 섹션에 나온 수치 {len(shared)}건 — 값이 서로 맞는지 확인 대상")
    for claim, ids in shared.items():
        lines.append(f"  {claim:>14}  {', '.join(ids)}")

    total_missing = len(uncovered_messages(index, topics))
    clusters = missed_clusters(index, topics, corpus_df)
    lines.append(f"\n## 누락 후보 — 미포함 {total_missing}건 중 여러 채널이 함께 다룬 {len(clusters)}덩어리")
    lines.append("   (단발성 기사는 뺐다. 아래가 토픽으로 묶였어야 할 후보다.)")
    for token, items in clusters:
        channels = sorted({c for c, _, _ in items})
        lines.append(f"\n  ▸ '{token}' — {len(items)}건 / {len(channels)}개 채널")
        for channel, idx, headline in items[:6]:
            lines.append(f"    [{idx}] ({channel[:18]}) {headline}")
        if len(items) > 6:
            lines.append(f"    … 외 {len(items) - 6}건")
    return "\n".join(lines) + "\n"


def _corpus_df_from_digest() -> dict[str, int] | None:
    """본문 전체로 토큰 빈도를 센다. 헤드라인만으로는 흔한 말이 희소해 보인다."""
    digest = _DAILY_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    if not digest.exists():
        return None
    import build_index

    messages = build_index.parse_digest(digest.read_text(encoding="utf-8"))
    return document_frequency([m.text for m in messages])


def main() -> None:
    topics = json.loads(_TOPICS_PATH.read_text(encoding="utf-8"))["topics"]
    sections = []
    for topic in topics:
        path = _SECTIONS_DIR / f"{topic['topic_id']}.json"
        if path.exists():
            sections.append(json.loads(path.read_text(encoding="utf-8")))
    index = json.loads(_INDEX_PATH.read_text(encoding="utf-8")) if _INDEX_PATH.exists() else {}

    text = render(sections, index, topics, _corpus_df_from_digest())
    _OUT_PATH.write_text(text, encoding="utf-8")
    raw = sum((_SECTIONS_DIR / f"{t['topic_id']}.json").stat().st_size
              for t in topics if (_SECTIONS_DIR / f"{t['topic_id']}.json").exists())
    print(f"완료: {_OUT_PATH} ({len(text.encode('utf-8'))/1024:.0f}KB, "
          f"섹션 원본 {raw/1024:.0f}KB 대비 {len(text.encode('utf-8'))/raw*100:.0f}%)")


if __name__ == "__main__":
    main()
