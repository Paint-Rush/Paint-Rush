import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { GameSocket } from '../src/services/gameSocket.ts';

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
      contents: "import { createElement } from 'react'; import { renderToStaticMarkup } from 'react-dom/server'; import GamePage from './src/pages/GamePage'; export { bombArea, gameShortcut, nextSelection, toGameState, ultimateCenter, ultimateChanges, ultimateTargets, unpaintable } from './src/game/roomView'; export { parseMap } from './src/game/model'; export { readRoomState, initialSession } from './src/services/gameSocket'; export function render(session, socket, cards) { return renderToStaticMarkup(createElement(GamePage, { session, socket, onExit: () => {}, initialCards: cards })); }",
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
  const dir = await mkdtemp(join(tmpdir(), 'paint-rush-ultimate-'));
  const file = join(dir, 'ultimate.cjs');
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
const ULTIMATES = {
  character_01: { ultimate_id: 'ultimate_01', ultimate_name: '테스트 궁극기 1' },
  character_02: { ultimate_id: 'ultimate_02', ultimate_name: '테스트 궁극기 2' },
};

function player(id, overrides = {}) {
  const spawn = id === 1 ? [0, 0] : [19, 19];
  const characterId = overrides.character_id ?? 'character_01';
  return {
    user_id: id, color: id === 1 ? 'RED' : 'BLUE', spawn, position: spawn, alive: true, connected: true, tile_count: 0,
    character_id: characterId, character_name: id === 1 ? '페인터' : '상대', ...ULTIMATES[characterId],
    hp: 3, max_hp: 3, knocked_out: false, respawn_remaining_seconds: 0, ...overrides,
  };
}

function roomMessage(overrides = {}, players = [player(1), player(2)]) {
  return {
    type: 'room_state', room_id: ROOM, status: 'PLAYING', map_id: 'default',
    map: { width: 20, height: 20, tiles: Array.from({ length: 20 }, () => Array(20).fill('NORMAL')) },
    players, my_cost: 8, my_hand: ['move_dash', 'attack_slash', 'move_jump'], my_next_card: 'attack_bomb',
    my_ultimate_used: false, tile_owners: Array.from({ length: 20 }, () => Array(20).fill(null)),
    started_at: null, ready_remaining_seconds: 0, remaining_seconds: 200, max_cost: 10, ...overrides,
  };
}

class FakeSocket {
  readyState = 0;
  sent = [];
  onopen = null;
  onmessage = null;
  onerror = null;
  onclose = null;
  send(data) { this.sent.push(JSON.parse(data)); }
  close() { this.readyState = 3; }
  open() { this.readyState = 1; this.onopen?.({}); }
  receive(message) { this.onmessage?.({ data: JSON.stringify(message) }); }
  drop() { this.readyState = 3; this.onclose?.({ code: 1006, reason: '' }); }
  ultimates() { return this.sent.filter((message) => message.type === 'ultimate_use'); }
}

function playing(message = roomMessage()) {
  const sockets = [];
  const session = new GameSocket(() => { const socket = new FakeSocket(); sockets.push(socket); return socket; }, 'ws://localhost/ws/game');
  session.start('jwt', 'character_01');
  const socket = sockets[0];
  socket.open();
  socket.receive({ type: 'auth_success', user_id: 1 });
  socket.receive({ type: 'queue_waiting', waiting: 1, match_size: 2 });
  socket.receive({ type: 'match_found', room_id: ROOM, map_id: 'default', players: [player(1), player(2)] });
  socket.receive(message);
  return { session, socket };
}

function fakeSocket() {
  return { onEvent() { return () => {}; }, playCard() { return true; }, castUltimate() { return true; }, sync() { return true; }, retry() { return true; }, leaveRoom() { return true; } };
}

function render(message, overrides = {}) {
  const room = game.readRoomState(message, Date.now());
  return game.render({ ...game.initialSession(), phase: 'started', connected: true, userId: 1, roomId: ROOM, room, ...overrides }, fakeSocket(), CARDS);
}

