import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { GameSocket } from '../src/services/gameSocket.ts';

const root = fileURLToPath(new URL('../', import.meta.url));

async function loadRoomView() {
  const result = await build({
    stdin: { contents: "export { attackTargets, bombArea, bombHits, gameShortcut, moveTargets, selectCard, slashBlocked } from './src/game/roomView'; export { parseMap } from './src/game/model';", resolveDir: root, loader: 'ts' },
    bundle: true, write: false, format: 'cjs', platform: 'node', logLevel: 'silent',
  });
  const dir = await mkdtemp(join(tmpdir(), 'paint-rush-attack-'));
  const file = join(dir, 'room-view.cjs');
  await writeFile(file, result.outputFiles[0].text);
  const module = (await import(pathToFileURL(file).href)).default;
  await rm(dir, { recursive: true, force: true });
  return module;
}

const view = await loadRoomView();

class FakeSocket {
  static created = [];
  readyState = 0;
  sent = [];
  onopen = null;
  onmessage = null;
  onerror = null;
  onclose = null;
  constructor() { FakeSocket.created.push(this); }
  send(data) { this.sent.push(JSON.parse(data)); }
  close() { this.readyState = 3; }
  open() { this.readyState = 1; this.onopen?.({}); }
  receive(message) { this.onmessage?.({ data: JSON.stringify(message) }); }
}

const ROOM = '7f9c2b1e-0000-4000-8000-000000000001';
const players = [
  { user_id: 1, color: 'RED', spawn: [0, 0], character_id: 'character_01', character_name: null, ultimate_id: null, ultimate_name: null },
  { user_id: 2, color: 'BLUE', spawn: [19, 19], character_id: 'character_02', character_name: null, ultimate_id: null, ultimate_name: null },
];

function playing() {
  FakeSocket.created = [];
  const session = new GameSocket(() => new FakeSocket(), 'ws://localhost/ws/game');
  session.start('jwt-token', 'character_01');
  const socket = FakeSocket.created[0];
  socket.open();
  socket.receive({ type: 'auth_success', user_id: 1 });
  socket.receive({ type: 'match_found', room_id: ROOM, map_id: 'default', players });
  const owners = Array.from({ length: 20 }, () => Array(20).fill(null));
  owners[5][5] = 2;
  owners[5][6] = 2;
  owners[6][5] = 1;
  socket.receive({
    type: 'room_state', room_id: ROOM, status: 'PLAYING', map_id: 'default',
    map: { width: 20, height: 20, tiles: Array.from({ length: 20 }, () => Array(20).fill('NORMAL')) },
    players: players.map((player, index) => ({ ...player, position: index ? [19, 19] : [5, 6], alive: true, connected: true, tile_count: index ? 2 : 1 })),
    my_cost: 8, my_hand: ['attack_slash', 'move_dash', 'attack_bomb'], my_next_card: 'move_jump', my_ultimate_used: false,
    tile_owners: owners, started_at: null, ready_remaining_seconds: 0, remaining_seconds: 200, max_cost: 10,
  });
  return { session, socket };
}

test('tiles_updated with new_owner null clears the tile, while a missing new_owner is rejected', () => {
  const { session, socket } = playing();
  socket.receive({ type: 'tiles_updated', room_id: ROOM, tiles: [
    { x: 5, y: 5, previous_owner: 2, new_owner: null },
    { x: 7, y: 7, previous_owner: null, new_owner: 1 },
  ] });
  let { room } = session.getState();
  assert.equal(room.tileOwners[5][5], null);
  assert.equal(room.tileOwners[5][6], 2);
  assert.equal(room.tileOwners[7][7], 1);
  assert.deepEqual(room.players.map((player) => player.tileCount), [2, 1]);
  assert.equal(session.getState().warning, null);

  socket.receive({ type: 'tiles_updated', room_id: ROOM, tiles: [{ x: 5, y: 6, previous_owner: 2 }] });
  room = session.getState().room;
  assert.equal(room.tileOwners[5][6], 2);
  assert.match(session.getState().warning, /tiles_updated/);
  session.close();
});

test('attack_resolved is accepted as a known event without changing state', () => {
  const { session, socket } = playing();
  const seen = [];
  session.onEvent((event) => seen.push(event.type));
  const before = session.getState();
  socket.receive({ type: 'attack_resolved', room_id: ROOM, user_id: 1, card_id: 'attack_bomb', target_x: 5, target_y: 5, affected_tiles: [[4, 4], [5, 5]] });
  assert.equal(session.getState(), before);
  assert.deepEqual(seen, ['attack_resolved']);
  socket.receive({ type: 'attack_resolved', room_id: ROOM, user_id: 1, card_id: 'attack_bomb', target_x: 5, target_y: 5 });
  assert.match(session.getState().warning, /attack_resolved/);
  assert.equal(session.getState().room, before.room);
  session.close();
});

