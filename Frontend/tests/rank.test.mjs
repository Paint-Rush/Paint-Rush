import assert from 'node:assert/strict';
import test from 'node:test';
import {
  formatRp,
  loadChallengers,
  loadMyRank,
  loadRankHistory,
  loadRanking,
  matchRankState,
  rankProgress,
  readMyRank,
  readRankSettlement,
  tierLabel,
  tierMove,
  tierName,
} from '../src/services/rank.ts';

const token = { access_token: 'rank-token', token_type: 'bearer' };

function myRank(change = {}) {
  return {
    user_id: 7, total_rp: 329, tier: 'SILVER', division: 1, next_tier: 'SILVER', next_division: 2,
    rp_to_next: 71, ranking_position: null, ...change,
  };
}

function history(change = {}) {
  return {
    match_id: 'room-1', placement: 1, rp_before: 299, placement_rp_change: 30, rp_change: 30,
    rp_after: 329, created_at: '2026-11-20T10:00:00+00:00', ...change,
  };
}

function entry(position, change = {}) {
  return { position, user_id: position, username: `p${position}`, display_name: `플레이어${position}`, total_rp: 2000 - position, tier: 'MASTER', ...change };
}

function server(routes, calls = []) {
  return async (url, options) => {
    calls.push(url);
    assert.equal(options.headers.Authorization, 'Bearer rank-token');
    if (!(url in routes)) assert.fail(`unexpected ${url}`);
    const value = routes[url];
    return typeof value === 'function' ? value() : Response.json(value);
  };
}

test('loads my rank with the bearer token', async () => {
  const calls = [];
  const rank = await loadMyRank(token, server({ '/users/me/rank': myRank() }, calls));
  assert.deepEqual(rank, {
    userId: 7, totalRp: 329, tier: 'SILVER', division: 1, nextTier: 'SILVER', nextDivision: 2, rpToNext: 71, rankingPosition: null,
  });
  assert.deepEqual(calls, ['/users/me/rank']);
});

test('labels divisions only for Bronze through Diamond', () => {
  for (const [tier, division, label] of [
    ['BRONZE', 1, 'Bronze 1'], ['SILVER', 2, 'Silver 2'], ['GOLD', 3, 'Gold 3'], ['PLATINUM', 1, 'Platinum 1'], ['DIAMOND', 3, 'Diamond 3'],
    ['MASTER', null, 'Master'], ['CHALLENGER', null, 'Challenger'],
  ]) {
    assert.equal(tierLabel(tier, division), label);
  }
  assert.throws(() => readMyRank(myRank({ tier: 'MASTER', division: 1 })), /서버 응답/);
  assert.throws(() => readMyRank(myRank({ tier: 'GOLD', division: null })), /서버 응답/);
  assert.throws(() => readMyRank(myRank({ tier: 'GOLD', division: 4 })), /서버 응답/);
  assert.throws(() => readMyRank(myRank({ tier: 'CHALLENGER', division: null, ranking_position: null })), /서버 응답/);
});

test('progress uses only the server rp_to_next value', () => {
  const read = (change) => rankProgress(readMyRank(myRank(change)));
  assert.deepEqual(read({}), { percent: 29, label: 'Silver 2까지 71 RP' });
  assert.deepEqual(read({ total_rp: 1499, tier: 'DIAMOND', division: 3, next_tier: 'MASTER', next_division: null, rp_to_next: 1 }), {
    percent: 99, label: 'Master까지 1 RP',
  });
  assert.deepEqual(read({ total_rp: 1650, tier: 'MASTER', division: null, next_tier: 'CHALLENGER', next_division: null, rp_to_next: 150, ranking_position: 4 }), {
    percent: 50, label: 'Challenger 최소 조건까지 150 RP',
  });
  const waiting = read({ total_rp: 1900, tier: 'MASTER', division: null, next_tier: null, next_division: null, rp_to_next: null, ranking_position: 101 });
  assert.equal(waiting.percent, 100);
  assert.match(waiting.label, /상위 100위 진입 필요 · 현재 101위/);
  assert.deepEqual(read({ total_rp: 2100, tier: 'CHALLENGER', division: null, next_tier: null, next_division: null, rp_to_next: null, ranking_position: 3 }), {
    percent: null, label: '챌린저 3위 / 상위 100위 경쟁 중',
  });
});

