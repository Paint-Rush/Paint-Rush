import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { levelProgress, loadLobbyWallet } from '../src/services/account.ts';
import { celebrationMatch, claimCelebration, matchRewardState, readRewardSettlement } from '../src/services/reward.ts';

const root = fileURLToPath(new URL('../', import.meta.url));

async function loadRenderers() {
  const result = await build({
    stdin: {
      contents: `
        import { createElement } from 'react';
        import { renderToStaticMarkup } from 'react-dom/server';
        import LobbyPage from './src/pages/LobbyPage';
        import ResultPage from './src/pages/ResultPage';
        import { MatchRewardPanel } from './src/components/RewardWidgets';
        import { initialSession } from './src/services/gameSocket';
        const noop = () => {};
        const token = { access_token: 't', token_type: 'bearer' };
        export const panel = (state, celebrate) => renderToStaticMarkup(createElement(MatchRewardPanel, { state, celebrate }));
        export const lobby = () => renderToStaticMarkup(createElement(LobbyPage, { token, onGame: noop, onLogout: noop, loadRank: () => new Promise(noop), loadWallet: () => new Promise(noop) }));
        export const result = (change) => renderToStaticMarkup(createElement(ResultPage, {
          session: { ...initialSession(), ...change },
          socket: { onEvent: () => noop, leaveRoom: () => true, retry: () => true },
          onLeft: noop,
        }));
      `,
      resolveDir: root,
      loader: 'tsx',
    },
    bundle: true,
    write: false,
    format: 'cjs',
    platform: 'node',
    jsx: 'automatic',
    loader: { '.css': 'empty' },
    define: { 'process.env.NODE_ENV': '"production"' },
    logLevel: 'silent',
  });
  const dir = await mkdtemp(join(tmpdir(), 'paint-rush-reward-'));
  const file = join(dir, 'reward-ui.cjs');
  await writeFile(file, result.outputFiles[0].text);
  const module = (await import(pathToFileURL(file).href)).default;
  await rm(dir, { recursive: true, force: true });
  return module;
}

const ui = await loadRenderers();
const ROOM = 'room-r';

function wire(change = {}) {
  return {
    type: 'reward_settlement', room_id: ROOM, match_id: ROOM, applied: true, reason: null, placement: 1,
    gold_earned: 100, gold_before: 500, gold_after: 600, experience_earned: 100, experience_before: 200,
    experience_after: 300, level_before: 2, level_after: 3, leveled_up: true, ...change,
  };
}

const NO_LEVEL_UP = { placement: 3, gold_earned: 60, gold_after: 560, experience_earned: 60, experience_after: 260, level_after: 2, leveled_up: false };

function applied(change) {
  return matchRewardState(readRewardSettlement(wire(change)), ROOM);
}

function token(name) {
  return { access_token: name, token_type: 'bearer' };
}

test('loads the wallet gold with the bearer token', async () => {
  const calls = [];
  const gold = await loadLobbyWallet(token('jwt-1'), async (url, options) => {
    calls.push([url, options.headers.Authorization]);
    return Response.json({ user_id: 7, gold: 1500 });
  });
  assert.equal(gold, 1500);
  assert.deepEqual(calls, [['/users/me/wallet', 'Bearer jwt-1']]);
});

test('wallet errors and malformed responses', async () => {
  await assert.rejects(loadLobbyWallet(token('t'), async () => new Response('', { status: 401 })), /다시 로그인/);
  await assert.rejects(loadLobbyWallet(token('t'), async () => new Response('', { status: 500 })), /불러오지 못했습니다/);
  await assert.rejects(loadLobbyWallet(token('t'), async () => { throw new TypeError('down'); }), /연결할 수 없습니다/);
  for (const bad of [{}, { gold: -1 }, { gold: 1.5 }, { gold: '10' }, null]) {
    await assert.rejects(loadLobbyWallet(token('t'), async () => Response.json(bad)), /서버 응답/);
  }
});

