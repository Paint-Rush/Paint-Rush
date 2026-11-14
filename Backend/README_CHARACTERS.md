# 캐릭터 및 궁극기 메시지 규격

현재 캐릭터와 궁극기는 테스트용 데이터만 등록되어 있습니다. 실제 능력, 스탯, 궁극기 효과, 비용, 쿨타임은 아직 구현되지 않았습니다.

## 캐릭터 목록

`GET /characters`

```json
{
  "characters": [
    {"character_id": "character_01", "name": "테스트 캐릭터 1", "ultimate_id": "ultimate_01", "ultimate_name": "테스트 궁극기 1"},
    {"character_id": "character_02", "name": "테스트 캐릭터 2", "ultimate_id": "ultimate_02", "ultimate_name": "테스트 궁극기 2"}
  ]
}
```

캐릭터는 `app/characters.py`의 `CHARACTERS`에 등록합니다. 항목을 추가하면 API, 매칭, 궁극기 사용에 바로 반영됩니다.

## 매칭 시 캐릭터 선택

```json
{"type": "queue_join", "character_id": "character_01"}
```

- `character_id`를 생략하면 `character_01`이 적용됩니다.
- 등록되지 않은 값이면 `unknown_character` 오류를 받고 대기열에 등록되지 않습니다.
- 이미 대기 중이면 `already_in_queue` 오류를 받고 기존 선택과 대기 순서가 유지됩니다.
- 대기 취소 또는 대기 중 연결 종료 시 선택 정보가 삭제됩니다.
- 매칭 후에는 캐릭터를 바꿀 수 없고, 다음 경기에서 다시 선택합니다.

`match_found`와 `room_state`의 `players` 항목에 다음 필드가 포함됩니다.

```json
{"character_id": "character_01", "character_name": "테스트 캐릭터 1", "ultimate_id": "ultimate_01", "ultimate_name": "테스트 궁극기 1"}
```

`room_state`의 `my_ultimate_used`는 이번 경기에서 본인이 궁극기를 사용했는지 나타냅니다. 재접속해도 유지되고 새 경기에서는 `false`로 초기화됩니다.

## 궁극기 사용

```json
{"type": "ultimate_use"}
{"type": "ultimate_use", "target_x": 7, "target_y": 6}
```

궁극기 ID는 보내지 않습니다. 서버가 플레이어의 캐릭터로 궁극기를 결정합니다. PLAYING 또는 DEATHMATCH 상태에서 생존 중일 때 경기당 한 번 사용할 수 있고 코스트를 소비하지 않습니다. 실패한 요청은 사용 횟수를 소비하지 않습니다.

| ultimate_id | 효과 | 목표 |
| --- | --- | --- |
| ultimate_01 (컬러 버스트) | 내 위치 중심 3×3의 NORMAL 타일을 모두 내 색으로 변경 (상대 타일 포함) | 불필요, 보내도 무시 |
| ultimate_02 (컬러 스틸) | 목표 중심 3×3에서 상대가 점령한 NORMAL 타일만 내 색으로 변경 | 내 위치에서 맨해튼 거리 3 이내. 생략하면 내 위치 |

- GLASS/BLOCKED 타일은 변경되지 않습니다. 맵 밖 범위는 잘립니다.
- 위치, HP, 손패, 카드 대기 큐, 코스트는 바뀌지 않습니다.
- 변경된 타일은 점령 수와 `total_painted` 통계에 반영됩니다.

성공 시 룸 참가자 전원이 받습니다.

```json
{
  "type": "ultimate_activated", "room_id": "...", "user_id": 1,
  "character_id": "character_02", "ultimate_id": "ultimate_02",
  "target_x": 7, "target_y": 6,
  "affected_tiles": [[6, 5], [7, 5], [8, 5], [6, 6], [7, 6], [8, 6], [6, 7], [7, 7], [8, 7]],
  "changed_tiles": [[7, 6]]
}
```

`affected_tiles`는 효과 범위, `changed_tiles`는 실제로 소유권이 바뀐 좌표입니다. 바뀐 타일이 있으면 이어서 기존 `tiles_updated`가 전송됩니다. 소유권 반영은 `tiles_updated` 기준으로 처리하세요.

실패 시 기존 `error` 형식으로 응답합니다.

| code | 상황 |
| --- | --- |
| not_in_room | 참가 중인 룸이 없음 |
| game_not_playing | READY, FINISHED 또는 경기 시간 300초 이후 |
| player_knocked_out | 기절 상태 |
| player_dead | 사망 상태 |
| unknown_character | 등록되지 않은 캐릭터 |
| ultimate_already_used | 이번 경기에서 이미 사용 |
| unknown_ultimate | 효과가 등록되지 않은 궁극기 |
| map_unavailable | 맵 데이터를 불러올 수 없음 |
| invalid_ultimate_target | 목표 좌표가 정수가 아니거나 맵 밖이거나 한쪽만 보냄 |
| ultimate_out_of_range | 목표가 사용 가능 거리 밖 |

## 이벤트 로그

- `character_selected`: 매칭으로 캐릭터가 확정될 때 플레이어마다 한 번 기록합니다. `user_id`, `character_id`, `ultimate_id`
- `ultimate_activated`: 사용 성공 시에만 기록합니다. `user_id`, `character_id`, `ultimate_id`

실패한 요청과 재접속 시 상태 재전송은 기록하지 않습니다.
