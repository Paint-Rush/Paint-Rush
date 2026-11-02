import test from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';

async function load(name) {
  const result = await build({ entryPoints: [fileURLToPath(new URL(`../src/game/${name}.ts`, import.meta.url))], bundle: true, write: false, format: 'esm', platform: 'node' });
  return import(`data:text/javascript;base64,${Buffer.from(result.outputFiles[0].text).toString('base64')}`);
}
const { readRoomState, parseHand } = await load('serverState');
const { patternTiles } = await load('patterns');
export function room() {
  return { type: 'room_state', room_id: 'test-room', status: 'PLAYING', map_id: 'default',
    map: { width: 20, height: 20, tiles: Array.from({ length: 20 }, () => Array(20).fill('NORMAL')) },
    players: [
      { user_id: 1, character_id: 'character_01', color: 'RED', spawn: [0, 0], position: [10, 10], alive: true, connected: true, tile_count: 0 },
      { user_id: 2, character_id: 'character_02', color: 'BLUE', spawn: [19, 19], position: [19, 19], alive: true, connected: true, tile_count: 0 },
    ], my_cost: 6, my_hand: ['move_dash', 'attack_slash', 'move_dash'], my_next_card: 'attack_bomb', my_ultimate_used: false,
    max_cost: 10, ready_remaining_seconds: 0, remaining_seconds: 300,
    tile_owners: Array.from({ length: 20 }, () => Array(20).fill(null)) };
}

test('reads the real server room_state, preserves duplicate hand IDs and hides other players private data', () => {
  const result = readRoomState(room(), 1, null, 100);
  assert.equal(result.authoritative, true); assert.equal(result.inputReady, true);
  assert.equal(result.players[0].cost, 6); assert.deepEqual(result.players[0].hand, ['move_dash', 'attack_slash', 'move_dash']);
  assert.deepEqual(result.players[0].deck, []); assert.deepEqual(result.players[1].hand, []);
  assert.equal(result.nextCard, 'attack_bomb'); assert.equal(result.receivedAt, 100);
  assert.equal(result.map.legend['0'].name, 'NORMAL');
});

test('rejects invalid cost, wrong hand size, duplicate users and missing self', () => {
  assert.throws(() => readRoomState({ ...room(), my_cost: 11 }, 1, null));
  assert.throws(() => parseHand(['move_dash', 'attack_slash']));
  const bad = room(); bad.players[1].user_id = 1;
  assert.throws(() => readRoomState(bad, 1, null));
  assert.throws(() => readRoomState(room(), 99, null));
});

test('cross-checks counts against actual tile owners and keeps previous state untouched on rejection', () => {
  const previous = readRoomState(room(), 1, null);
  const bad = room(); bad.players[0].tile_count = 1;
  assert.throws(() => readRoomState(bad, 1, previous), /일치하지 않습니다/);
  assert.equal(previous.players[0].tiles, 0);
  bad.tile_owners[10][10] = 1;
  assert.equal(readRoomState(bad, 1, previous).players[0].tiles, 1);
});

test('maps GLASS and BLOCKED from editor data and rejects unknown tiles and invalid spawns', () => {
  const data = room(); data.map.tiles[4][5] = 'GLASS'; data.map.tiles[5][5] = 'BLOCKED';
  const parsed = readRoomState(data, 1, null);
  assert.equal(parsed.map.legend[String(parsed.map.tiles[4][5])].walkable, true);
  assert.equal(parsed.map.legend[String(parsed.map.tiles[5][5])].walkable, false);
  data.map.tiles[7][5] = 'INVALID'; assert.throws(() => readRoomState(data, 1, null));
  data.map.tiles[7][5] = 'NORMAL'; data.players[0].spawn = [3, 3]; assert.throws(() => readRoomState(data, 1, null));
});

test('fixed storyboard previews produce the X, wind and chosen straight line without modifying map data', () => {
  const map = readRoomState(room(), 1, null).map, before = JSON.stringify(map);
  const request = { pattern: 'the_line', direction: 'left', origin: { x: 10, y: 10 }, map };
  assert.deepEqual(patternTiles(request), [10, 9, 8, 7, 6].map(x => ({ x, y: 10 })));
  assert.deepEqual(patternTiles({ ...request, direction: 'up' }), [10, 9, 8, 7, 6].map(y => ({ x: 10, y })));
  const cross = patternTiles({ ...request, pattern: 'criss_cross' });
  assert.equal(cross.length, 9); assert.ok(cross.every(p => Math.abs(p.x - 10) === Math.abs(p.y - 10)));
  const wind = patternTiles({ ...request, pattern: 'swastika' });
  assert.equal(wind.length, 17); assert.ok(wind.some(p => p.x === 12 && p.y === 8));
  assert.equal(JSON.stringify(map), before);
});

test('previews clip to the map, omit glass/obstacles and never change tile ownership', () => {
  const state = readRoomState(room(), 1, null), owners = JSON.stringify(state.owners);
  assert.deepEqual(patternTiles({ map: state.map, origin: { x: 0, y: 0 }, direction: 'left', pattern: 'the_line' }), [{ x: 0, y: 0 }]);
  state.map.tiles[10][9] = 1; state.map.tiles[10][8] = 2;
  const points = patternTiles({ map: state.map, origin: { x: 10, y: 10 }, direction: 'left', pattern: 'the_line' });
  assert.ok(points.every(p => p.x !== 9 && p.x !== 8)); assert.equal(JSON.stringify(state.owners), owners);
});
