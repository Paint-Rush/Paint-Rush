# 게임 프론트 연결 규격 (서버 담당자와 합의할 초안)

2026-10-28 확인한 `main`(`f94b798`)에는 인증 REST API, Python `GameRoom`/`PlayerState`와 내부 매칭 큐(`join_queue`, `cancel_queue`, `try_match`)가 있습니다. `try_match`의 기본 인원은 2명이며 프론트는 2~4명을 모두 표시합니다. 하지만 게임용 HTTP/WebSocket 라우트는 아직 등록되어 있지 않습니다. 아래 WebSocket 주소와 이벤트는 **기존 서버 API가 아니라 연결을 위한 제안**입니다. 백엔드를 구현했다고 간주하지 마세요. 서버 코드와 DB는 이번 변경에서 수정하지 않습니다.

## 연결과 권한

기본 경로: `/api/v1/game/ws`. 로비의 연결 설정에서 같은 출처의 경로로 변경할 수 있습니다. HTTPS에서는 WSS를 사용합니다. 토큰은 URL·브라우저 저장소에 기록하지 않고 첫 메시지로 보냅니다. 서버는 토큰을 검증한 다음 가입 가능한 플레이어 ID를 결정해야 합니다. 프로덕션에서는 인증 메시지와 비밀번호·토큰을 로그에 남기지 마세요.

```text
client → {"type":"authenticate","access_token":"JWT"}
server → {"type":"authenticated"}
client → {"type":"queue.join","character_id":"painter","deck":["move","attack","defend","dash","splash","shield"]}
server → {"type":"queued"}
```

`queued`를 받은 시점부터 대기 시간이 표시됩니다. 클라이언트의 대기 시간은 화면 표시용이고 동률 판정에 사용하는 큐 순서는 서버가 보관해야 합니다. 10초간 초기 응답이 없거나 연결이 끊기면 오류를 표시하며 체험 모드로 몰래 전환하지 않습니다.

```text
client → {"type":"queue.cancel"}
client → {"type":"card.use","room_id":"UUID","card_id":"move","target":[1,0]}
server → {"type":"error","message":"카드 범위 밖입니다."}
```

카드 대상이 미선택이면 `target`은 `null`입니다. 카드 요청은 한 번에 한 개만 보내고, 새 스냅샷 또는 오류가 오면 잠금을 풉니다. 5초간 카드 응답이 없으면 안내합니다. 카드를 거부하는 일반 `error` 메시지는 연결 종료로 취급하지 않습니다.

카드 ID·이름·효과는 실제 카드 기획이 없으므로 임시 예시입니다. **서버는 현재 위치·생존·턴 상태·시간·코스트·덱·대상 범위를 검증한 후에만 상태를 바꿔야 합니다.** 프론트의 비활성화는 보안 검증을 대신하지 않습니다. 정의되지 않은 카드 ID는 사용 불가로 표시합니다. 실제 규격이 정해지면 `src/game/model.ts`의 `CARDS` 및 `src/game/transport.ts`를 맞춥니다.

## 전체 스냅샷

서버가 상태를 바꿀 때 `{"type":"snapshot","room": ...}` 형태로 완전한 스냅샷을 보냅니다. 부분 패치는 아직 지원하지 않습니다. 순서 보존은 단일 WebSocket 연결을 기준으로 합니다.

`room` 필드:

| 필드 | 형식 / 의미 |
|---|---|
| `room_id` | UUID 문자열 |
| `status` | `READY`, `PLAYING`, `DEATHMATCH`, `FINISHED` |
| `self_id` | 인증된 내 계정 ID (양의 정수) |
| `map` | 아래의 타일 JSON 전체. 서버가 결정한 맵 |
| `player_order` | 먼저 매칭한 순서의 서로 다른 계정 ID 2~4개 |
| `players` | ID를 키로 하는 `PlayerState` 객체 |
| `tile_owners` | `[y][x]`, `null` 또는 참가자 ID |
| `tile_counts` | ID별 현재 칠해진 타일 수 |
| `remaining_ms` | 본 게임 남은 시간, 0~300000 |
| `ready_remaining_ms` | 준비 남은 시간, 0~10000 |
| `winner_id` | `FINISHED`에서 필수. 상위 1명의 계정 ID |