test('re-login and returning to the lobby read the latest server gold', async () => {
  const balances = { 'jwt-a': [100, 200], 'jwt-b': [9] };
  const request = async (url, options) => Response.json({ user_id: 1, gold: balances[options.headers.Authorization.slice(7)].shift() });
  assert.equal(await loadLobbyWallet(token('jwt-a'), request), 100);
  assert.equal(await loadLobbyWallet(token('jwt-b'), request), 9);
  assert.equal(await loadLobbyWallet(token('jwt-a'), request), 200);
});

test('level progress uses the server level and total experience', () => {
  assert.deepEqual(levelProgress(1, 0), { current: 0, needed: 100, remaining: 100, percent: 0 });
  assert.deepEqual(levelProgress(2, 250), { current: 150, needed: 200, remaining: 50, percent: 75 });
  assert.deepEqual(levelProgress(3, 300), { current: 0, needed: 300, remaining: 300, percent: 0 });
  assert.deepEqual(levelProgress(7, 50), { current: 0, needed: 700, remaining: 2750, percent: 0 });
  assert.equal(levelProgress(2, 9999).percent, 100);
});

test('lobby shows the coin slot and keeps the menu', () => {
  const html = ui.lobby();
  assert.match(html, /aria-label="보유 골드"/);
  assert.doesNotMatch(html, /lobby-coin[^>]*>준비 중/);
  assert.match(html, /GAME/);
  assert.match(html, /랭킹 보기/);
});

test('reward panel shows gold, experience, and level up from the server', () => {
  const html = ui.panel(applied(), false);
  assert.match(html, /경기 1위/);
  assert.match(html, /GOLD \+100/);
  assert.match(html, /500 → 600 GOLD/);
  assert.match(html, /EXP \+100/);
  assert.match(html, /200 → 300 EXP/);
  assert.match(html, /LEVEL UP!/);
  assert.match(html, /LEVEL <b>2<\/b> → LEVEL <b class="match-level-new">3<\/b>/);
  assert.doesNotMatch(html, /is-celebrating/);
  assert.match(ui.panel(applied(), true), /is-level-up is-celebrating/);
});

test('reward panel without a level up shows no celebration', () => {
  const html = ui.panel(applied(NO_LEVEL_UP), true);
  assert.match(html, /GOLD \+60/);
  assert.match(html, /LEVEL 2 유지/);
  assert.doesNotMatch(html, /LEVEL UP|is-level-up|is-celebrating/);
});

test('pending, unrewarded, not settled, and broken rewards show no numbers', () => {
  const states = [
    matchRewardState(null, ROOM),
    matchRewardState(readRewardSettlement(wire()), 'other-room'),
    matchRewardState({ status: 'unrewarded', roomId: ROOM, reason: 'not_settled' }, ROOM),
    matchRewardState({ status: 'unrewarded', roomId: ROOM, reason: 'unrewarded_match' }, ROOM),
    matchRewardState({ status: 'error', roomId: ROOM, message: '보상 정산 정보를 확인할 수 없습니다.' }, ROOM),
  ];
  assert.deepEqual(states.map((s) => s.status), ['pending', 'pending', 'pending', 'unrewarded', 'error']);
  const html = states.map((s) => ui.panel(s, false));
  assert.match(html[0], /보상 정산 확인 중/);
  assert.match(html[2], /보상 정산 확인 중/);
  assert.match(html[3], /보상 미적용/);
  assert.match(html[4], /확인할 수 없습니다/);
  for (const markup of html) assert.doesNotMatch(markup, /GOLD \+|EXP \+|\d+ → \d+/);
});