test('attack cards are sent with the real hand index and target, and errors keep hand, cost and tiles', () => {
  const { session, socket } = playing();
  assert.equal(session.playCard(2, { x: 7, y: 6 }), true);
  assert.deepEqual(socket.sent.at(-1), { type: 'card_use', hand_index: 2, target_x: 7, target_y: 6 });
  const room = session.getState().room;
  assert.deepEqual([room.myHand, room.myCost], [['attack_slash', 'move_dash', 'attack_bomb'], 8]);

  for (const code of ['target_required', 'invalid_attack_target', 'attack_out_of_range', 'unattackable_tile']) {
    socket.receive({ type: 'error', code, message: `${code} 메시지` });
    const state = session.getState();
    assert.equal(state.room, room);
    assert.equal(state.phase, 'started');
    assert.equal(state.warning, `${code} 메시지`);
  }
  session.close();
});

test('attack targets follow the server rules and move targets stay unchanged', () => {
  const tiles = Array.from({ length: 20 }, () => Array(20).fill('NORMAL'));
  tiles[4][5] = 'BLOCKED';
  tiles[5][6] = 'GLASS';
  const map = view.parseMap({ map_id: 'default', width: 20, height: 20, tiles });
  const keys = (points) => points.map(({ x, y }) => `${x},${y}`).sort();
  assert.deepEqual(keys(view.attackTargets(map, { x: 5, y: 5 }, 'attack_slash')), ['4,5', '5,6']);
  assert.deepEqual(keys(view.attackTargets(map, { x: 0, y: 0 }, 'attack_slash')), ['0,1', '1,0']);
  const bomb = view.attackTargets(map, { x: 5, y: 5 }, 'attack_bomb');
  assert.equal(bomb.length, 25);
  assert.ok(bomb.every(({ x, y }) => Math.abs(x - 5) + Math.abs(y - 5) <= 3));
  assert.ok(bomb.some(({ x, y }) => x === 5 && y === 4));
  assert.equal(view.attackTargets(map, { x: 0, y: 0 }, 'attack_bomb').length, 10);
  assert.deepEqual(view.attackTargets(map, { x: 5, y: 5 }, 'move_dash'), []);
  assert.deepEqual(keys(view.moveTargets(map, { x: 5, y: 5 })), ['4,5', '5,6', '6,5']);
});

