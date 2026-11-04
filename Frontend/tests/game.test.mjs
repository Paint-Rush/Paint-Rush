import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { parseMap, parseSnapshot, rankPlayers, canUseCard, CARDS, getCard, DEFAULT_DECK } from '../src/game/model.ts';
import { Camera } from '../src/game/camera.ts';

const mapData = JSON.parse(readFileSync(new URL('../src/game/maps/crossroads.json', import.meta.url), 'utf8'));
const map = parseMap(mapData);

// Demo imports use extensionless TypeScript for Vite, so bundle that module for Node tests.
import { build } from 'esbuild';
const bundle = await build({ entryPoints: [fileURLToPath(new URL('../src/game/demo.ts', import.meta.url))], bundle: true, write: false, format: 'esm', platform: 'node' });
const { createDemo, DemoClock, usePreviewCard, paint } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);
const actionsBundle = await build({ entryPoints: [fileURLToPath(new URL('../src/game/cardActions.ts', import.meta.url))], bundle: true, write: false, format: 'esm', platform: 'node' });
const { resolveCardAction, cardTargetTiles } = await import(`data:text/javascript;base64,${Buffer.from(actionsBundle.outputFiles[0].text).toString('base64')}`);

test('map loads all 400 tiles, rejects mismatched dimensions, unknown tile IDs and blocked corners', () => {
  assert.equal(map.tiles.flat().length, 400);
  assert.ok(map.tiles.flat().every(id => map.legend[String(id)].walkable));
  assert.throws(() => parseMap({ ...mapData, width: 19 }));
  const bad = structuredClone(mapData); bad.tiles[0][0] = 999;
  assert.throws(() => parseMap(bad));
  bad.tiles[0][0] = 1; assert.throws(() => parseMap(bad), /꼭짓점/);
  assert.throws(() => parseMap({ ...mapData, legend: { '0': { name: 'bad', color: 'url(evil)', walkable: true } } }));
});

test('camera changes independently of avatars and disables effects with reduced motion', () => {
  const avatar = Object.freeze({ x: 50, y: 60 });
  const camera = new Camera(); camera.configure(800, 600, 960, 960);
  assert.deepEqual({ x: camera.x, y: camera.y }, { x: 480, y: 480 });
  camera.setZoom(2); camera.center(avatar); const zoom = camera.zoom;
  assert.equal(camera.zoom, zoom); assert.deepEqual(avatar, { x: 50, y: 60 });
  camera.pan(100, 50); assert.deepEqual(avatar, { x: 50, y: 60 });
  camera.setZoom(99); assert.equal(camera.zoom, 3); camera.setZoom(0); assert.equal(camera.zoom, 1);
  camera.trigger('warp'); assert.notEqual(camera.step(0.1, false).skew, 0);
  assert.deepEqual(camera.step(0.1, true), { dx: 0, dy: 0, scale: 1, skew: 0 });
});

test('free camera zoom keeps the pointed world location fixed and overview resets pan and zoom', () => {
  const camera = new Camera(); camera.configure(800, 600, 960, 960);
  const pointer = { x: 450, y: 310 };
  const worldAt = () => ({ x: camera.x + (pointer.x - 400) / camera.scale, y: camera.y + (pointer.y - 300) / camera.scale });
  const before = worldAt(); camera.zoomAt(2, pointer);
  assert.ok(Math.abs(worldAt().x - before.x) < 0.000001); assert.ok(Math.abs(worldAt().y - before.y) < 0.000001);
  camera.pan(100, -100); assert.notEqual(camera.x, 480);
  camera.overview(); assert.equal(camera.zoom, 1); assert.equal(camera.x, 480); assert.equal(camera.y, 480);
  assert.deepEqual(camera.viewport(), { x: 0, y: 0, width: 960, height: 960 });
});

