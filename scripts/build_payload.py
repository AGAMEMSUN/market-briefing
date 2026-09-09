"""섹션 JSON + 시세 스냅샷 -> payload.json (렌더 데이터 계약).

오케스트레이터가 섹션 JSON 7개(71KB ≈ 15K 토큰)를 컨텍스트로 읽고 payload 를
손으로 조립하던 단계를 없앤다. 섹션 작성 에이전트가 `merged` 를 [{channel, what}]
형태로 이미 내놓으므로 여기서는 필드를 옮겨 담기만 하면 된다.

스파크라인은 시계열이 **물리적으로 불가능한 값**(0 이하, 일간 50% 초과)을 담고
있을 때만 뺀다. 변동이 크다는 이유로 빼면 안 된다 — 그건 장세지 오류가 아니다.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_STATE_DIR = Path(__file__).resolve().parent / ".state"
_SECTIONS_DIR = _STATE_DIR / "sections"
_TOPICS_PATH = _STATE_DIR / "topics.json"
_SNAPSHOT_PATH = _STATE_DIR / "market_snapshot.json"
_INDEX_PATH = _STATE_DIR / "briefing_index.json"
_OUT_PATH = _STATE_DIR / "payload.json"

LABELS = {
    "KOSPI": "코스피", "KOSDAQ": "코스닥", "KR 10Y": "국고채 10Y",
    "S&P 500": "S&P 500", "NASDAQ": "나스닥",
    "US 10Y": "미 국채 10Y", "VIX": "VIX",
    "USD/KRW": "원/달러", "DXY": "달러인덱스", "WTI": "WTI유", "Gold": "금",
}

# 하루에 지수가 50% 넘게 움직이는 일은 없다 — 그 정도면 시장이 아니라 데이터가 틀린 것이다.
#
# 예전에는 '5% 초과일이 많으면 시계열이 깨진 것'으로 보고 코스피·코스닥 차트를 뺐다.
# 토스(KRX)와 야후를 65일 대조해보니 값이 다른 날은 당일 하나뿐이었고 나머지 64일은
# 완전히 일치했다 — 급변동은 데이터 오류가 아니라 실제 장세였다. 멀쩡한 차트를 빼면서
# "비정상 변동" 이라고 독자에게 알린 셈이라 기준을 '물리적으로 불가능한 값' 으로 바꿨다.
_IMPOSSIBLE_JUMP_PCT = 50.0
_SPARK_POINTS = 45


def _is_broken(history: list[float]) -> str | None:
    """시계열이 실제로 망가졌을 때만 사유를 돌려준다. 아니면 None."""
    if any(v is None or v <= 0 for v in history):
        return "0 이하 값이 섞여 있어 추이 차트 제외"
    jumps = [abs(history[i + 1] / history[i] - 1) * 100 for i in range(len(history) - 1)]
    if jumps and max(jumps) > _IMPOSSIBLE_JUMP_PCT:
        return (f"일간 {max(jumps):.0f}% 변동이 있어 데이터 오류로 보고 추이 차트 제외")
    return None


def strip_indicators(snapshot: dict) -> list[dict]:
    out = []
    for ind in snapshot.get("indicators", []):
        if "error" in ind:
            continue
        history = [h["close"] for h in (ind.get("history") or []) if h.get("close") is not None]
        note = _is_broken(history) if len(history) > 1 else None
        bad = note is not None
        out.append({
            "name": ind["name"],
            "label": LABELS.get(ind["name"], ind["name"]),
            "last": ind.get("last"),
            "unit": ind.get("unit"),
            "change_pct": ind.get("change_pct"),
            "as_of": ind.get("as_of"),
            "source": ind.get("source"),
            "spark": None if bad or len(history) < 2 else history[-_SPARK_POINTS:],
            "note": note,
        })
    return out


def strip_sectors(snapshot: dict) -> list[dict]:
    """섹터는 스냅샷에서 이미 등락률 내림차순으로 정렬돼 있고 라벨도 붙어 있다.

    렌더가 쓰는 네 필드만 옮긴다 — `last` 는 히트맵에 안 나가므로 싣지 않는다.
    """
    return [{"ticker": s.get("ticker"), "label": s.get("label"),
             "change_pct": s.get("change_pct"), "as_of": s.get("as_of")}
            for s in (snapshot.get("sectors") or [])
            if isinstance(s.get("change_pct"), (int, float))]


def topic_payload(section: dict) -> dict:
    """섹션 JSON 에서 렌더가 쓰는 필드만 옮긴다.

    verification_notes 는 의도적으로 제외한다 — 대시보드에 안 나가고, payload 를
    키우면 렌더 산출물과 아티팩트 발행 비용이 같이 커진다.
    """
    merged = section.get("merged")
    if not merged:  # 구버전 섹션 호환 — 채널명만 있으면 설명 없이 싣는다
        merged = [{"channel": c, "what": "관련 보도"}
                  for c in section.get("merged_channels", [])]
    return {
        "topic_id": section["topic_id"],
        "title": section["title"],
        "primary_channel": section.get("primary_channel", ""),
        "merged": merged,
        "organized": section.get("organized", ""),
        "summary": section.get("summary", ""),
        "indicators": [{"label": i.get("label"), "value": i.get("value"),
                        "change": i.get("change", "")}
                       for i in section.get("indicators", [])],
        "draft_insight": section.get("draft_insight", ""),
        "sources": section.get("sources", []),
    }


def build(topics: list[dict], sections: list[dict], snapshot: dict, index: dict,
          collected_at: str = "") -> dict:
    noise = index.get("noise_counts") or {}
    parts = [f"{label} {noise[key]}건" for key, label in
             (("advertorial", "협찬성 기사"), ("low_signal", "저신호 단문"),
              ("near_duplicate", "채널 내 근사중복")) if noise.get(key)]
    total = sum(len(m.get("messages", [])) for m in (index.get("channels") or {}).values())
    return {
        "date": index.get("date") or datetime.now().strftime("%Y-%m-%d"),
        "collected_at": collected_at,
        "channel_count": len(index.get("channels") or {}),
        "message_count": total,
        "filtered_out": noise,
        "indicators": strip_indicators(snapshot),
        "sectors": strip_sectors(snapshot),
        "topics": [topic_payload(s) for s in sections],
        "quiet_channels": index.get("quiet_channels") or [],
        "not_checked_channels": index.get("not_checked_channels") or [],
        "excluded_channels": index.get("excluded_channels") or [],
        "excluded_note": ("파이썬 노이즈 필터로 " + "·".join(parts) + " 제외."
                          if parts else ""),
    }


def main() -> None:
    topics = json.loads(_TOPICS_PATH.read_text(encoding="utf-8"))["topics"]
    sections, missing = [], []
    for topic in topics:
        path = _SECTIONS_DIR / f"{topic['topic_id']}.json"
        if path.exists():
            sections.append(json.loads(path.read_text(encoding="utf-8")))
        else:
            missing.append(topic["topic_id"])
    if missing:
        raise SystemExit(f"섹션 JSON 이 없습니다: {', '.join(missing)}")

    payload = build(
        topics, sections,
        json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8")),
        json.loads(_INDEX_PATH.read_text(encoding="utf-8")),
        collected_at=datetime.now().strftime("%H:%M KST 발행"))
    _OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    dropped = [i["label"] for i in payload["indicators"] if i["spark"] is None]
    print(f"완료: {_OUT_PATH} (토픽 {len(payload['topics'])}개, "
          f"지표 {len(payload['indicators'])}개, 섹터 {len(payload['sectors'])}개, "
          f"{_OUT_PATH.stat().st_size/1024:.0f}KB)")
    if dropped:
        print(f"  스파크라인 제외(시계열 이상): {', '.join(dropped)}")


if __name__ == "__main__":
    main()
