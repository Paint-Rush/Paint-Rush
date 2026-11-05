import assert from 'node:assert/strict';
import test from 'node:test';
import { GameSocket, gameSocketUrl, readRoomState, readySecondsLeft } from '../src/services/gameSocket.ts';

class FakeSocket {
  static created = [];
  readyState = 0;
  sent = [];
  closed = null;
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

  close(code, reason) {
    this.closed = { code, reason };
    this.readyState = 3;
  }

  open() {
    this.readyState = 1;
    this.onopen?.({});
  }

  receive(message) {
    this.onmessage?.({ data: typeof message === 'string' ? message : JSON.stringify(message) });
  }

  drop(code = 1006) {
    this.readyState = 3;
    this.onclose?.({ code, reason: '' });
  }

  types() {
    return this.sent.map((message) => message.type);
  }
}

const ROOM = '7f9c2b1e-0000-4000-8000-000000000001';
const players = [
  { user_id: 1, color: 'RED', spawn: [0, 0], character_id: 'character_02', character_name: '테스트 캐릭터 2', ultimate_id: 'ultimate_02', ultimate_name: '테스트 궁극기 2' },
  { user_id: 2, color: 'BLUE', spawn: [0, 19], character_id: 'character_01', character_name: '테스트 캐릭터 1', ultimate_id: 'ultimate_01', ultimate_name: '테스트 궁극기 1' },
];

function roomState(overrides = {}) {
  return {
    type: 'room_state',
    room_id: ROOM,
    status: 'READY',
    map_id: 'default',
    map: { width: 20, height: 20, tiles: Array.from({ length: 20 }, () => Array(20).fill('NORMAL')) },
    players: players.map((player) => ({ ...player, position: player.spawn, alive: true, connected: true, tile_count: 0 })),
    my_cost: 6,
    my_hand: ['move_dash', 'attack_slash', 'move_jump'],
    my_next_card: 'attack_bomb',
    my_ultimate_used: false,
    tile_owners: Array.from({ length: 20 }, () => Array(20).fill(null)),
    started_at: null,
    ready_remaining_seconds: 8,
    remaining_seconds: 300,
    max_cost: 10,
    ...overrides,
  };
}

function setup() {
  FakeSocket.created = [];
  let time = 1_000_000;
  const session = new GameSocket((url) => new FakeSocket(url), 'ws://localhost:5173/ws/game', () => time);
  return { session, sockets: FakeSocket.created, advance: (ms) => { time += ms; }, last: () => FakeSocket.created.at(-1) };
}

function authenticated(characterId = 'character_02') {
  const context = setup();
  context.session.start('jwt-token', characterId);
  const socket = context.last();
  socket.open();
  socket.receive({ type: 'auth_success', user_id: 1 });
  return { ...context, socket };
}

function waiting() {
  const context = authenticated();
  context.socket.receive({ type: 'queue_waiting', waiting: 1, match_size: 2 });
  return context;
}

function matched() {
  const context = waiting();
  context.socket.receive({ type: 'match_found', room_id: ROOM, map_id: 'default', players });
  context.socket.receive({ type: 'game_ready', room_id: ROOM, remaining_seconds: 10 });
  return context;
}

test('builds the same-origin game socket URL without the token', () => {
  assert.equal(gameSocketUrl({ protocol: 'http:', host: 'localhost:5173' }), 'ws://localhost:5173/ws/game');
  assert.equal(gameSocketUrl({ protocol: 'https:', host: 'paint.example' }), 'wss://paint.example/ws/game');
  const { session, last } = setup();
  session.start('secret-jwt', 'character_01');
  assert.doesNotMatch(last().url, /secret-jwt|token/);
  session.close();
});