`players[id]`의 기존 룸 필드:

```json
{
  "user_id": 1,
  "character_id": "painter",
  "color": "RED",
  "spawn": [0, 0],
  "position": [0, 0],
  "connected": true,
  "alive": true,
  "deck": ["move", "attack", "defend", "dash", "splash", "shield"],
  "cost": 6,
  "cost_used": 0,
  "cards_used": 0,
  "kills": 0,
  "total_painted": 0,
  "username": "페인터",
  "level": 1,
  "hand": ["move", "attack", "defend", "dash"],
  "respawn_remaining_ms": 0
}
```

`username`, `level`, `hand`, `respawn_remaining_ms`는 프론트 표시를 위해 추가 제안하는 필드입니다. `username`과 `level`이 없으면 ID 기반 이름과 Lv.1이 표시됩니다. `hand`가 없으면 카드가 표시되지 않습니다. 색상은 기존 룸과 같은 `RED/BLUE/GREEN/YELLOW`이며 참가자별로 달라야 합니다. 최종 결과에서 `xp_gained`를 제공하면 경험치를 표시하고, 없으면 “서버 집계 대기”로 표시합니다. 클라이언트가 경험치나 레벨을 임의로 확정하지 않습니다.

서버 상태만이 실제 게임의 기준입니다. 준비·게임·사망 남은 시간은 받은 값에서 경과 시간을 빼서 화면에만 표시합니다. 0초가 되어도 클라이언트가 리스폰·단계 변경·승패를 확정하지 않습니다. 종료된 게임에서는 카드 버튼을 숨기며, 타이머가 0이면 새로운 카드 사용을 막습니다. 승자는 `winner_id`를 사용하며 결과 테이블의 동률 정렬에는 지침의 여섯 기준을 사용합니다.

2~4명의 위치·색상·타일·플레이어 ID·유효한 맵·종료 시 우승자 등을 수신 경계에서 검증합니다. 잘못된 데이터는 마지막 확인 화면을 보존하고 오류로 안내합니다. 네트워크 재접속·이탈 시 패배 처리·로그 파일 저장·NPC AI·매치 결과 DB 저장은 서버 구현 범위이며 아직 연결된 기능이 아닙니다.

## 타일 JSON

실제 예시: `src/game/maps/crossroads.json`. 기본은 20×20이며 렌더러는 2~100 크기의 직사각 맵도 지원합니다. 업로드 파일은 1MB 이하, 모든 행과 열의 크기는 선언과 일치해야 합니다. 타일 정의는 확장할 수 있고, 미등록 ID는 거부합니다. 네 꼭짓점은 스폰 가능한 타일이어야 합니다.

```json
{
  "version": 1,
  "id": "example",
  "name": "작은 광장",
  "width": 2,
  "height": 2,
  "tiles": [[0, 0], [0, 0]],
  "legend": {"0": {"name": "바닥", "color": "#e7e3d7", "walkable": true}}
}
```

프론트가 불러온 로컬 JSON은 체험 모드용입니다. 실제 경기 맵은 서버의 `map`으로 교체되므로 업로드로 서버 경기 내용을 바꿀 수 없습니다. JSON은 데이터를 파싱할 뿐 코드나 외부 리소스를 실행하지 않습니다. 캐릭터·NPC는 이 맵 포맷에 포함하지 않고 룸 데이터의 별도 레이어로 그립니다.

## 체험 모드와 테스트

`DemoClock`은 UI 검증용입니다. 3초 매칭 → 10초 준비 → 5분 게임 → 4분 경과 시 데스매치 → 결과로 이어집니다. 코스트는 0.86초/1, 데스매치는 0.45초/1이며 기존 룸처럼 시작 6·최대 10입니다. 사망 체험은 10초 후 원래 스폰으로 복귀합니다. 임시 상대의 움직임과 카드 효과는 실제 게임 규칙이나 NPC 구현이 아닙니다. 전적·레벨을 저장하지 않습니다.

`npm test`는 파싱·타이머·승패 정렬·카메라 독립성·API 요청을 검증합니다. `npm run test:ui`는 HTML 파일과 브라우저의 모의 HTTP/WebSocket 응답을 사용하며 서버를 실행하지 않습니다. 실제 백엔드와 연결됐다는 증거를 대신하지 않습니다.
