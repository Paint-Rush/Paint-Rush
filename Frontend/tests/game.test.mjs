import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { parseMap, parseSnapshot, rankPlayers, canUseCard, DEFAULT_DECK } from '../src/game/model.ts';
import { Camera } from '../src/game/camera.ts';

const mapData = JSON.parse(readFileSync(new URL('../src/game/maps/crossroads.json', import.meta.url), 'utf8'));
const map = parseMap(mapData);

// Demo imports use extensionless TypeScript for Vite, so bundle that module for Node tests.
import { build } from 'esbuild';
const bundle = await build({ entryPoints: [fileURLToPath(new URL('../src/game/demo.ts', import.meta.url))], bundle: true, write: false, format: 'esm', platform: 'node' });
const { createDemo, DemoClock, demoUseCard } = await import(`data:text/javascript;base64,${Buffer.from(bundle.outputFiles[0].text).toString('base64')}`);

test('map loads all 400 tiles, rejects mismatched dimensions, unknown tile IDs and blocked corners', () => {
  assert.equal(map.tiles.flat().length, 400);
  assert.throws(() => parseMap({ ...mapData, width: 19 }));
  const bad = structuredClone(mapData); bad.tiles[0][0] = 999;
  assert.throws(() => parseMap(bad));
  bad.tiles[0][0] = 1; assert.throws(() => parseMap(bad), /꼭짓점/);
  assert.throws(() => parseMap({ ...mapData, legend: { '0': { name: 'bad', color: 'url(evil)', walkable: true } } }));
});

test('camera changes independently of avatars and disables effects with reduced motion', () => {
  const avatar = Object.freeze({ x: 50, y: 60 });
  const camera = new Camera(); camera.follow(avatar, 1);
  assert.ok(camera.x > 49); camera.pan(100, 50); const x = camera.x;
  camera.follow(avatar, 1); assert.equal(camera.x, x); assert.deepEqual(avatar, { x: 50, y: 60 });
  camera.setZoom(99); assert.equal(camera.zoom, 2); camera.setZoom(0); assert.equal(camera.zoom, 0.45);
  camera.trigger('warp'); assert.notEqual(camera.step(0.1, false).skew, 0);
  assert.deepEqual(camera.step(0.1, true), { dx: 0, dy: 0, scale: 1, skew: 0 });
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

test('playing a card charges cost and cycles the played card, not a different card', () => {
  const state = createDemo(map, 'tester', 'painter', DEFAULT_DECK); state.phase = 'playing';
  const next = demoUseCard(state, 'attack');
  assert.equal(next.players[0].cost, 2); assert.equal(next.players[0].costUsed, 4);
  assert.equal(next.players[0].cardsUsed, 1); assert.equal(next.players[0].deck.at(-1), 'attack');
  assert.equal(next.players[0].deck[0], 'move'); assert.equal(state.players[0].cost, 6);
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