test('connects, authenticates first, then joins the queue with the confirmed character_id', () => {
  const { session, last } = setup();
  assert.equal(session.start('jwt-token', 'character_02'), true);
  const socket = last();
  assert.equal(session.getState().phase, 'connecting');
  assert.deepEqual(socket.sent, []);
  socket.open();
  assert.equal(session.getState().phase, 'authenticating');
  assert.deepEqual(socket.sent, [{ type: 'auth', token: 'jwt-token' }]);
  socket.receive({ type: 'auth_success', user_id: 1 });
  assert.deepEqual(socket.sent.at(-1), { type: 'queue_join', character_id: 'character_02' });
  assert.equal(session.getState().phase, 'joining');
  assert.equal(session.getState().userId, 1);
  socket.receive({ type: 'queue_waiting', waiting: 1, match_size: 2 });
  assert.equal(session.getState().phase, 'waiting');
  assert.deepEqual(session.getState().queue, { waiting: 1, matchSize: 2 });
  session.close();
});

test('never sends queue_join before auth_success and blocks duplicate join requests', () => {
  const { session, sockets, last } = setup();
  session.start('jwt-token', 'character_01');
  assert.equal(session.start('jwt-token', 'character_01'), false);
  assert.equal(sockets.length, 1);
  const socket = last();
  socket.open();
  assert.deepEqual(socket.types(), ['auth']);
  socket.receive({ type: 'auth_success', user_id: 1 });
  socket.receive({ type: 'queue_waiting', waiting: 1, match_size: 2 });
  assert.equal(session.start('jwt-token', 'character_01'), false);
  assert.deepEqual(socket.types(), ['auth', 'queue_join']);
  socket.receive({ type: 'error', code: 'already_in_queue', message: '이미 매칭 대기 중인 유저입니다.' });
  assert.equal(session.getState().phase, 'waiting');
  session.close();
});

test('cancels only after the server confirms and can queue again on the same connection', () => {
  const { session, socket, sockets } = waiting();
  assert.equal(session.cancel(), true);
  assert.equal(session.cancel(), false);
  assert.equal(session.getState().phase, 'cancelling');
  assert.deepEqual(socket.sent.at(-1), { type: 'queue_cancel' });
  assert.equal(socket.types().filter((type) => type === 'queue_cancel').length, 1);
  socket.receive({ type: 'queue_cancelled' });
  assert.equal(session.getState().phase, 'cancelled');
  session.release();
  assert.equal(session.getState().phase, 'idle');
  assert.equal(session.start('jwt-token', 'character_01'), true);
  assert.equal(sockets.length, 1);
  assert.deepEqual(socket.sent.at(-1), { type: 'queue_join', character_id: 'character_01' });
  session.close();
});

test('a match found during cancellation wins and no cancel is sent after match_found', () => {
  const { session, socket } = waiting();
  session.cancel();
  socket.receive({ type: 'match_found', room_id: ROOM, map_id: 'default', players });
  socket.receive({ type: 'error', code: 'not_in_queue', message: '매칭 대기 중이 아닙니다.' });
  assert.equal(session.getState().phase, 'matched');
  const sentBefore = socket.sent.length;
  assert.equal(session.cancel(), false);
  assert.equal(socket.sent.length, sentBefore);
  session.close();
});

test('stores the match room, participants, and server ready time', () => {
  const { session, socket, advance } = matched();
  const state = session.getState();
  assert.equal(state.phase, 'ready');
  assert.equal(state.roomId, ROOM);
  assert.deepEqual(state.players.map((player) => [player.userId, player.characterId, player.color]), [[1, 'character_02', 'RED'], [2, 'character_01', 'BLUE']]);
  assert.equal(state.players[0].characterName, '테스트 캐릭터 2');
  assert.ok(socket.types().includes('room_state'));
  assert.equal(readySecondsLeft(state, 1_000_000), 10);
  advance(3200);
  assert.equal(readySecondsLeft(session.getState(), 1_003_200), 7);
  socket.receive({ type: 'game_tick', status: 'READY', remaining_seconds: 5 });
  assert.equal(readySecondsLeft(session.getState(), 1_003_200), 5);
  socket.receive(roomState({ ready_remaining_seconds: 5 }));
  assert.equal(session.getState().room.players.length, 2);
  assert.equal(session.getState().roomId, ROOM);
  session.close();
});