test('camera bounds avoid empty space at corners, after extreme drags and after resizing', () => {
  const camera = new Camera(); camera.configure(800, 600, 960, 960); camera.setZoom(3);
  for (const target of [{ x: -9999, y: -9999 }, { x: 9999, y: 9999 }]) {
    camera.center(target); const view = camera.viewport();
    assert.ok(view.x >= 0 && view.y >= 0); assert.ok(view.x + view.width <= 960 && view.y + view.height <= 960);
    assert.ok(Math.abs(view.width * camera.scale - 800) < 0.000001);
    assert.ok(Math.abs(view.height * camera.scale - 600) < 0.000001);
  }
  camera.configure(1440, 390, 960, 960);
  assert.equal(camera.zoom, 3); assert.equal(camera.x, 480);
  assert.deepEqual(camera.viewport().width, 960);
  camera.setZoom(1); assert.equal(camera.x, 480); assert.equal(camera.y, 480);
});

test('demo assigns four distinct corners and waits ten seconds in ready', () => {
  let state = createDemo(map, 'tester', 'painter', DEFAULT_DECK, () => 0.3);
  assert.equal(new Set(state.players.map(p => `${p.spawn.x},${p.spawn.y}`)).size, 4);
  const clock = new DemoClock(); state = clock.advance(state, 3000); assert.equal(state.phase, 'ready');
  state = clock.advance(state, 9999); assert.equal(state.phase, 'ready');
  state = clock.advance(state, 1); assert.equal(state.phase, 'playing');
  assert.equal(state.remainingMs, 300000);
});

test('cost respects 860ms then 450ms at the four minute boundary, capped at ten', () => {
  const clock = new DemoClock(); let state = createDemo(map, 'tester', 'painter', DEFAULT_DECK); state.phase = 'playing';
  state = clock.advance(state, 859); assert.equal(state.players[0].cost, 6);
  state = clock.advance(state, 1); assert.equal(state.players[0].cost, 7);
  state.remainingMs = 60000; state = clock.advance(state, 449); assert.equal(state.players[0].cost, 7);
  assert.equal(state.phase, 'deathmatch');
  state = clock.advance(state, 1); assert.equal(state.players[0].cost, 8);
  state = clock.advance(state, 10000); assert.equal(state.players[0].cost, 10);
});

test('death waits ten seconds and returns to original spawn; finish blocks cards', () => {
  const clock = new DemoClock(); let state = createDemo(map, 'tester', 'painter', DEFAULT_DECK); state.phase = 'playing';
  state.players[0].alive = false; state.players[0].respawnMs = 10000;
  state = clock.advance(state, 9999); assert.equal(state.players[0].alive, false);
  state = clock.advance(state, 1); assert.equal(state.players[0].alive, true); assert.deepEqual(state.players[0].position, state.players[0].spawn);
  state.remainingMs = 1; state = clock.advance(state, 1); assert.equal(state.phase, 'finished');
  assert.ok(state.winnerId); assert.equal(canUseCard(state, state.players[0], 'move'), false);
});

test('the catalog contains exactly the six supplied cards and the user-specified costs', () => {
  assert.deepEqual(CARDS.map(card => card.name), ['JUMPER', 'DASH', 'SLIDING', 'CRISS CROSS', 'SWASTIKA', 'THE LINE']);
  assert.deepEqual(DEFAULT_DECK, ['jumper', 'dash', 'sliding', 'criss_cross', 'swastika', 'the_line']);
  assert.deepEqual(CARDS.map(card => card.cost), [4, 3, 4, 5, 6, 3]);
  for (const removed of ['move_dash', 'move_jump', 'attack_slash', 'attack_bomb', 'defend', 'shield']) assert.equal(getCard(removed), undefined);
});

function previewFixture() {
  const plain = parseMap({ width: 20, height: 20, tiles: Array.from({ length: 20 }, () => Array(20).fill('NORMAL')) });
  const state = createDemo(plain, 'tester', 'painter', DEFAULT_DECK);
  state.phase = 'playing'; state.players[0].position = { x: 10, y: 10 };
  paint(state, state.players[0], [state.players[0].position]);
  return state;
}