function grid(glass = [], blocked = []) {
  const tiles = Array.from({ length: 20 }, () => Array(20).fill('NORMAL'));
  for (const [x, y] of glass) tiles[y][x] = 'GLASS';
  for (const [x, y] of blocked) tiles[y][x] = 'BLOCKED';
  return game.parseMap({ map_id: 'default', width: 20, height: 20, tiles });
}

const key = (code, extra = {}) => game.gameShortcut({ key: code === 'KeyQ' ? 'q' : code, code, repeat: false, ...extra }, false);
const sorted = (points) => points.map(({ x, y }) => `${x},${y}`).sort();
const ultimateButton = (html) => html.match(/<button[^>]*class="battle-ultimate[^"]*"[^>]*>/)[0];

test('Q selects the ultimate, ignoring key repeat, modifiers and text inputs', () => {
  assert.deepEqual(key('KeyQ'), { type: 'ultimate' });
  assert.equal(key('KeyQ', { repeat: true }), null);
  assert.equal(key('KeyQ', { ctrlKey: true }), null);
  assert.equal(game.gameShortcut({ key: 'q', code: 'KeyQ', repeat: false }, true), null);
  assert.deepEqual(key('Space'), { type: 'use' });
});

test('Q toggles the ultimate and stays mutually exclusive with hand cards', () => {
  const card = { index: 1, id: 'attack_slash' };
  assert.equal(game.nextSelection(null, 'ultimate', true), 'ultimate');
  assert.equal(game.nextSelection('ultimate', 'ultimate', true), null);
  assert.deepEqual(game.nextSelection('ultimate', card, true), card);
  assert.equal(game.nextSelection(card, 'ultimate', true), 'ultimate');
  assert.equal(game.nextSelection(card, card, true), null);
  assert.equal(game.nextSelection(null, 'ultimate', false), null);
  assert.deepEqual(game.nextSelection(card, 'ultimate', false), card);
});

test('color burst is always centered on the player and sends no target', () => {
  const map = grid([[1, 0]], [[0, 1]]);
  const origin = { x: 0, y: 0 };
  assert.deepEqual(game.ultimateCenter(map, origin, 'ultimate_01', { x: 9, y: 9 }), origin);
  assert.deepEqual(game.ultimateCenter(map, origin, 'ultimate_01', null), origin);
  const area = game.bombArea(map, origin);
  const owners = Array.from({ length: 20 }, () => Array(20).fill(null));
  owners[1][1] = 2;
  assert.deepEqual(sorted(game.ultimateChanges(map, owners, area, 1, 'ultimate_01')), ['0,0', '1,1']);
  assert.deepEqual(sorted(game.unpaintable(map, area)), ['0,1', '1,0']);

  const { session, socket } = playing();
  assert.equal(session.castUltimate(), true);
  assert.deepEqual(socket.ultimates(), [{ type: 'ultimate_use' }]);
  session.close();
});

test('color steal targets within Manhattan 3 and sends the chosen center', () => {
  const map = grid([[6, 5]]);
  const origin = { x: 5, y: 5 };
  const targets = game.ultimateTargets(map, origin, 'ultimate_02');
  assert.equal(targets.length, 25);
  assert.ok(targets.every(({ x, y }) => Math.abs(x - 5) + Math.abs(y - 5) <= 3));
  assert.deepEqual(game.ultimateCenter(map, origin, 'ultimate_02', { x: 7, y: 6 }), { x: 7, y: 6 });
  assert.equal(game.ultimateCenter(map, origin, 'ultimate_02', { x: 7, y: 7 }), null);
  assert.equal(game.ultimateCenter(map, origin, 'ultimate_02', null), null);
  const owners = Array.from({ length: 20 }, () => Array(20).fill(null));
  owners[6][7] = 2;
  owners[5][6] = 2;
  owners[5][7] = 1;
  const area = game.bombArea(map, { x: 7, y: 6 });
  assert.deepEqual(sorted(game.ultimateChanges(map, owners, area, 1, 'ultimate_02')), ['7,6']);
  assert.deepEqual(game.ultimateChanges(map, Array.from({ length: 20 }, () => Array(20).fill(null)), area, 1, 'ultimate_02'), []);

  const { session, socket } = playing(roomMessage({}, [player(1, { character_id: 'character_02', position: [5, 5] }), player(2)]));
  assert.equal(session.castUltimate({ x: 7, y: 6 }), true);
  assert.deepEqual(socket.ultimates(), [{ type: 'ultimate_use', target_x: 7, target_y: 6 }]);
  session.close();
});