test('game_started keeps the same connection, room, and latest server state', () => {
  const { session, socket, sockets } = matched();
  socket.receive(roomState());
  socket.receive({ type: 'game_tick', status: 'READY', remaining_seconds: 1 });
  socket.receive({ type: 'game_started', room_id: ROOM, duration_seconds: 300 });
  socket.receive({ type: 'game_tick', status: 'PLAYING', remaining_seconds: 300 });
  const state = session.getState();
  assert.equal(state.phase, 'started');
  assert.equal(state.roomId, ROOM);
  assert.equal(state.room.status, 'PLAYING');
  assert.equal(state.durationSeconds, 300);
  assert.equal(state.connected, true);
  assert.equal(sockets.length, 1);
  assert.equal(socket.closed, null);
  session.close();
});

test('normal in-game events update state and never break the connection', () => {
  const { session, socket } = matched();
  socket.receive(roomState({ status: 'PLAYING', ready_remaining_seconds: 0 }));
  socket.receive({ type: 'game_started', room_id: ROOM, duration_seconds: 300 });
  const events = [];
  session.onEvent((message) => events.push(message.type));
  const messages = [
    { type: 'card_used', user_id: 1, card_id: 'move_dash', hand_index: 0 },
    { type: 'hand_updated', hand: ['attack_slash', 'move_jump', 'attack_bomb'], cost: 4 },
    { type: 'cost_updated', cost: 5, max_cost: 10 },
    { type: 'move_requested', user_id: 1, card_id: 'move_dash' },
    { type: 'attack_requested', user_id: 2, card_id: 'attack_slash' },
    { type: 'player_moved', user_id: 1, x: 2, y: 0, path: [[0, 0], [1, 0], [2, 0]] },
    { type: 'tiles_updated', room_id: ROOM, tiles: [{ x: 1, y: 0, previous_owner: null, new_owner: 1 }, { x: 2, y: 0, previous_owner: null, new_owner: 1 }] },
    { type: 'ultimate_activated', user_id: 1, character_id: 'character_02', ultimate_id: 'ultimate_02' },
    { type: 'player_disconnected', user_id: 2 },
    { type: 'player_reconnected', user_id: 2 },
    { type: 'deathmatch_started', remaining_seconds: 60, cost_interval: 0.45 },
    { type: 'error', code: 'insufficient_cost', message: '코스트가 부족합니다.' },
    { type: 'game_finished', room_id: ROOM },
    { type: 'game_result', room_id: ROOM, winner_id: 1, results: [{ user_id: 1, rank: 1, is_winner: true }, { user_id: 2, rank: 2, is_winner: false }] },
  ];
  for (const message of messages) {
    socket.receive(message);
    assert.notEqual(session.getState().phase, 'error', message.type);
  }
  const { room, result, phase, error, connected } = session.getState();
  assert.equal(phase, 'finished');
  assert.equal(error, null);
  assert.equal(connected, true);
  assert.deepEqual(room.myHand, ['attack_slash', 'move_jump', 'attack_bomb']);
  assert.equal(room.myCost, 5);
  assert.deepEqual(room.players[0].position, [2, 0]);
  assert.equal(room.tileOwners[0][2], 1);
  assert.equal(room.players[0].tileCount, 2);
  assert.equal(room.players[1].connected, true);
  assert.equal(room.myUltimateUsed, true);
  assert.equal(result.winnerId, 1);
  assert.equal(events.length, messages.length);
  assert.equal(socket.closed, null);
  session.close();
});

test('room_left and session_replaced are handled as server events', () => {
  const { session, socket } = matched();
  socket.receive({ type: 'game_finished', room_id: ROOM });
  assert.equal(session.leaveRoom(), true);
  assert.deepEqual(socket.sent.at(-1), { type: 'room_leave' });
  socket.receive({ type: 'room_left', room_id: ROOM });
  assert.equal(session.getState().phase, 'idle');
  assert.equal(session.getState().roomId, null);
  socket.receive({ type: 'session_replaced' });
  socket.drop(4000);
  assert.equal(session.getState().phase, 'error');
  assert.equal(session.getState().error.code, 'session_replaced');
  session.close();
});