test('using an attack paints its actual pattern, spends configured cost and rotates the slot and next card', () => {
  const state = previewFixture(), original = structuredClone(state);
  state.players[0].hand[2] = 'criss_cross';
  state.previewQueue = ['swastika', 'the_line', 'sliding'];
  const before = structuredClone(state);
  const next = usePreviewCard(state, 2);
  assert.equal(next.players[0].cost, 1);
  assert.deepEqual(next.players[0].hand, ['jumper', 'dash', 'swastika']);
  assert.deepEqual(next.previewQueue, ['the_line', 'sliding', 'criss_cross']);
  assert.equal(next.nextCard, 'the_line');
  assert.equal(next.players[0].cardsUsed, 1); assert.equal(next.players[0].costUsed, 5);
  assert.equal(next.players[0].tiles, 9);
  assert.equal(next.owners[8][8], 1); assert.equal(next.owners[8][12], 1);
  assert.equal(next.owners[10][11], null);
  assert.deepEqual(next.players[0].position, original.players[0].position);
  assert.deepEqual(state, before);
});

test('JUMPER teleports anywhere within 5 by 5, skips intervening walls and does not paint glass', () => {
  const state = previewFixture();
  const moved = usePreviewCard(state, 0, { x: 11, y: 10 });
  assert.deepEqual(moved.players[0].position, { x: 11, y: 10 });
  assert.equal(moved.owners[10][11], 1); assert.equal(moved.players[0].tiles, 2);
  assert.equal(moved.players[0].cost, 2);
  const wall = previewFixture(); wall.map.tiles[10][11] = 2;
  const jumped = usePreviewCard(wall, 0, { x: 12, y: 10 });
  assert.deepEqual(jumped.players[0].position, { x: 12, y: 10 });
  assert.equal(jumped.owners[10][11], null); assert.equal(jumped.owners[10][12], 1);
  const route = usePreviewCard(state, 0, { x: 12, y: 12 });
  assert.equal(route.owners[11][11], 1); assert.equal(route.owners[12][12], 1); assert.equal(route.players[0].tiles, 3);
  const glass = previewFixture(); glass.map.tiles[10][11] = 1;
  const unpainted = usePreviewCard(glass, 0, { x: 11, y: 10 });
  assert.deepEqual(unpainted.players[0].position, { x: 11, y: 10 });
  assert.equal(unpainted.owners[10][11], null); assert.equal(unpainted.players[0].tiles, 1);
});

test('preview rejects illegal moves, insufficient cost, dead/end states and live server state without spending cards', () => {
  const state = previewFixture(), original = structuredClone(state);
  for (const target of [undefined, { x: 10, y: 10 }, { x: 13, y: 10 }, { x: -1, y: 10 }]) assert.throws(() => usePreviewCard(state, 0, target));
  const blocked = structuredClone(state); blocked.map.tiles[10][11] = 2;
  assert.throws(() => usePreviewCard(blocked, 0, { x: 11, y: 10 }), /장애물/);
  const poor = structuredClone(state); poor.players[0].cost = 1;
  assert.throws(() => usePreviewCard(poor, 2, { x: 11, y: 11 }));
  const dead = structuredClone(state); dead.players[0].alive = false;
  assert.throws(() => usePreviewCard(dead, 2));
  assert.throws(() => usePreviewCard({ ...state, phase: 'finished', remainingMs: 0 }, 2));
  assert.throws(() => usePreviewCard({ ...state, authoritative: true }, 2), /서버 경기/);
  assert.deepEqual(state, original);
});