test('Space and left click on the board confirm the ultimate through the existing input paths', async () => {
  const scene = await readFile(new URL('../src/game/BattleScene.tsx', import.meta.url), 'utf8');
  assert.match(scene, /if \(special\) \{\s+fire\(camera\.current\?\.pointer\(\) \?\? null\);/);
  assert.match(scene, /if \(aiming\) \{ fireUltimate\(point\); return; \}/);
  assert.match(scene, /onSelect=\{useAt\}/);
  assert.match(scene, /if \(ultimateId === COLOR_BURST\) \{ onUseUltimate\(\); clearCard\(\); return; \}/);
  assert.match(scene, /onUseUltimate\(center\);/);
  assert.match(scene, /onClick=\{toggleUltimate\}/);
  assert.match(scene, /if \(event\.key === 'Escape'\) clearCard\(\)/);
  assert.match(scene, /const clearCard = \(\) => \{ setActive\(null\); setUltimateOn\(false\);/);
  assert.doesNotMatch(scene, /castUltimate|card_use|ultimate_use/);
  const page = await readFile(new URL('../src/pages/GamePage.tsx', import.meta.url), 'utf8');
  assert.match(page, /if \(pending \|\| pendingTimer\.current\) return;\s+if \(!socket\.castUltimate\(target\)\)/);
});

test('a used ultimate cannot be selected or sent again', () => {
  const { session, socket } = playing(roomMessage({ my_ultimate_used: true }));
  assert.equal(session.castUltimate(), false);
  assert.deepEqual(socket.ultimates(), []);
  const html = render(roomMessage({ my_ultimate_used: true }));
  assert.match(ultimateButton(html), /disabled/);
  assert.match(html, /<span class="ultimate-status">사용 완료<\/span>/);
  session.close();
});

test('knockout, READY, FINISHED and a dropped connection block the ultimate', () => {
  const knocked = roomMessage({}, [player(1, { hp: 0, alive: false, knocked_out: true, respawn_remaining_seconds: 5 }), player(2)]);
  for (const [message, phase] of [[knocked, 'started'], [roomMessage({ status: 'READY', ready_remaining_seconds: 5 }), 'ready'], [roomMessage({ status: 'FINISHED', remaining_seconds: 0 }), 'finished']]) {
    const { session, socket } = playing(message);
    assert.equal(session.castUltimate(), false);
    assert.deepEqual(socket.ultimates(), []);
    assert.match(ultimateButton(render(message, { phase })), /disabled/);
    session.close();
  }
  assert.match(render(knocked), /<span class="ultimate-status">기절<\/span>/);
  assert.match(ultimateButton(render(roomMessage(), { connected: false })), /disabled/);
  const { session, socket } = playing();
  socket.drop();
  assert.equal(session.castUltimate(), false);
  assert.deepEqual(socket.ultimates(), []);
  session.close();
});

test('the HUD shows the server ultimate name apart from the three hand cards', () => {
  const html = render(roomMessage());
  const button = ultimateButton(html);
  assert.doesNotMatch(button, /disabled/);
  assert.match(button, /aria-label="궁극기 테스트 궁극기 1 사용 가능"/);
  assert.match(html, /<small>ULTIMATE<\/small><strong>테스트 궁극기 1<\/strong><span class="ultimate-status">사용 가능<\/span><em>Q<\/em>/);
  assert.equal((html.match(/class="server-hand-card[ "]/g) ?? []).length, 3);
  assert.match(html, /<kbd>Q<\/kbd> 궁극기/);
  const unknown = render(roomMessage({}, [player(1, { ultimate_id: null, ultimate_name: null }), player(2)]));
  assert.match(ultimateButton(unknown), /disabled/);
});

test('a rejected target or server error keeps the ultimate available', () => {
  const { session, socket } = playing(roomMessage({}, [player(1, { character_id: 'character_02', position: [5, 5] }), player(2)]));
  assert.equal(session.castUltimate({ x: 5.5, y: 6 }), false);
  assert.deepEqual(socket.ultimates(), []);
  assert.equal(session.castUltimate({ x: 9, y: 6 }), true);
  socket.receive({ type: 'error', code: 'ultimate_out_of_range', message: '궁극기 사용 가능한 거리를 벗어났습니다.' });
  assert.equal(session.getState().room.myUltimateUsed, false);
  assert.equal(session.getState().warning, '궁극기 사용 가능한 거리를 벗어났습니다.');
  assert.notEqual(session.getState().phase, 'error');
  assert.equal(session.castUltimate({ x: 7, y: 6 }), true);
  session.close();
});

test('ultimate_activated in the extended server format marks only the caster as used', () => {
  const { session, socket } = playing();
  const events = [];
  session.onEvent((message) => events.push(message));
  const activated = (userId, extra = {}) => ({
    type: 'ultimate_activated', room_id: ROOM, user_id: userId, character_id: 'character_01', ultimate_id: 'ultimate_01',
    target_x: 19, target_y: 19, affected_tiles: [[18, 18], [19, 18], [18, 19], [19, 19]], changed_tiles: [[19, 19]], ...extra,
  });
  socket.receive(activated(2));
  assert.equal(session.getState().room.myUltimateUsed, false);
  assert.equal(events.length, 1);
  socket.receive(activated(1, { room_id: 'other-room' }));
  assert.equal(session.getState().room.myUltimateUsed, false);
  socket.receive(activated(1, { changed_tiles: 'bad' }));
  assert.equal(session.getState().room.myUltimateUsed, false);
  assert.match(session.getState().warning, /ultimate_activated/);
  socket.receive(activated(1, { target_x: 0, target_y: 0, affected_tiles: [[0, 0], [1, 0], [0, 1], [1, 1]], changed_tiles: [] }));
  assert.equal(session.getState().room.myUltimateUsed, true);
  assert.equal(session.getState().phase, 'started');
  assert.equal(session.getState().room.tileOwners[0][0], null);
  session.close();
});

test('ultimate tiles arrive only through tiles_updated', () => {
  const { session, socket } = playing();
  socket.receive({ type: 'ultimate_activated', room_id: ROOM, user_id: 1, character_id: 'character_01', ultimate_id: 'ultimate_01', target_x: 0, target_y: 0, affected_tiles: [[0, 0], [1, 0]], changed_tiles: [[0, 0], [1, 0]] });
  assert.equal(session.getState().room.tileOwners[0][0], null);
  socket.receive({ type: 'tiles_updated', room_id: ROOM, tiles: [{ x: 0, y: 0, previous_owner: null, new_owner: 1 }, { x: 1, y: 0, previous_owner: 2, new_owner: 1 }] });
  const { room } = session.getState();
  assert.equal(room.tileOwners[0][0], 1);
  assert.equal(room.tileOwners[0][1], 1);
  assert.equal(room.players[0].tileCount, 2);
  session.close();
});

test('room_state restores the used ultimate after reconnect', () => {
  const { session, socket } = playing();
  socket.receive({ type: 'ultimate_activated', room_id: ROOM, user_id: 1, character_id: 'character_01', ultimate_id: 'ultimate_01' });
  socket.receive(roomMessage({ my_ultimate_used: false }));
  assert.equal(session.getState().room.myUltimateUsed, false);
  socket.receive(roomMessage({ my_ultimate_used: true }));
  assert.equal(session.getState().room.myUltimateUsed, true);
  const state = game.toGameState(session.getState().room, 1);
  assert.equal(state.ultimateUsed, true);
  assert.equal(state.players[0].ultimateId, 'ultimate_01');
  assert.equal(state.players[0].ultimateName, '테스트 궁극기 1');
  assert.equal(session.castUltimate(), false);
  session.close();
});
