## WebSocket연결 및 게임 나갓다가 재접속 시 처리
## 나가면 그냥 그사람 없이 진행하고 다시 들어오면 들어온 시점부터 플레이(클래시로얄 맹키로)

import asyncio
import json
import logging
import threading
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from starlette.concurrency import run_in_threadpool

from app import game_engine, matchmaking
from app.auth import get_token_user_id
from app.game_room import MAP_SIZE, GameRoom, RoomStatus, get_player_room, get_room, set_player_connected
from app.maps import load_map

AUTH_TIMEOUT = 5
CARD_ERRORS = {
    "not_in_room": "참가 중인 게임 룸이 없습니다.",
    "game_not_playing": "게임 진행 중에만 카드를 사용할 수 있습니다.",
    "player_dead": "사망 상태에서는 카드를 사용할 수 없습니다.",
    "deck_not_initialized": "덱이 초기화되지 않았습니다.",
    "invalid_hand_index": "잘못된 손패 인덱스입니다.",
    "unknown_card": "등록되지 않은 카드입니다.",
    "insufficient_cost": "코스트가 부족합니다.",
}
SESSION_REPLACED = 4000
TIMER_STEP = 0.05

logger = logging.getLogger(__name__)
router = APIRouter()
connections: dict[int, WebSocket] = {}
timers: dict[uuid.UUID, asyncio.Task] = {}
state_lock = threading.Lock()


def parse(text: str) -> dict | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def error(code: str, message: str) -> dict:
    return {"type": "error", "code": code, "message": message}


def is_connected(user_id: int) -> bool:
    return user_id in connections


async def send_to(user_id: int, message: dict) -> bool:
    websocket = connections.get(user_id)
    if websocket is None:
        return False
    try:
        await websocket.send_json(message)
    except Exception:
        return False
    return True


async def send_to_room(room: GameRoom, message: dict, exclude: int | None = None) -> None:
    for user_id in room.player_order:
        if user_id != exclude:
            await send_to(user_id, message)


def match_found(room: GameRoom) -> dict:
    return {
        "type": "match_found",
        "room_id": str(room.room_id),
        "map_id": room.map_id,
        "players": [
            {
                "user_id": player.user_id,
                "color": player.color.value,
                "spawn": list(player.spawn),
                "character_id": player.character_id,
            }
            for player in map(room.players.get, room.player_order)
        ],
    }


def room_state(room: GameRoom, user_id: int, map_data: dict) -> dict:
    return {
        "type": "room_state",
        "room_id": str(room.room_id),
        "status": room.status.value,
        "map_id": room.map_id,
        "map": {"width": map_data["width"], "height": map_data["height"], "tiles": map_data["tiles"]},
        "players": [
            {
                "user_id": player.user_id,
                "color": player.color.value,
                "character_id": player.character_id,
                "spawn": list(player.spawn),
                "position": list(player.position),
                "alive": player.alive,
                "connected": player.connected,
                "tile_count": room.tile_counts.get(player.user_id, 0),
            }
            for player in map(room.players.get, room.player_order)
        ],
        "my_cost": room.players[user_id].cost,
        "my_hand": list(room.players[user_id].hand),
        "my_next_card": next(iter(room.players[user_id].draw_queue), None),
        "tile_owners": [row[:] for row in room.tile_owners],
        "started_at": room.started_at.isoformat() if room.started_at else None,
        **game_engine.timer_state(room, game_engine.clock()),
    }


async def build_room_state(user_id: int) -> dict:
    room = get_player_room(user_id)
    if not room:
        return error("not_in_room", "참가 중인 게임 룸이 없습니다.")
    try:
        map_data = await run_in_threadpool(load_map, room.map_id)
    except (FileNotFoundError, ValueError):
        return error("map_unavailable", "맵 데이터를 불러올 수 없습니다.")
    if map_data["width"] != MAP_SIZE or map_data["height"] != MAP_SIZE:
        return error("map_unavailable", "맵 크기가 게임 룸과 일치하지 않습니다.")
    with state_lock:
        return room_state(room, user_id, map_data)


async def run_timer(room: GameRoom) -> None:
    try:
        while True:
            await asyncio.sleep(TIMER_STEP)
            with state_lock:
                if get_room(room.room_id) is not room:
                    return
                events = game_engine.advance(room, game_engine.clock())
                finished = room.status is RoomStatus.FINISHED
            for user_id, message in events:
                if user_id is None:
                    await send_to_room(room, message)
                else:
                    await send_to(user_id, message)
            if finished:
                return
    except Exception:
        logger.exception("game timer failed: %s", room.room_id)
    finally:
        timers.pop(room.room_id, None)


def start_timer(room: GameRoom) -> dict | None:
    ready = game_engine.start(room, game_engine.clock())
    if ready:
        timers[room.room_id] = asyncio.create_task(run_timer(room))
    return ready