test('DASH moves exactly four cardinal tiles and SLIDING moves one to five diagonal tiles, painting the route', () => {
  const state = previewFixture();
  const dash = usePreviewCard(state, 1, { x: 14, y: 10 });
  assert.deepEqual(dash.players[0].position, { x: 14, y: 10 });
  assert.equal(dash.players[0].tiles, 5);
  for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
    const target = { x: 10 + dx * 4, y: 10 + dy * 4 };
    assert.deepEqual(usePreviewCard(state, 1, target).players[0].position, target);
    for (const length of [1, 2, 3, 5]) assert.throws(() => usePreviewCard(state, 1, { x: 10 + dx * length, y: 10 + dy * length }), /정확히 4칸/);
  }
  const slide = usePreviewCard(state, 2, { x: 14, y: 14 });
  assert.deepEqual(slide.players[0].position, { x: 14, y: 14 });
  assert.equal(slide.players[0].tiles, 5);
  for (const [dx, dy] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
    for (let length = 1; length <= 5; length++) {
      const target = { x: 10 + dx * length, y: 10 + dy * length };
      const moved = usePreviewCard(state, 2, target);
      assert.deepEqual(moved.players[0].position, target); assert.equal(moved.players[0].tiles, length + 1);
    }
    assert.throws(() => usePreviewCard(state, 2, { x: 10 + dx * 6, y: 10 + dy * 6 }), /최대 5칸/);
  }
  assert.throws(() => usePreviewCard(state, 1, { x: 11, y: 11 }), /직선/);
  assert.throws(() => usePreviewCard(state, 2, { x: 12, y: 11 }), /대각선/);
  const blocked = previewFixture(); blocked.map.tiles[10][12] = 2; blocked.map.tiles[12][12] = 2;
  assert.throws(() => usePreviewCard(blocked, 1, { x: 14, y: 10 }), /경로/);
  assert.throws(() => usePreviewCard(blocked, 2, { x: 14, y: 14 }), /경로/);
});

test('attacks keep the player still; SWASTIKA paints 17 tiles and THE LINE paints four tiles ahead', () => {
  const state = previewFixture(); state.players[0].hand = ['swastika', 'the_line', 'criss_cross'];
  const wind = usePreviewCard(state, 0);
  assert.equal(wind.players[0].tiles, 17); assert.equal(wind.owners[8][12], 1); assert.equal(wind.owners[8][11], 1);
  assert.deepEqual(wind.players[0].position, state.players[0].position);
  const line = usePreviewCard(state, 1, { x: 10, y: 14 });
  assert.deepEqual(line.players[0].position, state.players[0].position);
  assert.equal(line.previewAction.tiles.length, 4);
  assert.equal(line.players[0].tiles, 5);
  for (let y = 10; y <= 14; y++) assert.equal(line.owners[y][10], 1);
  assert.equal(line.owners[9][10], null); assert.equal(line.owners[10][11], null);
  assert.throws(() => usePreviewCard(state, 1, { x: 11, y: 11 }), /상하좌우/);
  assert.throws(() => usePreviewCard(state, 1, { x: 10, y: 15 }), /4칸/);
  const capture = previewFixture(); capture.players[0].hand[2] = 'criss_cross';
  capture.owners[8][8] = 2; capture.players[1].tiles = 1;
  const captured = usePreviewCard(capture, 2);
  assert.equal(captured.players[1].tiles, 0); assert.equal(captured.players[0].tiles, 9);
});

test('each supplied card deducts its exact cost and rejects insufficient cost without spending or rotating', () => {
  for (const card of CARDS) {
    const state = previewFixture(); state.players[0].cost = 10; state.players[0].hand[0] = card.id;
    const target = card.id === 'sliding' ? { x: 12, y: 12 } : { x: card.id === 'dash' ? 14 : 12, y: 10 };
    const next = usePreviewCard(state, 0, target);
    assert.equal(next.players[0].cost, 10 - card.cost, card.id);
    assert.equal(next.players[0].costUsed, card.cost);
    state.players[0].cost = card.cost - 1;
    const before = structuredClone(state);
    assert.equal(canUseCard(state, state.players[0], card.id), false);
    assert.throws(() => usePreviewCard(state, 0, target));
    assert.deepEqual(state, before);
  }
  const mismatch = previewFixture(); mismatch.cardCosts = { jumper: 1 };
  assert.equal(canUseCard(mismatch, mismatch.players[0], 'jumper'), false);
});

