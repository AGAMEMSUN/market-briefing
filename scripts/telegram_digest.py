"""매일 정오, Windows 작업 스케줄러가 실행한다.

구독 중인 브로드캐스트 채널 전체에서 "마지막 실행 이후 ~ 지금까지" 새 메시지만
모아 daily/YYYY-MM-DD.md 로 저장한다. channels.EXCLUDE_TITLES 에
있는 채널만 건너뛴다.

안전장치:
- 읽기 전용 — 메시지 전송/채널 참여/설정 변경을 하지 않는다.
- 서킷브레이커 — FloodWaitError(요청 제한) 발생 시 재시도하지 않고 즉시 중단,
  로그만 남긴다.
- 매 실행 결과를 .state/run_log.jsonl 에 기록한다.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from telethon.sync import TelegramClient
from telethon.errors import FloodWaitError, UnauthorizedError

from channels import EXCLUDE_TITLES
import build_index
import channel_selection
import digest_manifest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_DIR = Path(__file__).resolve().parent
_STATE_DIR = _SCRIPT_DIR / ".state"
_DAILY_DIR = _ROOT / "daily"
_SESSION_PATH = _STATE_DIR / "session"
_LAST_IDS_PATH = _STATE_DIR / "last_ids.json"
_LOG_PATH = _STATE_DIR / "run_log.jsonl"

_MAX_MESSAGES_PER_CHANNEL = 200


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def _prune_legacy_keys(data: dict[str, int]) -> dict[str, int]:
    """제목으로 키잉하던 시절의 항목을 걷어낸다.

    체크포인트는 채널 ID 문자열로 키잉한다. 옛 제목 키를 그대로 두면 파일이
    영원히 수렴하지 않는다. ID 형태(정수 문자열)만 남긴다 — 이번 실행에서 못 본
    채널의 체크포인트까지 지우면 수집이 중간에 끊겼을 때 재수집이 발생한다.
    """
    return {k: v for k, v in data.items() if k.lstrip("-").isdigit()}


def _load_last_ids() -> dict[str, int]:
    if _LAST_IDS_PATH.exists():
        return json.loads(_LAST_IDS_PATH.read_text(encoding="utf-8"))
    return {}


def _save_last_ids(data: dict[str, int]) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    _LAST_IDS_PATH.write_text(
        json.dumps(_prune_legacy_keys(data), ensure_ascii=False, indent=2), encoding="utf-8")


def _log(event: dict) -> None:
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    event = {"ts": datetime.now(timezone.utc).isoformat(), **event}
    with _LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _write_digest(collected: dict[str, list[str]]) -> Path:
    today = datetime.now().strftime("%Y-%m-%d")
    _DAILY_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _DAILY_DIR / f"{today}.md"
    # 같은 날 재실행 시 last_ids.json 때문에 collected 에는 "이번에 새로 온 것"만
    # 들어있다 — 기존 파일을 덮어쓰면 그날 누적 수집이 사라지므로 합쳐서 쓴다.
    existing_text = out_path.read_text(encoding="utf-8") if out_path.exists() else None
    merged = build_index.merge_digest(existing_text, collected, today)
    out_path.write_text(merged, encoding="utf-8")
    return out_path


def main() -> None:
    _load_dotenv(_ROOT / ".env")
    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        _log({"level": "error", "msg": "missing TELEGRAM_API_ID/HASH"})
        raise SystemExit("TELEGRAM_API_ID / TELEGRAM_API_HASH가 .env에 없습니다.")
    if not _SESSION_PATH.with_suffix(".session").exists():
        _log({"level": "error", "msg": "session missing — run telegram_login_setup.py first"})
        raise SystemExit("세션 파일이 없습니다. telegram_login_setup.py를 먼저 실행하세요.")

    last_ids = _load_last_ids()
    collected: dict[str, list[str]] = {}
    checked_titles: list[str] = []
    excluded_titles: list[str] = []
    unmatched_exclusions: list[str] = []
    all_titles: list[str] = []
    flood_wait_seconds: int | None = None

    local_tz = datetime.now().astimezone().tzinfo
    today_start_utc = datetime.combine(date.today(), dtime.min, tzinfo=local_tz).astimezone(timezone.utc)

    try:
        with TelegramClient(str(_SESSION_PATH), int(api_id), api_hash) as client:
            # 브로드캐스트 채널만 — 그룹과 1:1 대화는 구조적으로 제외된다.
            dialogs = [d for d in client.iter_dialogs() if d.is_channel and not d.is_group]
            dialog_names = [d.name for d in dialogs]
            target_names, excluded_titles, unmatched_exclusions = (
                channel_selection.resolve_exclusions(EXCLUDE_TITLES, dialog_names)
            )
            target_names_set = set(target_names)
            targets = [d for d in dialogs if d.name in target_names_set]

            for dialog in targets:
                title = dialog.name
                # 체크포인트는 채널 ID 로 관리한다 — 제목은 개명으로 바뀌지만 ID 는
                # 그대로다. 제목으로 관리하면 개명 시 since_id 가 0 으로 리셋돼
                # 오늘 이미 받은 메시지를 다른 제목 아래로 다시 수집하게 되고,
                # 그 중복은 merge_digest 의 (channel, time, text) 중복 제거를
                # 피해가며 find_duplicate_groups 에 "서로 다른 채널의 교차 중복"
                # 이라는 가짜 신호로 잡힌다.
                channel_id = str(dialog.entity.id)
                checked_titles.append(title)
                since_id = last_ids.get(channel_id, 0)
                messages: list[str] = []
                max_id_seen = since_id
                try:
                    for msg in client.iter_messages(
                        dialog.entity, min_id=since_id, limit=_MAX_MESSAGES_PER_CHANNEL
                    ):
                        if msg.date < today_start_utc:
                            break
                        if msg.message:
                            messages.append(f"- ({msg.date:%H:%M}) {msg.message.strip()}")
                        max_id_seen = max(max_id_seen, msg.id)
                except FloodWaitError as e:
                    flood_wait_seconds = e.seconds
                    _log({"level": "error", "channel": title, "msg": f"flood_wait {e.seconds}s"})
                    checked_titles.pop()
                    break
                if messages:
                    collected[title] = list(reversed(messages))
                last_ids[channel_id] = max_id_seen

            all_titles = [d.name for d in targets]
    except FloodWaitError as e:
        _log({"level": "error", "msg": f"flood_wait {e.seconds}s on connect"})
        raise SystemExit(f"텔레그램이 요청을 제한했습니다 ({e.seconds}초 대기 필요). 잠시 후 다시 시도하세요.")
    except EOFError:
        # 세션 파일은 있지만 서버 쪽에서 로그아웃된 경우 telethon 이 start() 로
        # 넘어가 input() 으로 전화번호를 묻는다 — 작업 스케줄러에선 EOFError 로 죽는다.
        _log({"level": "error", "msg": "session revoked — interactive login required"})
        raise SystemExit(
            "텔레그램 세션이 만료·해지되어 재로그인이 필요합니다. "
            "telegram_login_setup.py 를 다시 실행하세요."
        )
    except UnauthorizedError as e:
        _log({"level": "error", "msg": f"unauthorized: {e.__class__.__name__}"})
        raise SystemExit(
            "텔레그램 인증이 거부되었습니다(세션 무효). "
            "telegram_login_setup.py 를 다시 실행하세요."
        )

    not_checked_titles = [t for t in all_titles if t not in checked_titles]

    out_path = _write_digest(collected)
    digest_manifest.write_manifest(
        digest_manifest.manifest_path(_STATE_DIR, datetime.now().strftime("%Y-%m-%d")),
        collected=checked_titles,
        excluded=excluded_titles,
        not_checked=not_checked_titles,
    )
    _save_last_ids(last_ids)

    _log({
        "level": "error" if flood_wait_seconds else "info",
        "channels_collected": len(collected),
        "channels_checked": len(checked_titles),
        "channels_excluded": excluded_titles,
        "channels_not_checked": not_checked_titles,
        "unmatched_exclusions": unmatched_exclusions,
        "flood_wait_seconds": flood_wait_seconds,
        "output": str(out_path),
    })

    print(f"완료: {out_path} (채널 {len(checked_titles)}개 조회, {len(collected)}개에서 새 글 수집)")
    if excluded_titles:
        print("제외:", ", ".join(excluded_titles))
    if not_checked_titles:
        print(f"조회 못 함 {len(not_checked_titles)}개:", ", ".join(not_checked_titles))
    if unmatched_exclusions:
        print("제외 목록에 있으나 채널을 찾지 못함 (개명 가능성):", ", ".join(unmatched_exclusions))
    if flood_wait_seconds:
        print(f"주의: 텔레그램 요청 제한으로 중간에 멈췄습니다 ({flood_wait_seconds}초 대기 필요).")


if __name__ == "__main__":
    main()