async def stop_timers() -> None:
    tasks = list(timers.values())
    timers.clear()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def authenticate(websocket: WebSocket) -> int | None:
    try:
        text = await asyncio.wait_for(websocket.receive_text(), AUTH_TIMEOUT)
    except asyncio.TimeoutError:
        await websocket.close(status.WS_1008_POLICY_VIOLATION, "auth timeout")
        return None
    data = parse(text)
    if not data or data.get("type") != "auth":
        await websocket.send_json(error("auth_required", "먼저 인증해야 합니다."))
        await websocket.close(status.WS_1008_POLICY_VIOLATION, "auth required")
        return None
    token = data.get("token")
    user_id = await run_in_threadpool(get_token_user_id, token) if isinstance(token, str) else None
    if user_id is None:
        await websocket.send_json(error("auth_failed", "유효하지 않은 토큰입니다."))
        await websocket.close(status.WS_1008_POLICY_VIOLATION, "auth failed")
    return user_id


async def connect(user_id: int, websocket: WebSocket) -> None:
    with state_lock:
        old = connections.get(user_id)
        connections[user_id] = websocket
        room = get_player_room(user_id)
        reconnected = bool(room) and not room.players[user_id].connected
        if room:
            set_player_connected(user_id, True)

    if old:
        try:
            await old.send_json({"type": "session_replaced"})
            await old.close(SESSION_REPLACED, "session replaced")
        except Exception:
            pass

    await websocket.send_json({"type": "auth_success", "user_id": user_id})
    if room:
        await websocket.send_json(await build_room_state(user_id))
        if reconnected:
            await send_to_room(room, {"type": "player_reconnected", "user_id": user_id}, exclude=user_id)


async def disconnect(user_id: int, websocket: WebSocket) -> None:
    with state_lock:
        if connections.get(user_id) is not websocket:
            return
        del connections[user_id]
        matchmaking.cancel_queue(user_id)
        room = get_player_room(user_id)
        if room:
            set_player_connected(user_id, False)
    if room:
        await send_to_room(room, {"type": "player_disconnected", "user_id": user_id}, exclude=user_id)


async def queue_join(user_id: int, websocket: WebSocket) -> None:
    with state_lock:
        if get_player_room(user_id):
            reply = error("already_in_game", "이미 게임 중인 유저입니다.")
        elif user_id in matchmaking.waiting_queue:
            reply = error("already_in_queue", "이미 매칭 대기 중인 유저입니다.")
        else:
            matchmaking.join_queue(user_id)
            reply = {
                "type": "queue_waiting",
                "waiting": len(matchmaking.waiting_queue),
                "match_size": matchmaking.MATCH_SIZE,
            }
    await websocket.send_json(reply)
    if reply["type"] == "error":
        return

    with state_lock:
        room = matchmaking.try_match()
        if room:
            for player in room.players.values():
                player.connected = is_connected(player.user_id)
            message = match_found(room)
            ready = start_timer(room)
    if room:
        await send_to_room(room, message)
        await send_to_room(room, ready)


async def queue_cancel(user_id: int, websocket: WebSocket) -> None:
    with state_lock:
        cancelled = matchmaking.cancel_queue(user_id)
    if cancelled:
        await websocket.send_json({"type": "queue_cancelled"})
    else:
        await websocket.send_json(error("not_in_queue", "매칭 대기 중이 아닙니다."))


async def card_use(user_id: int, websocket: WebSocket, data: dict) -> None:
    hand_index = data.get("hand_index")
    code = None
    with state_lock:
        room = get_player_room(user_id)
        try:
            if not room:
                raise ValueError("not_in_room")
            card_id = game_engine.use_card(room, user_id, hand_index, game_engine.clock())
        except ValueError as e:
            code = str(e)
        else:
            player = room.players[user_id]
            used = {"type": "card_used", "user_id": user_id, "card_id": card_id, "hand_index": hand_index}
            hand = {"type": "hand_updated", "hand": list(player.hand), "cost": player.cost}
            cost = game_engine.cost_message(player)
    if code:
        await websocket.send_json(error(code, CARD_ERRORS[code]))
        return
    await send_to_room(room, used)
    await websocket.send_json(hand)
    await websocket.send_json(cost)


async def room_state_request(user_id: int, websocket: WebSocket) -> None:
    await websocket.send_json(await build_room_state(user_id))


HANDLERS = {
    "queue_join": queue_join,
    "queue_cancel": queue_cancel,
    "room_state": room_state_request,
}


@router.websocket("/ws/game")
async def game_socket(websocket: WebSocket):
    await websocket.accept()
    try:
        user_id = await authenticate(websocket)
    except WebSocketDisconnect:
        return
    if user_id is None:
        return

    try:
        await connect(user_id, websocket)
        while True:
            data = parse(await websocket.receive_text())
            event = data.get("type") if data else None
            handler = HANDLERS.get(event) if isinstance(event, str) else None
            if data is None:
                await websocket.send_json(error("invalid_json", "JSON 객체 형식이 아닙니다."))
            elif event == "auth":
                await websocket.send_json(error("already_authenticated", "이미 인증되었습니다."))
            elif event == "card_use":
                await card_use(user_id, websocket, data)
            elif handler:
                await handler(user_id, websocket)
            else:
                await websocket.send_json(error("unknown_type", f"알 수 없는 이벤트입니다: {event!r}"))
    except WebSocketDisconnect:
        pass
    finally:
        await disconnect(user_id, websocket)
