import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const root = fileURLToPath(new URL('../', import.meta.url));

const pixiStub = {
  name: 'pixi-stub',
  setup(builder) {
    builder.onResolve({ filter: /^pixi\.js$/ }, () => ({ path: 'pixi', namespace: 'stub' }));
    builder.onLoad({ filter: /.*/, namespace: 'stub' }, () => ({ contents: 'export class Application {} export class Container {} export class Graphics {} export class Point {}', loader: 'js' }));
  },
};

async function loadModule() {
  const result = await build({
    stdin: {
      contents: "import { createElement } from 'react'; import { renderToStaticMarkup } from 'react-dom/server'; import GamePage from './src/pages/GamePage'; export { toGameState, moveTargets } from './src/game/roomView'; export { loadCards, cardName } from './src/services/cards'; export { readRoomState, initialSession } from './src/services/gameSocket'; export function render(session, socket, cards) { return renderToStaticMarkup(createElement(GamePage, { session, socket, onExit: () => {}, initialCards: cards })); }",
      resolveDir: root,
      loader: 'tsx',
    },
    bundle: true,
    write: false,
    format: 'cjs',
    platform: 'node',
    jsx: 'automatic',
    loader: { '.css': 'empty', '.png': 'dataurl' },
    plugins: [pixiStub],
    define: { 'process.env.NODE_ENV': '"production"' },
    logLevel: 'silent',
  });
  const dir = await mkdtemp(join(tmpdir(), 'paint-rush-game-'));
  const file = join(dir, 'game-page.cjs');
  await writeFile(file, result.outputFiles[0].text);
  const module = (await import(pathToFileURL(file).href)).default;
  await rm(dir, { recursive: true, force: true });
  return module;
}

const game = await loadModule();
const ROOM = '7f9c2b1e-0000-4000-8000-000000000001';
const CARDS = {
  move_dash: { cardId: 'move_dash', type: 'MOVE', cost: 2 },
  move_jump: { cardId: 'move_jump', type: 'MOVE', cost: 3 },
  attack_slash: { cardId: 'attack_slash', type: 'ATTACK', cost: 3 },
  attack_bomb: { cardId: 'attack_bomb', type: 'ATTACK', cost: 5 },
};

function roomMessage(overrides = {}) {
  const owners = Array.from({ length: 20 }, () => Array(20).fill(null));
  owners[0][0] = 1;
  owners[19][19] = 2;
  const tiles = Array.from({ length: 20 }, () => Array(20).fill('NORMAL'));
  tiles[1][0] = 'BLOCKED';
  return {
    type: 'room_state',
    room_id: ROOM,
    status: 'PLAYING',
    map_id: 'default',
    map: { width: 20, height: 20, tiles },
    players: [
      { user_id: 1, color: 'RED', spawn: [0, 0], position: [0, 0], alive: true, connected: true, tile_count: 1, character_id: 'character_01', character_name: '페인터', ultimate_id: null, ultimate_name: null },
      { user_id: 2, color: 'BLUE', spawn: [19, 19], position: [19, 19], alive: true, connected: true, tile_count: 1, character_id: 'character_02', character_name: null, ultimate_id: null, ultimate_name: null },
    ],
    my_cost: 3,
    my_hand: ['move_dash', 'attack_bomb', 'move_jump'],
    my_next_card: 'attack_slash',
    my_ultimate_used: false,
    tile_owners: owners,
    started_at: null,
    ready_remaining_seconds: 0,
    remaining_seconds: 125,
    max_cost: 10,
    ...overrides,
  };
}

function session(overrides = {}, room = roomMessage()) {
  return { ...game.initialSession(), phase: 'started', connected: true, userId: 1, roomId: ROOM, room: game.readRoomState(room), ...overrides };
}

function fakeSocket() {
  return { calls: [], onEvent() { return () => {}; }, playCard(...args) { this.calls.push(['playCard', ...args]); return true; }, sync() { this.calls.push(['sync']); return true; }, retry() { return true; }, leaveRoom() { return true; } };
}

test('converts the server room_state into the in-game view without client-made cards', () => {
  const state = game.toGameState(game.readRoomState(roomMessage()), 1, 50);
  assert.equal(state.phase, 'playing');
  assert.equal(state.map.width, 20);
  assert.equal(state.map.legend[String(state.map.tiles[1][0])].walkable, false);
  assert.deepEqual(state.players[0].position, { x: 0, y: 0 });
  assert.deepEqual(state.players[0].hand, ['move_dash', 'attack_bomb', 'move_jump']);
  assert.deepEqual(state.players[1].hand, []);
  assert.equal(state.players[0].cost, 3);
  assert.equal(state.nextCard, 'attack_slash');
  assert.equal(state.owners[19][19], 2);
  assert.equal(state.remainingMs, 125000);
  assert.equal(game.toGameState(game.readRoomState(roomMessage({ status: 'DEATHMATCH' })), 1).phase, 'deathmatch');
  assert.throws(() => game.toGameState(game.readRoomState(roomMessage()), 99));
});

