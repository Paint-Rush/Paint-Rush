import assert from 'node:assert/strict';
import test from 'node:test';
import { GameSocket, initialSession } from '../src/services/gameSocket.ts';
import { readRewardSettlement } from '../src/services/reward.ts';

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

function reward(change = {}) {
  return {
    type: 'reward_settlement', room_id: ROOM, match_id: ROOM, applied: true, reason: null, placement: 1,
    gold_earned: 100, gold_before: 500, gold_after: 600,
    experience_earned: 100, experience_before: 200, experience_after: 300,
    level_before: 2, level_after: 3, leveled_up: true, ...change,
  };
}

function rank(change = {}) {
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

test('initial session has no reward', () => {
  assert.equal(initialSession().rewardSettlement, null);
});

test('reads gold, experience, and level-up from the server', () => {
  const state = readRewardSettlement(reward());
  assert.deepEqual(state, {
    status: 'applied',
    roomId: ROOM,
    reward: {
      roomId: ROOM, matchId: ROOM, placement: 1, goldEarned: 100, goldBefore: 500, goldAfter: 600,
      experienceEarned: 100, experienceBefore: 200, experienceAfter: 300, levelBefore: 2, levelAfter: 3, leveledUp: true,
    },
  });
  const plain = readRewardSettlement(reward({ placement: 2, gold_earned: 40, gold_after: 540, experience_earned: 40, experience_after: 240, level_after: 2, leveled_up: false }));
  assert.equal(plain.reward.leveledUp, false);
  assert.deepEqual(readRewardSettlement({ type: 'reward_settlement', room_id: ROOM, match_id: ROOM, applied: false, reason: 'unrewarded_match' }), {
    status: 'unrewarded', roomId: ROOM, reason: 'unrewarded_match',
  });
});

test('rejects malformed rewards', () => {
  for (const bad of [
    reward({ gold_after: 601 }),
    reward({ experience_after: 299 }),
    reward({ gold_earned: -1, gold_after: 499 }),
    reward({ gold_earned: '100' }),
    reward({ experience_earned: 1.5, experience_after: 201.5 }),
    reward({ level_before: 0 }),
    reward({ level_after: 1, leveled_up: false }),
    reward({ leveled_up: false }),
    reward({ level_after: 2 }),
    reward({ leveled_up: 'yes' }),
    reward({ placement: 5 }),
    reward({ placement: 0 }),
    reward({ match_id: 'other' }),
    reward({ applied: 1 }),
    reward({ applied: false, reason: '' }),
    reward({ gold_before: null }),
  ]) {
    assert.throws(() => readRewardSettlement(bad), /서버 응답/);
  }
});

test('stores the reward for the current room', () => {
  const { session, socket } = finished();
  assert.equal(session.getState().rewardSettlement, null);
  socket.receive(reward());
  const state = session.getState().rewardSettlement;
  assert.equal(state.status, 'applied');
  assert.deepEqual([state.reward.goldAfter, state.reward.experienceAfter, state.reward.levelAfter, state.reward.leveledUp], [600, 300, 3, true]);
  assert.equal(session.getState().warning, null);
  session.close();
});

test('two-player games keep gold and experience rewards next to the unranked RP state', () => {
  const { session, socket } = finished(2);
  socket.receive({ type: 'rank_settlement', room_id: ROOM, match_id: ROOM, applied: false, reason: 'unranked_match' });
  socket.receive(reward({ placement: 2, gold_earned: 40, gold_after: 540, experience_earned: 40, experience_after: 240, level_after: 2, leveled_up: false }));
  const state = session.getState();
  assert.equal(state.rankSettlement.status, 'unranked');
  assert.equal(state.rewardSettlement.status, 'applied');
  assert.equal(state.rewardSettlement.reward.goldEarned, 40);
  session.close();
});

test('rank and reward events are independent', () => {
  const { session, socket } = finished();
  socket.receive(reward());
  socket.receive(rank());
  const state = session.getState();
  assert.equal(state.rankSettlement.settlement.rpAfter, 1500);
  assert.equal(state.rewardSettlement.reward.goldAfter, 600);
  socket.receive({ ...rank(), rp_after: 9 });
  assert.equal(session.getState().rewardSettlement, state.rewardSettlement);
  assert.equal(session.getState().rankSettlement, state.rankSettlement);
  session.close();
});

test('ignores duplicate rewards for the same match', () => {
  const { session, socket } = finished();
  socket.receive(reward());
  const first = session.getState().rewardSettlement;
  socket.receive(reward());
  socket.receive(reward({ gold_earned: 1, gold_after: 501 }));
  assert.equal(session.getState().rewardSettlement, first);
  session.close();
});

test('ignores rewards for other rooms', () => {
  const { session, socket } = finished();
  socket.receive(reward({ room_id: OTHER, match_id: OTHER }));
  socket.receive({ ...reward(), room_id: undefined });
  assert.equal(session.getState().rewardSettlement, null);
  session.close();
});

test('malformed rewards become an error state without numbers', () => {
  const { session, socket } = finished();
  socket.receive(reward({ gold_after: 9999 }));
  assert.deepEqual(session.getState().rewardSettlement, { status: 'error', roomId: ROOM, message: '보상 정산 정보를 확인할 수 없습니다.' });
  socket.receive(reward());
  assert.equal(session.getState().rewardSettlement.status, 'applied');
  session.close();
});

test('reconnecting keeps the reward the server sends again', () => {
  const { session, socket } = finished();
  socket.receive(reward());
  const before = session.getState().rewardSettlement;
  socket.drop();
  assert.equal(session.retry(), true);
  const next = FakeSocket.created.at(-1);
  next.open();
  next.receive({ type: 'auth_success', user_id: 1 });
  next.receive(roomState());
  next.receive(gameResult());
  next.receive(rank());
  next.receive(reward());
  assert.equal(session.getState().rewardSettlement, before);
  session.close();
});

test('a refreshed page restores the reward from the server', () => {
  FakeSocket.created = [];
  const session = new GameSocket((url) => new FakeSocket(url), 'ws://localhost:5173/ws/game');
  session.resume('jwt-token');
  const socket = FakeSocket.created.at(-1);
  socket.open();
  socket.receive({ type: 'auth_success', user_id: 1 });
  socket.receive(roomState());
  socket.receive(gameResult());
  socket.receive(rank());
  socket.receive(reward({ placement: 4, gold_earned: 40, gold_after: 540, experience_earned: 40, experience_after: 240, level_after: 2, leveled_up: false }));
  const state = session.getState();
  assert.equal(state.rewardSettlement.reward.placement, 4);
  assert.equal(state.rankSettlement.status, 'applied');
  session.close();
});

test('a new match and leaving the room clear the reward', () => {
  const { session, socket } = finished();
  socket.receive(reward());
  socket.receive({ type: 'room_left', room_id: ROOM });
  assert.equal(session.getState().rewardSettlement, null);
  socket.receive({ type: 'match_found', room_id: OTHER, map_id: 'default', players: players(4) });
  assert.equal(session.getState().rewardSettlement, null);
  assert.equal(session.getState().rankSettlement, null);
  session.close();
});