test('the hand never sends a card without a board target', async () => {
  const scene = await readFile(new URL('../src/game/BattleScene.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(scene, /onUseCard\(index\)/);
  assert.match(scene, /onClick=\{\(\) => toggleCard\(index, id\)\}/);
  assert.match(scene, /onUseCard\(active\.index, point\)/);
  assert.match(scene, /attackTargets\(state\.map, self\.position, activeId\)/);
});

test('cards are selected only by click, toggled by a second click and replaced by another card', async () => {
  assert.deepEqual(view.selectCard(null, 1, 'attack_bomb'), { index: 1, id: 'attack_bomb' });
  assert.equal(view.selectCard({ index: 1, id: 'attack_bomb' }, 1, 'attack_bomb'), null);
  assert.deepEqual(view.selectCard({ index: 1, id: 'attack_bomb' }, 0, 'move_dash'), { index: 0, id: 'move_dash' });
  const scene = await readFile(new URL('../src/game/BattleScene.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(scene, /onPointerEnter=\{\(\) => previewCard|onFocus=\{\(\) => previewCard/);
  assert.match(scene, /event\.key === 'Escape'\) clearCard\(\)/);
  assert.match(scene, /onCancel=\{clearCard\}/);
  const canvas = await readFile(new URL('../src/game/GameCanvas.tsx', import.meta.url), 'utf8');
  assert.match(canvas, /!drag\.moved && event\.button === 2\) latest\.current\.onCancel\?\.\(\)/);
  assert.match(canvas, /pans: event\.button === 2/);
});

test('slash marks glass and blocked neighbours separately from valid targets', () => {
  const tiles = Array.from({ length: 20 }, () => Array(20).fill('NORMAL'));
  tiles[4][5] = 'BLOCKED';
  tiles[5][6] = 'GLASS';
  const map = view.parseMap({ map_id: 'default', width: 20, height: 20, tiles });
  const keys = (points) => points.map(({ x, y }) => `${x},${y}`).sort();
  assert.deepEqual(keys(view.slashBlocked(map, { x: 5, y: 5 })), ['5,4', '6,5']);
  assert.deepEqual(keys(view.slashBlocked(map, { x: 0, y: 0 })), []);
});

test('bomb preview clips the 3x3 area and marks only opponent normal tiles as changing', () => {
  const tiles = Array.from({ length: 20 }, () => Array(20).fill('NORMAL'));
  tiles[1][1] = 'GLASS';
  const map = view.parseMap({ map_id: 'default', width: 20, height: 20, tiles });
  const owners = Array.from({ length: 20 }, () => Array(20).fill(null));
  owners[0][0] = 2;
  owners[0][1] = 1;
  owners[1][1] = 2;
  owners[1][0] = 3;
  owners[5][5] = 2;
  const keys = (points) => points.map(({ x, y }) => `${x},${y}`).sort();
  const corner = view.bombArea(map, { x: 0, y: 0 });
  assert.deepEqual(keys(corner), ['0,0', '0,1', '1,0', '1,1']);
  assert.deepEqual(keys(view.bombHits(map, owners, corner, 1)), ['0,0', '0,1']);
  assert.equal(view.bombArea(map, { x: 5, y: 5 }).length, 9);
  assert.equal(view.bombArea(map, { x: 19, y: 10 }).length, 6);
  assert.deepEqual(view.bombHits(map, owners, view.bombArea(map, { x: 10, y: 10 }), 1), []);
});

test('number keys select the matching hand index and toggle like a click', () => {
  const key = (value, extra = {}) => view.gameShortcut({ key: value, code: `Digit${value}`, repeat: false, ...extra }, false);
  assert.deepEqual([key('1'), key('2'), key('3')], [0, 1, 2].map((index) => ({ type: 'select', index })));
  assert.equal(key('4'), null);
  assert.equal(key('1', { repeat: true }), null);
  assert.equal(key('2', { ctrlKey: true }), null);
  assert.equal(view.gameShortcut({ key: '1', code: 'Digit1', repeat: false }, true), null);
  let active = view.selectCard(null, 1, 'attack_bomb');
  assert.deepEqual(active, { index: 1, id: 'attack_bomb' });
  active = view.selectCard(active, 1, 'attack_bomb');
  assert.equal(active, null);
  active = view.selectCard(view.selectCard(null, 0, 'move_dash'), 2, 'attack_slash');
  assert.deepEqual(active, { index: 2, id: 'attack_slash' });
});

test('Space uses the card and R recenters, ignoring key repeat and text inputs', () => {
  assert.deepEqual(view.gameShortcut({ key: ' ', code: 'Space', repeat: false }, false), { type: 'use' });
  assert.equal(view.gameShortcut({ key: ' ', code: 'Space', repeat: true }, false), null);
  assert.equal(view.gameShortcut({ key: ' ', code: 'Space', repeat: false }, true), null);
  assert.deepEqual(view.gameShortcut({ key: 'r', code: 'KeyR', repeat: false }, false), { type: 'recenter' });
  assert.equal(view.gameShortcut({ key: 'r', code: 'KeyR', repeat: true }, false), null);
});

test('Space reuses the click path with the tile under the pointer, and R keeps the old recenter', async () => {
  const scene = await readFile(new URL('../src/game/BattleScene.tsx', import.meta.url), 'utf8');
  assert.match(scene, /const point = hasCard \? camera\.current\?\.pointer\(\) : null;\s+if \(point\) use\(point\);/);
  assert.match(scene, /onSelect=\{useAt\}/);
  assert.deepEqual(scene.match(/onUseCard\([^)]*\)/g), ['onUseCard(index: number, target?: Point)', 'onUseCard(active.index, point)']);
  assert.match(scene, /window\.removeEventListener\('keydown', press\); window\.removeEventListener\('keyup', release\);/);
  assert.match(scene, /<kbd>R<\/kbd> 내 위치/);
  const canvas = await readFile(new URL('../src/game/GameCanvas.tsx', import.meta.url), 'utf8');
  assert.match(canvas, /hovered\.current = \(\) => last \? pointAt\(last\) : null;/);
  assert.match(canvas, /const exit = \(\) => \{ last = null; leave\(\); \};/);
  assert.match(canvas, /const local = world\.toLocal\(/);
  assert.match(canvas, /event\.code !== 'KeyR' \|\| event\.repeat/);
  assert.match(canvas, /camera\.current\.center\(\{ x: \(self\.position\.x \+ 0\.5\) \* TILE, y: \(self\.position\.y \+ 0\.5\) \* TILE \}\);\s+leave\(\);/);
  assert.doesNotMatch(canvas, /event\.code !== 'Space'/);
});
