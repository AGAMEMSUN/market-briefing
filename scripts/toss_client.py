"""토스증권 Open API 클라이언트 — 지수·국채 시세 조회.

야후 파이낸스의 코스피·코스닥 시계열에 비정상 변동이 섞여 있어(실측: 코스피
일간 최대 +17.9%, 64거래일 중 18일이 5% 초과) 스파크라인을 못 그렸다. 토스는
KRX 원천 데이터라 이 문제가 없다.

토스 심볼 카탈로그는 8종뿐이다 — KOSPI, KOSDAQ, KR_BOND_{2,3,5,10,20,30}Y.
S&P500·나스닥·VIX·미 국채는 없으므로 그쪽은 야후를 그대로 쓴다.

인증: OAuth 2.0 client_credentials. `.env` 의 TOSS_CLIENT_ID/TOSS_CLIENT_SECRET
을 읽는다. 시크릿은 절대 커밋하지 않는다(.gitignore 에 .env 등록됨).
"""
from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ENV_PATH = _ROOT / ".env"
_TOKEN_CACHE = _ROOT / "scripts" / ".state" / "toss_token.json"

BASE_URL = "https://openapi.tossinvest.com"

# 심볼 카탈로그(8종). 이 밖의 심볼은 400 unsupported-symbol 로 거절된다.
INDEX_SYMBOLS = ("KOSPI", "KOSDAQ")
BOND_SYMBOLS = ("KR_BOND_2Y", "KR_BOND_3Y", "KR_BOND_5Y",
                "KR_BOND_10Y", "KR_BOND_20Y", "KR_BOND_30Y")
SYMBOLS = INDEX_SYMBOLS + BOND_SYMBOLS


class TossError(RuntimeError):
    """토스 API 호출 실패. 지표 하나의 실패가 스냅샷 전체를 막지 않게 잡아 쓴다."""


def load_env(path: Path = _ENV_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


def credentials(env: dict[str, str] | None = None) -> tuple[str, str] | None:
    """client_id·client_secret 이 둘 다 있을 때만 돌려준다."""
    env = load_env() if env is None else env
    cid = env.get("TOSS_CLIENT_ID", "").strip()
    secret = env.get("TOSS_CLIENT_SECRET", "").strip()
    return (cid, secret) if cid and secret else None


def available() -> bool:
    return credentials() is not None


def _read(response) -> dict:
    raw = response.read()
    if raw[:2] == b"\x1f\x8b":  # 토스는 오류 응답을 gzip 으로 보낸다
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def _request(url: str, *, data: bytes | None = None, headers: dict | None = None,
             timeout: int = 20) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        return _read(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        raise TossError(f"HTTP {exc.code}: {raw.decode('utf-8', 'replace')[:300]}") from exc
    except urllib.error.URLError as exc:
        raise TossError(f"연결 실패: {exc.reason}") from exc


def fetch_token(client_id: str, client_secret: str) -> dict:
    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }).encode()
    return _request(f"{BASE_URL}/oauth2/token", data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    })


def get_token(*, now: datetime | None = None, cache_path: Path = _TOKEN_CACHE) -> str:
    """토큰을 발급하고 만료 60초 전까지 디스크에 캐시한다.

    /brief 한 번에 지표 여러 개를 조회하므로 매 호출마다 재발급하면 낭비다.
    """
    now = now or datetime.now(timezone.utc)
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if datetime.fromisoformat(cached["expires_at"]) > now:
                return cached["access_token"]
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            pass  # 캐시가 이상하면 다시 받는다

    creds = credentials()
    if creds is None:
        raise TossError("TOSS_CLIENT_ID·TOSS_CLIENT_SECRET 이 .env 에 없습니다.")
    payload = fetch_token(*creds)
    token = payload["access_token"]
    expires_at = now + timedelta(seconds=max(int(payload.get("expires_in", 600)) - 60, 30))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"access_token": token, "expires_at": expires_at.isoformat()}),
        encoding="utf-8")
    return token


def _auth_get(path: str, params: dict, token: str) -> dict:
    url = f"{BASE_URL}{path}?{urllib.parse.urlencode(params)}"
    payload = _request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    if "result" not in payload:  # 성공 응답은 항상 result envelope 을 갖는다
        raise TossError(f"예상 밖 응답: {json.dumps(payload, ensure_ascii=False)[:200]}")
    return payload["result"]


def candles(symbol: str, count: int = 70, token: str | None = None) -> list[tuple[str, float]]:
    """일봉 종가를 오래된 순으로 [(YYYY-MM-DD, close)] 형태로 돌려준다.

    market_snapshot 의 야후 fetch 와 같은 시그니처라 그대로 갈아끼울 수 있다.
    """
    if symbol not in SYMBOLS:
        raise TossError(f"심볼 카탈로그에 없는 심볼: {symbol}")
    token = token or get_token()
    result = _auth_get(f"/api/v1/market-indicators/{symbol}/candles",
                       {"interval": "1d", "count": min(count, 200)}, token)
    rows = []
    for candle in result.get("candles", []):
        stamp = candle.get("timestamp") or ""
        close = candle.get("closePrice")
        if not stamp or close is None:
            continue
        rows.append((stamp[:10], float(close)))
    rows.sort(key=lambda r: r[0])  # 응답이 최신순이어도 오래된 순으로 맞춘다
    return rows


def exchange_rate(base: str = "USD", quote: str = "KRW", token: str | None = None) -> dict:
    token = token or get_token()
    return _auth_get("/api/v1/exchange-rate",
                     {"baseCurrency": base, "quoteCurrency": quote}, token)


def _hint(message: str) -> str:
    """토스 오류 문구를 사람이 바로 조치할 수 있는 안내로 바꾼다."""
    if "IP address not allowed" in message:
        return ("현재 공인 IP 가 허용 목록에 없습니다.\n"
                "토스증권 → 설정 → Open API → 허용 IP 관리 에서 추가하세요.\n"
                "현재 IP 확인: curl https://api.ipify.org")
    if "client_secret" in message:
        return ("client_secret 이 틀렸습니다. 시크릿은 발급 시 한 번만 표시되므로\n"
                "확인이 안 되면 Open API 화면에서 재발급받아 .env 를 갱신하세요.\n"
                "(재발급하면 client_id 도 함께 바뀔 수 있습니다.)")
    if "invalid_client" in message:
        return "client_id 또는 client_secret 이 비어 있거나 형식이 잘못됐습니다. .env 를 확인하세요."
    return ""


def main() -> None:
    """연결 점검용 — python scripts/toss_client.py"""
    if not available():
        raise SystemExit("TOSS_CLIENT_ID·TOSS_CLIENT_SECRET 을 .env 에 넣어주세요.")
    try:
        token = get_token()
        print(f"토큰 발급 OK ({token[:16]}...)")
        for symbol in ("KOSPI", "KOSDAQ", "KR_BOND_10Y"):
            rows = candles(symbol, count=5, token=token)
            print(f"  {symbol:12} {len(rows)}봉  최근={rows[-1] if rows else '없음'}")
        rate = exchange_rate(token=token)
        print(f"  USD/KRW      매매기준율={rate.get('midRate')} ({rate.get('rateChangeType')})")
    except TossError as exc:
        # 스택트레이스는 이 상황에서 알려주는 게 없다 — 원인과 조치만 남긴다.
        raise SystemExit(f"토스 API 호출 실패\n  {exc}\n\n{_hint(str(exc))}".rstrip()) from None


if __name__ == "__main__":
    main()