test('hover target ranges match executable rules, respect map edges and do not mutate game data', () => {
  const state = previewFixture(), before = structuredClone(state), origin = state.players[0].position;
  for (const [id, count] of [['jumper', 24], ['dash', 4], ['sliding', 20], ['the_line', 16]]) {
    const targets = cardTargetTiles(id, origin, state.map);
    assert.equal(targets.length, count, id);
    for (const target of targets) assert.ok(resolveCardAction(id, origin, state.map, target).tiles.length);
  }
  assert.equal(cardTargetTiles('dash', { x: 0, y: 0 }, state.map).length, 2);
  assert.equal(cardTargetTiles('jumper', { x: 0, y: 0 }, state.map).length, 8);
  assert.equal(cardTargetTiles('sliding', { x: 0, y: 0 }, state.map).length, 5);
  assert.deepEqual(state, before);
  state.map.tiles[10][12] = 2;
  assert.ok(!cardTargetTiles('dash', origin, state.map).some(p => p.x === 14 && p.y === 10));
  assert.ok(cardTargetTiles('jumper', origin, state.map).some(p => p.x === 11 && p.y === 10));
});

test('ranking applies every tie-break in order and yields only one winner', () => {
  const sample = createDemo(map, 'tester', 'painter', DEFAULT_DECK).players[0];
  for (const [field, av, bv] of [['tiles', 11, 10], ['painted', 11, 10], ['kills', 2, 1], ['cardsUsed', 1, 2], ['costUsed', 1, 2], ['queueOrder', 0, 1]]) {
    const a = { ...sample, id: 1, [field]: av }, b = { ...sample, id: 2, [field]: bv };
    assert.equal(rankPlayers([b, a])[0].id, 1, field);
  }
});

function wireRoom() {
  const state = createDemo(map, 'tester', 'painter', DEFAULT_DECK);
  return { room_id: 'room-1', map_id: map.id, map, self_id: 1, status: 'READY', remaining_ms: 300000, ready_remaining_ms: 10000,
    player_order: state.players.map(p => p.id), tile_counts: Object.fromEntries(state.players.map(p => [p.id, 0])), tile_owners: state.owners,
    players: Object.fromEntries(state.players.map(p => [p.id, { user_id: p.id, username: p.name, character_id: p.character, color: p.color,
      position: [p.position.x, p.position.y], spawn: [p.spawn.x, p.spawn.y], connected: true, alive: true, deck: p.deck, hand: p.hand,
      cost: p.cost, cost_used: 0, cards_used: 0, kills: 0, total_painted: 0 }])) };
}
test('backend GameRoom snake_case adapter accepts 2–4 players and rejects invalid data', () => {
  const room = wireRoom(); const parsed = parseSnapshot(room, 100);
  assert.equal(parsed.players[0].id, 1); assert.equal(parsed.readyMs, 10000); assert.equal(parsed.receivedAt, 100);
  assert.throws(() => parseSnapshot({ ...room, self_id: 99 }));
  assert.throws(() => parseSnapshot({ ...room, status: '__proto__' }));
  assert.throws(() => parseSnapshot({ ...room, player_order: [1, 1] }));
  assert.throws(() => parseSnapshot({ ...room, status: 'FINISHED' }));
  assert.equal(parseSnapshot({ ...room, status: 'FINISHED', winner_id: 1 }).winnerId, 1);
  room.players[1].position = [-1, 0]; assert.throws(() => parseSnapshot(room));
});
