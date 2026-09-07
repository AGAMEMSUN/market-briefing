---
name: brief-dig
description: 특정 토픽 하나를 깊게 파고든다. "OO 이슈 자세히", "그 토픽 더 알아봐" 같은 요청에 사용한다.
---

# 토픽 심층 분석

인자로 받은 토픽(또는 사용자가 지목한 이슈)에 대해 `briefing-section-writer`
서브에이전트를 **웹 검색 상한을 15회로 올려** 한 개만 띄운다.

`scripts/.state/topics.json` 이 있으면 거기서 해당 토픽의 `message_refs` 를 가져오고,
없으면 `brief` 스킬의 1~3단계를 먼저 실행한다.

결과는 대시보드에 반영하지 않고 채팅에 정리해 보여준다. 대시보드까지 갱신하려면
`/brief` 를 쓴다.