test('loads ranking pages and challengers', async () => {
  const calls = [];
  const request = server({
    '/rankings?limit=100&offset=0': { total: 150, rankings: [entry(1, { tier: 'CHALLENGER' }), entry(2)] },
    '/rankings?limit=100&offset=100': { total: 150, rankings: [entry(101)] },
    '/rankings/challengers': { total: 1, rankings: [entry(1, { tier: 'CHALLENGER' })] },
  }, calls);
  const first = await loadRanking(token, 0, request);
  assert.equal(first.total, 150);
  assert.deepEqual(first.rankings.map((item) => [item.position, item.displayName, item.tier, item.totalRp]), [
    [1, '플레이어1', 'CHALLENGER', 1999], [2, '플레이어2', 'MASTER', 1998],
  ]);
  assert.deepEqual((await loadRanking(token, 1, request)).rankings.map((item) => item.position), [101]);
  assert.equal((await loadChallengers(token, request)).rankings[0].tier, 'CHALLENGER');
  assert.deepEqual(calls, ['/rankings?limit=100&offset=0', '/rankings?limit=100&offset=100', '/rankings/challengers']);
  await assert.rejects(loadRanking(token, 0, server({ '/rankings?limit=100&offset=0': { total: 1, rankings: [entry(1, { tier: 'GOLD' })] } })), /서버 응답/);
});

test('loads RP history and keeps placement and applied change apart', async () => {
  const items = await loadRankHistory(token, server({
    '/users/me/rank/history?limit=20': [history(), history({ match_id: 'room-0', placement: 4, rp_before: 10, placement_rp_change: -30, rp_change: -10, rp_after: 0 })],
  }));
  assert.equal(items.length, 2);
  assert.deepEqual([items[1].placementRpChange, items[1].rpChange, items[1].rpAfter], [-30, -10, 0]);
  assert.deepEqual([30, 10, -10, -30, 0].map(formatRp), ['+30 RP', '+10 RP', '-10 RP', '-30 RP', '0 RP']);
  await assert.rejects(loadRankHistory(token, server({ '/users/me/rank/history?limit=20': [history({ rp_after: 999 })] })), /서버 응답/);
});

test('explains expired tokens, server errors, and connection failures', async () => {
  await assert.rejects(loadMyRank(token, async () => new Response('', { status: 401 })), /다시 로그인/);
  await assert.rejects(loadMyRank(token, async () => new Response('', { status: 500 })), /불러오지 못했습니다/);
  await assert.rejects(loadMyRank(token, async () => { throw new TypeError('fetch failed'); }), /연결할 수 없습니다/);
  await assert.rejects(loadMyRank(token, async () => new Response('not json')), /서버 응답/);
});

function settlement(change = {}) {
  return {
    type: 'rank_settlement', room_id: 'room-1', match_id: 'room-1', applied: true, reason: null, placement: 1,
    rp_before: 1470, placement_rp_change: 30, rp_change: 30, rp_after: 1500,
    tier_before: 'DIAMOND', division_before: 3, tier_after: 'MASTER', division_after: null, ...change,
  };
}

test('reads applied and unranked settlements from the server', () => {
  const applied = readRankSettlement(settlement());
  assert.equal(applied.status, 'applied');
  assert.deepEqual(applied.settlement, {
    roomId: 'room-1', matchId: 'room-1', placement: 1, rpBefore: 1470, placementRpChange: 30, rpChange: 30, rpAfter: 1500,
    tierBefore: 'DIAMOND', divisionBefore: 3, tierAfter: 'MASTER', divisionAfter: null,
  });
  assert.deepEqual(readRankSettlement({ type: 'rank_settlement', room_id: 'room-2', match_id: 'room-2', applied: false, reason: 'unranked_match' }), {
    status: 'unranked', roomId: 'room-2', reason: 'unranked_match',
  });
});