function resultSession(count, change = {}) {
  const players = Array.from({ length: count }, (_, i) => ({
    userId: i + 1, color: ['RED', 'BLUE', 'GREEN', 'YELLOW'][i], spawn: { x: 0, y: 0 }, characterId: `character_0${i + 1}`,
  }));
  const results = players.map((p, i) => ({
    userId: p.userId, rank: i + 1, isWinner: i === 0, paintedTiles: 10 - i, totalPainted: 10, kills: 0, deaths: 0, cardsUsed: 1, costUsed: 1,
  }));
  return { roomId: ROOM, userId: 1, phase: 'finished', connected: true, players, result: { roomId: ROOM, winnerId: 1, results }, ...change };
}

const RANK = {
  status: 'applied', roomId: ROOM,
  settlement: {
    roomId: ROOM, matchId: ROOM, placement: 1, rpBefore: 1470, placementRpChange: 30, rpChange: 30, rpAfter: 1500,
    tierBefore: 'DIAMOND', divisionBefore: 3, tierAfter: 'MASTER', divisionAfter: null,
  },
};

test('four-player results show RP and rewards together', () => {
  const html = ui.result(resultSession(4, { rankSettlement: RANK, rewardSettlement: readRewardSettlement(wire()) }));
  assert.match(html, /aria-label="랭크 결과"/);
  assert.match(html, /\+30 RP/);
  assert.match(html, /승급!/);
  assert.match(html, /aria-label="경기 보상"/);
  assert.match(html, /GOLD \+100/);
  assert.match(html, /LEVEL UP!/);
  assert.equal((html.match(/data-user=/g) ?? []).length, 4);
});

test('two-player results explain unranked RP but still show rewards', () => {
  const reward = readRewardSettlement(wire({ placement: 2, gold_earned: 40, gold_after: 540, experience_earned: 40, experience_after: 240, level_after: 2, leveled_up: false }));
  const html = ui.result(resultSession(2, { rankSettlement: { status: 'unranked', roomId: ROOM, reason: 'unranked_match' }, rewardSettlement: reward }));
  assert.match(html, /랭크 미적용/);
  assert.match(html, /골드·경험치 보상은 정상 지급됩니다/);
  assert.match(html, /GOLD \+40/);
  assert.match(html, /EXP \+40/);
  assert.doesNotMatch(html, /보상 미적용/);
});

test('result page waits for the reward and ignores other rooms', () => {
  assert.match(ui.result(resultSession(4)), /보상 정산 확인 중/);
  const other = { ...readRewardSettlement(wire({ room_id: 'x', match_id: 'x' })) };
  assert.match(ui.result(resultSession(4, { rewardSettlement: other })), /보상 정산 확인 중/);
  assert.doesNotMatch(ui.result(resultSession(4, { result: null })), /aria-label="경기 보상"/);
});

test('server render never starts the celebration', () => {
  const html = ui.result(resultSession(4, { rewardSettlement: readRewardSettlement(wire()) }));
  assert.match(html, /is-level-up/);
  assert.doesNotMatch(html, /is-celebrating/);
});

test('celebration plays once per match, across re-render, reconnect, and reload', () => {
  const values = new Map();
  const store = { getItem: (k) => values.get(k) ?? null, setItem: (k, v) => values.set(k, v) };
  assert.equal(claimCelebration('match-1', store), true);
  assert.equal(claimCelebration('match-1', store), false);
  assert.equal(claimCelebration('match-2', store), true);
  values.set('paint-rush:level-up-shown', JSON.stringify(['match-3']));
  assert.equal(claimCelebration('match-3', store), false);
  assert.equal(claimCelebration('match-4', null), true);
  assert.equal(claimCelebration('match-4', null), false);
  const broken = { getItem: () => { throw new Error('blocked'); }, setItem: () => {} };
  assert.equal(claimCelebration('match-5', broken), true);
});

test('celebration only targets matches with a level up', () => {
  assert.equal(celebrationMatch(applied()), ROOM);
  assert.equal(celebrationMatch(applied(NO_LEVEL_UP)), null);
  assert.equal(celebrationMatch({ status: 'pending' }), null);
  assert.equal(celebrationMatch(null), null);
});
