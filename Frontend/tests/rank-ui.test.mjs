import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const root = fileURLToPath(new URL('../', import.meta.url));

async function loadRenderers() {
  const result = await build({
    stdin: {
      contents: `
        import { createElement } from 'react';
        import { renderToStaticMarkup } from 'react-dom/server';
        import LobbyPage from './src/pages/LobbyPage';
        import ResultPage from './src/pages/ResultPage';
        import { RankingView } from './src/pages/RankingPage';
        import { MatchRankPanel, RankSummary } from './src/components/RankWidgets';
        import { initialSession } from './src/services/gameSocket';
        const noop = () => {};
        const token = { access_token: 't', token_type: 'bearer' };
        export const summary = (rank) => renderToStaticMarkup(createElement(RankSummary, { rank }));
        export const panel = (state) => renderToStaticMarkup(createElement(MatchRankPanel, { state }));
        export const view = (props) => renderToStaticMarkup(createElement(RankingView, { tab: 'all', page: 0, list: null, history: null, onTab: noop, onPage: noop, onRetry: noop, onBack: noop, ...props }));
        export const lobby = () => renderToStaticMarkup(createElement(LobbyPage, { token, onGame: noop, onLogout: noop, onRanking: noop, loadRank: () => new Promise(noop) }));
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
  const dir = await mkdtemp(join(tmpdir(), 'paint-rush-rank-'));
  const file = join(dir, 'rank-ui.cjs');
  await writeFile(file, result.outputFiles[0].text);
  const module = (await import(pathToFileURL(file).href)).default;
  await rm(dir, { recursive: true, force: true });
  return module;
}

const ui = await loadRenderers();

function rank(change = {}) {
  return { userId: 7, totalRp: 329, tier: 'SILVER', division: 1, nextTier: 'SILVER', nextDivision: 2, rpToNext: 71, rankingPosition: null, ...change };
}

function change(overrides = {}) {
  return { matchId: 'room', placement: 1, rpBefore: 299, placementRpChange: 30, rpChange: 30, rpAfter: 329, createdAt: '2026-11-20T10:00:00+00:00', ...overrides };
}

function entries(count, first = 1, tier = 'MASTER') {
  return Array.from({ length: count }, (_, i) => ({
    position: first + i, userId: first + i, username: `p${first + i}`, displayName: `플레이어${first + i}`, totalRp: 3000 - first - i, tier,
  }));
}

const ready = (data) => ({ status: 'ready', data });

test('summary shows tier, division, RP, and progress', () => {
  const html = ui.summary(rank());
  assert.match(html, /Silver 1/);
  assert.match(html, /329 RP/);
  assert.match(html, /aria-valuenow="29"/);
  assert.match(html, /Silver 2까지 71 RP/);
  assert.doesNotMatch(html, /전체 \d+위/);
});

test('master and challenger have no division and show the ranking position', () => {
  const master = ui.summary(rank({ totalRp: 1650, tier: 'MASTER', division: null, nextTier: 'CHALLENGER', nextDivision: null, rpToNext: 150, rankingPosition: 4 }));
  assert.match(master, /<strong>Master<\/strong>/);
  assert.match(master, /전체 4위/);
  assert.match(master, /Challenger 최소 조건까지 150 RP/);
  const challenger = ui.summary(rank({ totalRp: 2100, tier: 'CHALLENGER', division: null, nextTier: null, nextDivision: null, rpToNext: null, rankingPosition: 3 }));
  assert.match(challenger, /rank-summary is-challenger/);
  assert.match(challenger, /<strong>Challenger<\/strong>/);
  assert.match(challenger, /상위 100위 경쟁 중/);
  assert.doesNotMatch(challenger, /role="progressbar"/);
});

test('lobby shows a rank area with a ranking button while loading', () => {
  const html = ui.lobby();
  assert.match(html, /aria-label="내 랭크"/);
  assert.match(html, /랭크 불러오는 중/);
  assert.match(html, /랭킹 보기/);
  assert.match(html, /GAME/);
});

test('ranking lists server order, highlights me, and paginates', () => {
  const html = ui.view({ myRank: ready(rank({ userId: 3 })), list: ready({ total: 250, rankings: entries(100, 101) }), page: 1 });
  const positions = [...html.matchAll(/rank-row-position">(\d+)</g)].map((m) => Number(m[1]));
  assert.equal(positions.length, 100);
  assert.deepEqual(positions.slice(0, 3), [101, 102, 103]);
  assert.match(html, /2 \/ 3/);
  const mine = ui.view({ myRank: ready(rank({ userId: 2 })), list: ready({ total: 3, rankings: entries(3) }) });
  assert.match(mine, /data-user="2" aria-current="true"/);
  assert.equal((mine.match(/aria-current="true"/g) ?? []).length, 1);
  assert.doesNotMatch(mine, /ranking-pages/);
  assert.equal((mine.match(/class="rank-row /g) ?? []).length, 3);
});

test('challenger tab is emphasized and empty states never invent rows', () => {
  const html = ui.view({ tab: 'challengers', myRank: ready(rank()), list: ready({ total: 2, rankings: entries(2, 1, 'CHALLENGER') }) });
  assert.match(html, /ranking-body is-challengers/);
  assert.match(html, /1800 RP 이상/);
  assert.equal((html.match(/rank-row is-challenger/g) ?? []).length, 2);
  assert.match(ui.view({ myRank: ready(rank()), list: ready({ total: 0, rankings: [] }) }), /아직 Master 이상 플레이어가 없습니다/);
  assert.match(ui.view({ tab: 'challengers', myRank: ready(rank()), list: ready({ total: 0, rankings: [] }) }), /아직 챌린저가 없습니다/);
});

test('loading and error states', () => {
  const loading = ui.view({ myRank: { status: 'loading' }, list: { status: 'loading' } });
  assert.match(loading, /내 랭크 불러오는 중/);
  assert.match(loading, /불러오는 중…/);
  const failed = ui.view({ myRank: { status: 'error', message: '서버에 연결할 수 없습니다.' }, list: { status: 'error', message: '랭크 정보를 불러오지 못했습니다.' } });
  assert.match(failed, /서버에 연결할 수 없습니다/);
  assert.match(failed, /랭크 정보를 불러오지 못했습니다/);
  assert.match(failed, /다시 시도/);
});

test('RP history shows placement, before, applied change, after, and date', () => {
  const html = ui.view({ tab: 'history', myRank: ready(rank()), history: ready([
    change(),
    change({ matchId: 'floor', placement: 4, rpBefore: 10, placementRpChange: -30, rpChange: -10, rpAfter: 0 }),
  ]) });
  assert.match(html, /1위/);
  assert.match(html, /\+30 RP/);
  assert.match(html, /299 → 329 RP/);
  assert.match(html, /4위/);
  assert.match(html, /is-down">-10 RP/);
  assert.match(html, /순위 보상 -30 RP · 최저 0 RP 적용/);
  assert.doesNotMatch(html, /is-down">-30 RP/);
  assert.match(html, /dateTime="2026-11-20T10:00:00\+00:00"/);
  assert.match(ui.view({ tab: 'history', myRank: ready(rank()), history: ready([]) }), /4인 경기가 없습니다/);
});

function applied(change = {}) {
  return {
    status: 'applied',
    settlement: {
      roomId: 'room-x', matchId: 'room-x', placement: 1, rpBefore: 1470, placementRpChange: 30, rpChange: 30, rpAfter: 1500,
      tierBefore: 'DIAMOND', divisionBefore: 3, tierAfter: 'MASTER', divisionAfter: null, ...change,
    },
  };
}

test('match panel shows a promotion with RP before and after', () => {
  const html = ui.panel(applied());
  assert.match(html, /경기 1위/);
  assert.match(html, /is-up">\+30 RP/);
  assert.match(html, /이전 RP<\/dt><dd>1,470/);
  assert.match(html, /현재 RP<\/dt><dd>1,500/);
  assert.match(html, /DIAMOND 3<\/span><span aria-hidden="true">→<\/span>/);
  assert.match(html, /<span>MASTER<\/span>/);
  assert.match(html, /승급!/);
  assert.match(html, /is-move-up/);
});

test('match panel shows a demotion and the bronze floor', () => {
  const html = ui.panel(applied({ placement: 4, rpBefore: 600, placementRpChange: -30, rpChange: -30, rpAfter: 570, tierBefore: 'GOLD', divisionBefore: 1, tierAfter: 'SILVER', divisionAfter: 3 }));
  assert.match(html, /is-down">-30 RP/);
  assert.match(html, /GOLD 1/);
  assert.match(html, /SILVER 3/);
  assert.match(html, /강등/);
  const floor = ui.panel(applied({ placement: 3, rpBefore: 5, placementRpChange: -10, rpChange: -5, rpAfter: 0, tierBefore: 'BRONZE', divisionBefore: 1, tierAfter: 'BRONZE', divisionAfter: 1 }));
  assert.match(floor, /-5 RP/);
  assert.match(floor, /최저 0 RP 적용/);
  assert.match(floor, /현재 티어 유지/);
  assert.doesNotMatch(floor, /→/);
});

test('match panel never shows RP numbers without server data', () => {
  for (const state of [{ status: 'pending' }, { status: 'error', message: '랭크 정산 정보를 확인할 수 없습니다.' }, { status: 'unranked', reason: 'unranked_match' }]) {
    assert.doesNotMatch(ui.panel(state), /[+-]\d+ RP|현재 RP/);
  }
  assert.match(ui.panel({ status: 'unranked', reason: 'unranked_match' }), /랭크 미적용/);
  assert.match(ui.panel({ status: 'error', message: '랭크 정산 정보를 확인할 수 없습니다.' }), /확인할 수 없습니다/);
});

function resultSession(count) {
  const players = Array.from({ length: count }, (_, i) => ({
    userId: i + 1, color: ['RED', 'BLUE', 'GREEN', 'YELLOW'][i], spawn: { x: 0, y: 0 }, characterId: `character_0${i + 1}`,
  }));
  const results = players.map((player, i) => ({
    userId: player.userId, rank: i + 1, isWinner: i === 0, paintedTiles: 10 - i, totalPainted: 10, kills: 0, deaths: 0, cardsUsed: 1, costUsed: 1,
  }));
  return { roomId: 'room-x', userId: 1, phase: 'finished', connected: true, players, result: { roomId: 'room-x', winnerId: 1, results } };
}

test('result page waits for the server settlement and ignores other rooms', () => {
  for (const count of [2, 4]) {
    const html = ui.result(resultSession(count));
    assert.match(html, /RP 정산 확인 중/);
    assert.doesNotMatch(html, /[+-]\d+ RP/);
  }
  const other = ui.result({ ...resultSession(4), rankSettlement: { status: 'applied', roomId: 'room-other', settlement: applied().settlement } });
  assert.match(other, /RP 정산 확인 중/);
  assert.doesNotMatch(ui.result({ ...resultSession(4), result: null }), /aria-label="랭크 결과"/);
});

test('result page shows the settlement for this match', () => {
  const html = ui.result({ ...resultSession(4), rankSettlement: { status: 'applied', roomId: 'room-x', settlement: applied().settlement } });
  assert.match(html, /\+30 RP/);
  assert.match(html, /승급!/);
  assert.match(html, /로비로 돌아가면 최신 골드가 반영됩니다/);
  const unranked = ui.result({ ...resultSession(2), rankSettlement: { status: 'unranked', roomId: 'room-x', reason: 'unranked_match' } });
  assert.match(unranked, /랭크 미적용/);
  assert.doesNotMatch(unranked, /RP 정산 확인 중/);
});

test('match panel shows RP only for legacy records without tiers', () => {
  const html = ui.panel(applied({ tierBefore: null, divisionBefore: null, tierAfter: null, divisionAfter: null }));
  assert.match(html, /\+30 RP/);
  assert.match(html, /티어 변화 정보가 없습니다/);
  assert.doesNotMatch(html, /승급|강등|현재 티어 유지|→/);
});
