---
name: brief-collect
description: 텔레그램 다이제스트와 시장 지표만 수집하고 합성은 하지 않는다. "수집만 해줘", "데이터만 받아둬" 같은 요청에 사용한다.
---

# 수집 전용

`brief` 스킬의 1~2단계만 실행한다.

```bash
python scripts/telegram_digest.py
python scripts/market_snapshot.py
python scripts/build_index.py
```

각 스크립트의 출력 요약(채널 수·메시지 수·중복그룹 수·실패한 지표)만 채팅에
보고하고 끝낸다. 토픽 클러스터링·섹션 작성·Artifact 발행은 하지 않는다.
자세한 절차와 문제 해결은 `brief` 스킬을 참조한다.