test('move targets follow the server one-step BFS rule and skip blocked tiles', () => {
  const state = game.toGameState(game.readRoomState(roomMessage()), 1);
  assert.deepEqual(game.moveTargets(state.map, { x: 0, y: 0 }), [{ x: 1, y: 0 }]);
  assert.equal(game.moveTargets(state.map, { x: 10, y: 10 }).length, 4);
});

test('reads card id, type and cost only from the server catalog', async () => {
  const body = { deck_size: 6, cards: Object.values(CARDS).map(({ cardId, type, cost }) => ({ card_id: cardId, type, cost })) };
  const catalog = await game.loadCards(async (path) => {
    assert.equal(path, '/cards');
    return new Response(JSON.stringify(body));
  });
  assert.deepEqual(catalog, CARDS);
  await assert.rejects(game.loadCards(async () => new Response(JSON.stringify({ cards: [{ card_id: 'x', type: 'ULT', cost: 1 }] }))));
  await assert.rejects(game.loadCards(async () => new Response('{}', { status: 500 })));
  assert.equal(game.cardName('move_dash'), 'DASH');
  assert.equal(game.cardName('attack_bomb'), 'BOMB');
});

test('renders the PixiJS board host, HUD, cost, hand and next card from the shared socket state', () => {
  const socket = fakeSocket();
  const html = game.render(session(), socket, CARDS);
  assert.match(html, /class="game-app phase-playing"/);
  assert.match(html, /class="arena-canvas"/);
  assert.match(html, /02:05/);
  assert.match(html, /<b>3<\/b> \/ 10/);
  assert.match(html, /aria-label="1번 손패 DASH 사용"/);
  assert.match(html, /aria-label="2번 손패 BOMB 사용"[^>]*disabled/);
  assert.doesNotMatch(html, /aria-label="1번 손패 DASH 사용"[^>]*disabled/);
  assert.match(html, /NEXT<\/span><div><b>✸<\/b><strong>SLASH<\/strong><small>3<\/small>/);
  assert.match(html, /페인터 · <b data-testid="player-position">0, 0<\/b>/);
  assert.doesNotMatch(html, /CRISS CROSS|SWASTIKA|THE LINE/);
  assert.deepEqual(socket.calls, []);
});

test('disables cards until the catalog loads and shows disconnect, finished and result states', () => {
  const loading = game.render(session(), fakeSocket(), null);
  assert.equal((loading.match(/server-hand-card"[^>]*disabled/g) ?? []).length, 3);
  const lost = game.render(session({ phase: 'error', connected: false, error: { code: 'game_disconnected', message: '게임 서버 연결이 끊겼습니다.' } }), fakeSocket(), CARDS);
  assert.match(lost, /connection-overlay/);
  assert.match(lost, /게임 서버 연결이 끊겼습니다\./);
  const finished = game.render(session({ phase: 'finished', result: { winnerId: 1, results: [{ userId: 1, rank: 1, isWinner: true }, { userId: 2, rank: 2, isWinner: false }] } }, roomMessage({ status: 'FINISHED', remaining_seconds: 0 })), fakeSocket(), CARDS);
  assert.match(finished, /경기 종료 · 카드 사용 중지/);
  assert.match(finished, /승리! 1위/);
  assert.match(finished, /경기 정리/);
  const waiting = game.render(session({ phase: 'finished' }, roomMessage({ status: 'FINISHED', remaining_seconds: 0 })), fakeSocket(), CARDS);
  assert.match(waiting, /서버 결과를 기다립니다/);
});

test('the game page reuses the App socket and never opens its own connection', async () => {
  const app = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8');
  assert.match(app, /<GamePage session=\{match\} socket=\{gameSocket\}/);
  assert.match(app, /gameSocket\.resume\(next\.access_token\)/);
  for (const path of ['../src/pages/GamePage.tsx', '../src/game/BattleScene.tsx', '../src/game/GameCanvas.tsx', '../src/game/roomView.ts']) {
    const source = await readFile(new URL(path, import.meta.url), 'utf8');
    assert.doesNotMatch(source, /new WebSocket|new GameSocket|queue_join|\/auth/);
  }
  const config = await readFile(new URL('../vite.config.ts', import.meta.url), 'utf8');
  assert.equal(config.split("'/cards'").length - 1, 2);
});