test('ignores malformed or unknown messages without failing the connection', () => {
  const { session, socket } = waiting();
  socket.receive('not json');
  assert.equal(session.getState().phase, 'waiting');
  assert.match(session.getState().warning, /형식/);
  socket.receive({ type: 'future_event' });
  socket.receive({ type: 'player_moved', user_id: 'x' });
  socket.receive([1, 2]);
  assert.equal(session.getState().phase, 'waiting');
  assert.equal(socket.closed, null);
  session.close();
});

test('rejects a malformed match_found as a protocol error instead of inventing a room', () => {
  const { session, socket } = waiting();
  socket.receive({ type: 'match_found', room_id: '', map_id: 'default', players });
  assert.equal(session.getState().phase, 'error');
  assert.equal(session.getState().error.code, 'protocol_error');
  assert.equal(session.getState().roomId, null);
  assert.ok(socket.closed);
  assert.throws(() => readRoomState(roomState({ tile_owners: [[99]] })));
});

test('reports connection failures, auth failures, and early closes', () => {
  const failed = setup();
  failed.session.start('jwt-token', 'character_01');
  failed.last().drop();
  assert.equal(failed.session.getState().error.code, 'connection_failed');

  const early = setup();
  early.session.start('jwt-token', 'character_01');
  early.last().open();
  early.last().drop(1008);
  assert.equal(early.session.getState().error.code, 'auth_closed');

  const rejected = setup();
  rejected.session.start('expired', 'character_01');
  rejected.last().open();
  rejected.last().receive({ type: 'error', code: 'auth_failed', message: '유효하지 않은 토큰입니다.' });
  rejected.last().drop(1008);
  const state = rejected.session.getState();
  assert.equal(state.phase, 'error');
  assert.equal(state.error.code, 'auth_failed');
  assert.match(state.error.message, /다시 로그인/);
  assert.equal(rejected.session.retry(), false);
  assert.deepEqual(rejected.last().types(), ['auth']);

  const thrown = new GameSocket(() => { throw new Error('offline'); }, 'ws://x/ws/game');
  thrown.start('jwt-token', 'character_01');
  assert.equal(thrown.getState().error.code, 'connection_failed');
});

test('a disconnect while waiting shows an error and retry queues again on a fresh connection', () => {
  const { session, socket, sockets } = waiting();
  socket.drop();
  assert.equal(session.getState().error.code, 'queue_disconnected');
  assert.equal(session.retry(), true);
  assert.equal(sockets.length, 2);
  const next = sockets[1];
  next.open();
  next.receive({ type: 'auth_success', user_id: 1 });
  assert.deepEqual(next.sent.at(-1), { type: 'queue_join', character_id: 'character_02' });
  session.close();
});

test('a disconnect during READY keeps the room and reconnects without joining a new queue', () => {
  const { session, socket, sockets } = matched();
  socket.drop();
  const lost = session.getState();
  assert.equal(lost.error.code, 'game_disconnected');
  assert.equal(lost.roomId, ROOM);
  session.retry();
  const next = sockets[1];
  next.open();
  next.receive({ type: 'auth_success', user_id: 1 });
  assert.ok(!next.types().includes('queue_join'));
  next.receive(roomState({ ready_remaining_seconds: 3 }));
  assert.equal(session.getState().phase, 'ready');
  assert.equal(session.getState().roomId, ROOM);
  session.close();
});

test('screen changes do not close the socket; only close() does, and stale sockets are ignored', () => {
  const { session, socket, sockets } = matched();
  let notified = 0;
  const listener = () => { notified += 1; };
  const unsubscribeA = session.subscribe(listener);
  const unsubscribeB = session.subscribe(listener);
  socket.receive({ type: 'game_tick', status: 'READY', remaining_seconds: 9 });
  assert.equal(notified, 1);
  unsubscribeA();
  unsubscribeB();
  session.release();
  assert.equal(session.getState().phase, 'ready');
  assert.equal(socket.closed, null);
  session.close();
  assert.deepEqual(socket.closed, { code: 1000, reason: 'client closed' });
  assert.equal(session.getState().phase, 'idle');
  socket.receive({ type: 'match_found', room_id: ROOM, map_id: 'default', players });
  socket.drop();
  assert.equal(session.getState().phase, 'idle');
  assert.equal(sockets.length, 1);
});

