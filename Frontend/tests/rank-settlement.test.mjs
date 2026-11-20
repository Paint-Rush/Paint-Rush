import assert from 'node:assert/strict';
import test from 'node:test';
import { GameSocket, initialSession } from '../src/services/gameSocket.ts';

const ROOM = '7f9c2b1e-0000-4000-8000-0000000000aa';
const OTHER = '7f9c2b1e-0000-4000-8000-0000000000bb';
const COLORS = ['RED', 'BLUE', 'GREEN', 'YELLOW'];
const SPAWNS = [[0, 0], [0, 19], [19, 0], [19, 19]];

class FakeSocket {
  static created = [];
  readyState = 0;
  sent = [];
  onopen = null;
  onmessage = null;
  onerror = null;
  onclose = null;

  constructor(url) {
    this.url = url;
    FakeSocket.created.push(this);
  }

  send(data) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.readyState = 3;
  }

  open() {
    this.readyState = 1;
    this.onopen?.({});
  }

  receive(message) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  drop() {
    this.readyState = 3;
    this.onclose?.({ code: 1006, reason: '' });
  }
}

function players(count) {
  return Array.from({ length: count }, (_, i) => ({
    user_id: i + 1, color: COLORS[i], spawn: SPAWNS[i], character_id: `character_0${i + 1}`,
    character_name: `테스트 캐릭터 ${i + 1}`, ultimate_id: null, ultimate_name: null,
  }));
}

function roomState(count = 4, status = 'FINISHED') {
  return {
    type: 'room_state', room_id: ROOM, status, map_id: 'default',
    map: { width: 20, height: 20, tiles: Array.from({ length: 20 }, () => Array(20).fill('NORMAL')) },
    players: players(count).map((p) => ({ ...p, position: p.spawn, alive: true, connected: true, tile_count: 0 })),
    my_cost: 6, my_hand: ['move_dash'], my_next_card: null, my_ultimate_used: false,
    tile_owners: Array.from({ length: 20 }, () => Array(20).fill(null)),
    started_at: '2026-11-20T00:00:00+00:00', ready_remaining_seconds: 0, remaining_seconds: 0, max_cost: 10,
  };
}

function gameResult(count = 4) {
  const results = Array.from({ length: count }, (_, i) => ({
    user_id: i + 1, rank: i + 1, is_winner: i === 0, painted_tiles: 10 - i, total_painted: 10, kills: 0, deaths: 0, cards_used: 1, cost_used: 1,
  }));
  return { type: 'game_result', room_id: ROOM, match_id: ROOM, winner_id: 1, results };
}

function settlement(change = {}) {
  return {
    type: 'rank_settlement', room_id: ROOM, match_id: ROOM, applied: true, reason: null, placement: 1,
    rp_before: 1470, placement_rp_change: 30, rp_change: 30, rp_after: 1500,
    tier_before: 'DIAMOND', division_before: 3, tier_after: 'MASTER', division_after: null, ...change,
  };
}

function finished(count = 4) {
  FakeSocket.created = [];
  const session = new GameSocket((url) => new FakeSocket(url), 'ws://localhost:5173/ws/game');
  session.start('jwt-token', 'character_01');
  const socket = FakeSocket.created.at(-1);
  socket.open();
  socket.receive({ type: 'auth_success', user_id: 1 });
  socket.receive({ type: 'match_found', room_id: ROOM, map_id: 'default', players: players(count) });
  socket.receive(roomState(count, 'PLAYING'));
  socket.receive(gameResult(count));
  return { session, socket };
}

test('initial session has no settlement', () => {
  assert.equal(initialSession().rankSettlement, null);
});

test('stores the settlement for the current room', () => {
  const { session, socket } = finished();
  assert.equal(session.getState().rankSettlement, null);
  socket.receive(settlement());
  const state = session.getState().rankSettlement;
  assert.equal(state.status, 'applied');
  assert.equal(state.roomId, ROOM);
  assert.deepEqual([state.settlement.rpChange, state.settlement.tierAfter], [30, 'MASTER']);
  assert.equal(session.getState().warning, null);
  session.close();
});

test('ignores duplicate settlements for the same match', () => {
  const { session, socket } = finished();
  socket.receive(settlement());
  const first = session.getState().rankSettlement;
  socket.receive(settlement());
  socket.receive(settlement({ rp_change: -30, placement_rp_change: -30, rp_after: 1440, tier_after: 'DIAMOND', division_after: 3 }));
  assert.equal(session.getState().rankSettlement, first);
  session.close();
});

test('ignores settlements for other rooms', () => {
  const { session, socket } = finished();
  socket.receive(settlement({ room_id: OTHER, match_id: OTHER }));
  socket.receive({ ...settlement(), room_id: undefined });
  assert.equal(session.getState().rankSettlement, null);
  session.close();
});

test('rejects malformed settlements as an error state without RP', () => {
  const { session, socket } = finished();
  socket.receive(settlement({ rp_after: 9999 }));
  const state = session.getState().rankSettlement;
  assert.deepEqual(state, { status: 'error', roomId: ROOM, message: '랭크 정산 정보를 확인할 수 없습니다.' });
  socket.receive(settlement());
  assert.equal(session.getState().rankSettlement.status, 'applied');
  session.close();
});

test('two-player games store an unranked settlement', () => {
  const { session, socket } = finished(2);
  socket.receive({ type: 'rank_settlement', room_id: ROOM, match_id: ROOM, applied: false, reason: 'unranked_match' });
  assert.deepEqual(session.getState().rankSettlement, { status: 'unranked', roomId: ROOM, reason: 'unranked_match' });
  session.close();
});

test('reconnecting restores the settlement the server sends again', () => {
  const { session, socket } = finished();
  socket.receive(settlement());
  const before = session.getState().rankSettlement;
  socket.drop();
  assert.equal(session.retry(), true);
  const next = FakeSocket.created.at(-1);
  next.open();
  next.receive({ type: 'auth_success', user_id: 1 });
  next.receive(roomState());
  next.receive(gameResult());
  next.receive(settlement());
  assert.equal(session.getState().rankSettlement, before);
  session.close();
});

test('a refreshed page restores the settlement from the server', () => {
  FakeSocket.created = [];
  const session = new GameSocket((url) => new FakeSocket(url), 'ws://localhost:5173/ws/game');
  session.resume('jwt-token');
  const socket = FakeSocket.created.at(-1);
  socket.open();
  socket.receive({ type: 'auth_success', user_id: 1 });
  socket.receive(roomState());
  socket.receive(gameResult());
  socket.receive(settlement({ placement: 4, rp_before: 600, placement_rp_change: -30, rp_change: -30, rp_after: 570, tier_before: 'GOLD', division_before: 1, tier_after: 'SILVER', division_after: 3 }));
  const state = session.getState().rankSettlement;
  assert.equal(state.status, 'applied');
  assert.deepEqual([state.settlement.tierBefore, state.settlement.tierAfter, state.settlement.divisionAfter], ['GOLD', 'SILVER', 3]);
  session.close();
});

test('a new match and leaving the room clear the settlement', () => {
  const { session, socket } = finished();
  socket.receive(settlement());
  socket.receive({ type: 'room_left', room_id: ROOM });
  assert.equal(session.getState().rankSettlement, null);
  socket.receive({ type: 'match_found', room_id: OTHER, map_id: 'default', players: players(4) });
  assert.equal(session.getState().rankSettlement, null);
  session.close();
});
