import pytest

from app import websocket
from app.attacks import check_attack, hit_players, resolve_attack
from app.game_engine import RESPAWN_SECONDS, advance, check_card, use_ultimate
from app.game_room import MAX_HP, RoomStatus
from tests.test_action_events import collect_until, start_game, users
from tests.test_attacks import own
from tests.test_card_use import snapshot
from tests.test_game_engine import client, now
from tests.test_movement import grid, room_at, set_position
from tests.test_websocket import WS_URL, auth, clear_state


@pytest.fixture(autouse=True)
def clear_rooms():
    clear_state()
    yield
    clear_state()


def hit(room, card_id, target, map_data=None, user_id=1, at=10):
    map_data = map_data or grid()
    area = check_attack(room, user_id, card_id, *target, map_data, at)
    resolve_attack(room, user_id, card_id, area, map_data, at)
    return hit_players(room, user_id, area, at)


def knock_out(room, at=10):
    room.players[2].position = (5, 6)
    for _ in range(MAX_HP):
        messages = hit(room, "attack_slash", (5, 6), at=at)
    return messages


def test_players_start_with_full_hp():
    room = room_at()
    for player in room.players.values():
        assert player.hp == MAX_HP == 3
        assert player.knocked_out_at is None
        assert player.alive is True


def test_slash_damages_target_and_keeps_painting():
    room = room_at()
    room.players[2].position = (5, 6)
    messages = hit(room, "attack_slash", (5, 6))
    assert room.players[2].hp == 2
    assert room.players[1].hp == 3
    assert room.tile_owners[6][5] == 1
    assert messages == [{
        "type": "player_damaged",
        "room_id": str(room.room_id),
        "user_id": 2,
        "attacker_id": 1,
        "hp": 2,
        "max_hp": 3,
    }]


def test_slash_misses_player_outside_target():
    room = room_at()
    room.players[2].position = (5, 7)
    assert hit(room, "attack_slash", (5, 6)) == []
    assert room.players[2].hp == 3


def test_three_hits_knock_out_once_and_count_stats():
    room = room_at()
    messages = knock_out(room)
    victim, attacker = room.players[2], room.players[1]
    assert victim.hp == 0 and victim.alive is False and victim.knocked_out_at == 10
    assert [m["type"] for m in messages] == ["player_damaged", "player_knocked_out"]
    assert messages[1] == {
        "type": "player_knocked_out",
        "room_id": str(room.room_id),
        "user_id": 2,
        "attacker_id": 1,
        "respawn_remaining_seconds": RESPAWN_SECONDS,
    }
    assert (attacker.kills, victim.deaths) == (1, 1)

    assert hit(room, "attack_slash", (5, 6)) == []
    assert hit(room, "attack_bomb", (5, 6)) == []
    assert victim.hp == 0 and victim.knocked_out_at == 10
    assert (attacker.kills, victim.deaths) == (1, 1)


def test_bomb_damages_each_opponent_once_and_not_attacker():
    room = room_at(user_ids=(1, 2, 3))
    room.players[2].position = (6, 6)
    room.players[3].position = (7, 7)
    messages = hit(room, "attack_bomb", (6, 6), map_data=grid(glass=[(6, 6)]))
    assert [(m["type"], m["user_id"]) for m in messages] == [("player_damaged", 2), ("player_damaged", 3)]
    assert [room.players[uid].hp for uid in (1, 2, 3)] == [3, 2, 2]


def test_bomb_misses_player_outside_area():
    room = room_at()
    room.players[2].position = (8, 8)
    assert hit(room, "attack_bomb", (6, 6)) == []
    assert room.players[2].hp == 3


def test_knocked_out_player_cannot_use_cards_or_ultimate():
    room = room_at()
    knock_out(room)
    victim = room.players[2]
    victim.position = (9, 9)
    before = snapshot(victim)
    for index in range(len(victim.hand)):
        with pytest.raises(ValueError, match="player_knocked_out"):
            check_card(room, 2, index, 10)
    with pytest.raises(ValueError, match="player_knocked_out"):
        use_ultimate(room, 2, 10)
    assert snapshot(victim) == before
    assert victim.ultimate_used is False