test('a finished previous room is cleared before queueing again', () => {
  const { session, socket } = authenticated('character_01');
  socket.receive(roomState({ status: 'FINISHED', ready_remaining_seconds: 0, remaining_seconds: 0 }));
  socket.receive({ type: 'error', code: 'already_in_game', message: '이미 게임 중인 유저입니다.' });
  assert.equal(session.getState().phase, 'finished');
  session.leaveRoom();
  socket.receive({ type: 'room_left', room_id: ROOM });
  assert.deepEqual(socket.sent.at(-1), { type: 'queue_join', character_id: 'character_01' });
  assert.equal(session.getState().phase, 'joining');
  session.close();
});

test('queue rejections from the server surface as errors', () => {
  const { session, socket } = authenticated('character_99');
  socket.receive({ type: 'error', code: 'unknown_character', message: '등록되지 않은 캐릭터입니다.' });
  assert.equal(session.getState().phase, 'error');
  assert.equal(session.getState().error.message, '등록되지 않은 캐릭터입니다.');
  session.close();
});

function playing() {
  const context = matched();
  context.socket.receive({ type: 'game_started', room_id: ROOM, duration_seconds: 300 });
  context.socket.receive(roomState({ status: 'PLAYING', ready_remaining_seconds: 0 }));
  return context;
}

test('sends card_use with the real hand index and target only while playing', () => {
  const { session, socket } = matched();
  assert.equal(session.playCard(0, { x: 0, y: 1 }), false);
  socket.receive({ type: 'game_started', room_id: ROOM, duration_seconds: 300 });
  socket.receive(roomState({ status: 'PLAYING', ready_remaining_seconds: 0 }));
  assert.equal(session.playCard(3), false);
  assert.equal(session.playCard(-1), false);
  assert.equal(session.playCard(0, { x: 0, y: 1 }), true);
  assert.deepEqual(socket.sent.at(-1), { type: 'card_use', hand_index: 0, target_x: 0, target_y: 1 });
  assert.equal(session.playCard(1), true);
  assert.deepEqual(socket.sent.at(-1), { type: 'card_use', hand_index: 1 });
  const room = session.getState().room;
  assert.deepEqual(room.players[0].position, [0, 0]);
  assert.equal(room.tileOwners[1][0], null);
  session.close();
});

test('applies server movement, painting overwrite and hand rotation from the same socket', () => {
  const { session, socket, sockets } = playing();
  socket.receive({ type: 'card_used', user_id: 1, card_id: 'move_dash', hand_index: 0 });
  socket.receive({ type: 'hand_updated', hand: ['move_jump', 'attack_slash', 'move_jump'], cost: 4 });
  socket.receive({ type: 'cost_updated', cost: 4, max_cost: 10 });
  socket.receive({ type: 'move_requested', user_id: 1, card_id: 'move_dash' });
  socket.receive({ type: 'player_moved', user_id: 1, x: 0, y: 1, path: [[0, 0], [0, 1]] });
  socket.receive({ type: 'tiles_updated', room_id: ROOM, tiles: [{ x: 0, y: 1, previous_owner: null, new_owner: 1 }] });
  socket.receive({ type: 'tiles_updated', room_id: ROOM, tiles: [{ x: 0, y: 1, previous_owner: 1, new_owner: 2 }] });
  const { room, phase, warning } = session.getState();
  assert.equal(phase, 'started');
  assert.equal(warning, null);
  assert.deepEqual(room.players[0].position, [0, 1]);
  assert.deepEqual(room.myHand, ['move_jump', 'attack_slash', 'move_jump']);
  assert.equal(room.myCost, 4);
  assert.equal(room.tileOwners[1][0], 2);
  assert.deepEqual(room.players.map((player) => player.tileCount), [0, 1]);
  assert.equal(sockets.length, 1);
  session.close();
});

