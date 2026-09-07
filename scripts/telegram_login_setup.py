"""최초 1회, 반드시 사용자가 직접 실제 터미널에서 실행한다.

본인 텔레그램 계정으로 로그인해 세션 파일(.state/session.session)을 만든다.
전화번호 -> 텔레그램 앱으로 온 인증 코드 -> (2단계 인증을 켜뒀다면) 비밀번호
순서로 콘솔에서 직접 입력받는다. Claude Code 툴 호출로는 실행할 수 없다 —
실시간 대화형 입력이 필요해서다.

세션 파일은 텔레그램 로그인 세션 자체이므로 .gitignore 에 등록돼 있고, 로컬
PC 밖으로 절대 옮기거나 커밋하면 안 된다.
"""
from __future__ import annotations

import os
from pathlib import Path

from telethon.sync import TelegramClient

_ROOT = Path(__file__).resolve().parents[1]
_STATE_DIR = Path(__file__).resolve().parent / ".state"
_SESSION_PATH = _STATE_DIR / "session"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def main() -> None:
    _load_dotenv(_ROOT / ".env")
    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise SystemExit(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH가 .env에 없습니다. "
            "my.telegram.org에서 발급받아 저장소 루트 .env에 먼저 추가하세요."
        )

    _STATE_DIR.mkdir(parents=True, exist_ok=True)

    with TelegramClient(str(_SESSION_PATH), int(api_id), api_hash) as client:
        me = client.get_me()
        print(f"로그인 완료: {me.first_name} (id={me.id})")
        print(f"세션 파일 저장됨: {_SESSION_PATH}.session")
        print("이제 telegram_digest.py 를 정오 작업 스케줄러에 등록하면 됩니다.")


if __name__ == "__main__":
    main()