test('rejects malformed settlements', () => {
  for (const bad of [
    settlement({ rp_after: 1499 }),
    settlement({ placement: 5 }),
    settlement({ tier_after: 'LEGEND' }),
    settlement({ division_after: 1 }),
    settlement({ division_before: null }),
    settlement({ division_before: 4 }),
    settlement({ match_id: 'other' }),
    settlement({ applied: 'yes' }),
    settlement({ rp_before: -1, rp_after: 29 }),
    settlement({ applied: false, reason: '' }),
  ]) {
    assert.throws(() => readRankSettlement(bad), /서버 응답/);
  }
});

test('tier movement compares the server tiers', () => {
  const read = (change) => tierMove(readRankSettlement(settlement(change)).settlement);
  assert.equal(read({}), 'up');
  assert.equal(read({ placement: 4, rp_before: 600, placement_rp_change: -30, rp_change: -30, rp_after: 570, tier_before: 'GOLD', division_before: 1, tier_after: 'SILVER', division_after: 3 }), 'down');
  assert.equal(read({ rp_before: 280, rp_after: 310, tier_before: 'BRONZE', division_before: 3, tier_after: 'SILVER', division_after: 1 }), 'up');
  assert.equal(read({ rp_before: 410, rp_after: 440, tier_before: 'SILVER', division_before: 2, tier_after: 'SILVER', division_after: 2 }), 'same');
  assert.equal(read({ rp_before: 1700, rp_after: 1730, tier_before: 'MASTER', division_before: null, tier_after: 'MASTER', division_after: null }), 'same');
  assert.equal(read({ placement: 4, rp_before: 1810, placement_rp_change: -30, rp_change: -30, rp_after: 1780, tier_before: 'CHALLENGER', division_before: null, tier_after: 'MASTER', division_after: null }), 'down');
  assert.equal(tierName('DIAMOND', 3), 'DIAMOND 3');
  assert.equal(tierName('MASTER', null), 'MASTER');
});

test('match state only uses a settlement for the same room', () => {
  const state = readRankSettlement(settlement());
  assert.deepEqual(matchRankState(null, 'room-1'), { status: 'pending' });
  assert.deepEqual(matchRankState(state, 'room-9'), { status: 'pending' });
  assert.equal(matchRankState(state, 'room-1').status, 'applied');
  assert.deepEqual(matchRankState({ status: 'error', roomId: 'room-1', message: 'x' }, 'room-1'), { status: 'error', message: 'x' });
});

test('a new login loads that user rank with the new token', async () => {
  const seen = [];
  const request = async (url, options) => {
    seen.push(options.headers.Authorization);
    const id = options.headers.Authorization.endsWith('a') ? 1 : 2;
    return Response.json(myRank({ user_id: id, total_rp: id * 100, tier: 'BRONZE', division: id, next_tier: 'BRONZE', next_division: id + 1, rp_to_next: 100 }));
  };
  const first = await loadMyRank({ access_token: 'token-a', token_type: 'bearer' }, request);
  const second = await loadMyRank({ access_token: 'token-b', token_type: 'bearer' }, request);
  assert.deepEqual([first.userId, second.userId], [1, 2]);
  assert.deepEqual(seen, ['Bearer token-a', 'Bearer token-b']);
});

test('accepts legacy settlements without tier data and never invents a tier', () => {
  const legacy = readRankSettlement(settlement({ tier_before: null, division_before: null, tier_after: null, division_after: null }));
  assert.equal(legacy.status, 'applied');
  assert.deepEqual([legacy.settlement.tierBefore, legacy.settlement.tierAfter, legacy.settlement.divisionBefore], [null, null, null]);
  assert.equal(tierMove(legacy.settlement), null);
  assert.throws(() => readRankSettlement(settlement({ tier_before: null, division_before: null })), /서버 응답/);
  assert.throws(() => readRankSettlement(settlement({ tier_before: null, division_before: 2, tier_after: null, division_after: null })), /서버 응답/);
});