test('sync requests a full room_state and the next room_state replaces local tiles', () => {
  const { session, socket } = playing();
  socket.receive({ type: 'tiles_updated', room_id: ROOM, tiles: [{ x: 3, y: 3, previous_owner: null, new_owner: 1 }] });
  assert.equal(session.sync(), true);
  assert.deepEqual(socket.sent.at(-1), { type: 'room_state' });
  const owners = Array.from({ length: 20 }, () => Array(20).fill(null));
  owners[5][5] = 2;
  socket.receive(roomState({ status: 'DEATHMATCH', ready_remaining_seconds: 0, remaining_seconds: 50, tile_owners: owners,
    players: players.map((player) => ({ ...player, position: player.spawn, alive: true, connected: true, tile_count: player.user_id === 2 ? 1 : 0 })) }));
  const { room } = session.getState();
  assert.equal(room.tileOwners[3][3], null);
  assert.equal(room.tileOwners[5][5], 2);
  assert.equal(room.status, 'DEATHMATCH');
  assert.equal(session.playCard(0, { x: 1, y: 0 }), true);
  session.close();
  assert.equal(session.sync(), false);
});

test('resume reconnects into a running game without character selection or queue_join', () => {
  const { session, sockets, last } = setup();
  assert.equal(session.resume('jwt-token'), true);
  assert.equal(session.resume('jwt-token'), false);
  const socket = last();
  socket.open();
  socket.receive({ type: 'auth_success', user_id: 1 });
  assert.equal(session.getState().phase, 'idle');
  assert.deepEqual(socket.types(), ['auth']);
  socket.receive(roomState({ status: 'PLAYING', ready_remaining_seconds: 0, remaining_seconds: 120 }));
  assert.equal(session.getState().phase, 'started');
  assert.equal(session.getState().roomId, ROOM);
  assert.deepEqual(socket.types(), ['auth']);
  assert.equal(sockets.length, 1);
  session.close();
});

test('resume stays idle when not in a room and the queue reuses the same socket', () => {
  const { session, sockets, last } = setup();
  session.resume('jwt-token');
  const socket = last();
  assert.equal(session.start('jwt-token', 'character_01'), true);
  socket.open();
  socket.receive({ type: 'auth_success', user_id: 1 });
  assert.deepEqual(socket.types(), ['auth', 'queue_join']);
  assert.equal(session.getState().phase, 'joining');
  session.close();

  const second = setup();
  second.session.resume('jwt-token');
  second.last().open();
  second.last().receive({ type: 'auth_success', user_id: 1 });
  assert.equal(second.session.getState().error, null);
  assert.equal(second.session.start('jwt-token', 'character_02'), true);
  assert.equal(second.sockets.length, 1);
  assert.deepEqual(second.last().types(), ['auth', 'queue_join']);
  second.session.close();
  assert.equal(sockets.length, 1);
});

test('a game socket drop keeps the room and retry restores it with room_state, not queue_join', () => {
  const { session, socket, sockets, last } = playing();
  socket.drop();
  assert.equal(session.getState().phase, 'error');
  assert.equal(session.getState().room.status, 'PLAYING');
  assert.equal(session.retry(), true);
  const next = last();
  next.open();
  next.receive({ type: 'auth_success', user_id: 1 });
  assert.deepEqual(next.types(), ['auth', 'room_state']);
  next.receive(roomState({ status: 'PLAYING', ready_remaining_seconds: 0 }));
  assert.equal(session.getState().phase, 'started');
  assert.equal(sockets.length, 2);
  session.close();
});

test('game_finished and game_result end the game and logout closes the socket', () => {
  const { session, socket } = playing();
  socket.receive({ type: 'game_finished', room_id: ROOM });
  socket.receive({ type: 'game_result', room_id: ROOM, winner_id: 1, results: [{ user_id: 1, rank: 1, is_winner: true }, { user_id: 2, rank: 2, is_winner: false }] });
  assert.equal(session.getState().phase, 'finished');
  assert.equal(session.getState().room.status, 'FINISHED');
  assert.equal(session.getState().result.winnerId, 1);
  assert.equal(session.playCard(0, { x: 1, y: 0 }), false);
  session.close();
  assert.equal(socket.closed.code, 1000);
  assert.equal(session.getState().phase, 'idle');
});