def test_respawn_after_ten_seconds_keeps_tiles_hand_and_cost():
    room = room_at()
    own(room, 2, (10, 10), (11, 10))
    knock_out(room)
    victim = room.players[2]
    hand = list(victim.hand)
    tiles = [row[:] for row in room.tile_owners]

    advance(room, 19.9)
    assert victim.knocked_out_at == 10 and victim.position == (5, 6)
    assert room.tile_counts[2] == 2

    events = advance(room, 20)
    respawned = [m for _, m in events if m["type"] == "player_respawned"]
    x, y = victim.spawn
    assert respawned == [{
        "type": "player_respawned",
        "room_id": str(room.room_id),
        "user_id": 2,
        "hp": 3,
        "max_hp": 3,
        "x": x,
        "y": y,
    }]
    assert victim.hp == 3 and victim.alive is True and victim.knocked_out_at is None
    assert victim.position == victim.spawn
    assert victim.hand == hand
    assert room.tile_owners == tiles and room.tile_counts[2] == 2
    assert check_card(room, 2, 0, 20) == victim.hand[0]
    assert all(m["type"] != "player_respawned" for _, m in advance(room, 30))


def test_no_respawn_after_finish():
    room = room_at()
    knock_out(room, at=305)
    events = advance(room, 315)
    assert room.status is RoomStatus.FINISHED
    assert all(m["type"] != "player_respawned" for _, m in events)
    assert room.players[2].knocked_out_at == 305 and room.players[2].hp == 0


def test_knockout_over_websocket_and_reconnect_restores_state(client, users, now):
    a, b = users[0], users[1]
    with client.websocket_connect(WS_URL) as ws1:
        with client.websocket_connect(WS_URL) as ws2:
            room = start_game(ws1, ws2, users, now)
            set_position(room, a, (5, 5))
            set_position(room, b, (5, 6))
            with websocket.state_lock:
                room.players[b].hp = 1
            ws1.send_json({"type": "card_use", "hand_index": 1, "target_x": 5, "target_y": 6})
            sent = collect_until(ws1, "player_knocked_out")
            assert [m["type"] for m in sent][-2:] == ["player_damaged", "player_knocked_out"]
            assert collect_until(ws2, "player_knocked_out")[-1]["user_id"] == b
            assert (room.players[a].kills, room.players[b].deaths) == (1, 1)

            before = snapshot(room.players[b])
            ws2.send_json({"type": "card_use", "hand_index": 0, "target_x": 5, "target_y": 7})
            assert collect_until(ws2, "error")[-1]["code"] == "player_knocked_out"
            ws2.send_json({"type": "ultimate_use"})
            assert collect_until(ws2, "error")[-1]["code"] == "player_knocked_out"
            assert snapshot(room.players[b]) == before

        with client.websocket_connect(WS_URL) as ws3:
            assert auth(ws3, b)["type"] == "auth_success"
            state = ws3.receive_json()
            assert state["type"] == "room_state"
            info = next(p for p in state["players"] if p["user_id"] == b)
            assert info["hp"] == 0 and info["max_hp"] == 3
            assert info["knocked_out"] is True and info["alive"] is False
            assert info["respawn_remaining_seconds"] == RESPAWN_SECONDS

            now[0] = 20
            respawned = collect_until(ws3, "player_respawned")[-1]
            assert respawned["user_id"] == b and respawned["hp"] == 3
            assert [respawned["x"], respawned["y"]] == list(room.players[b].spawn)
            assert collect_until(ws1, "player_respawned")[-1] == respawned
            ws3.send_json({"type": "room_state"})
            info = next(p for p in collect_until(ws3, "room_state")[-1]["players"] if p["user_id"] == b)
            assert info["hp"] == 3 and info["knocked_out"] is False and info["respawn_remaining_seconds"] == 0
