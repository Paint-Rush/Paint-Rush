import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
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
      contents: "import { createElement } from 'react'; import { renderToStaticMarkup } from 'react-dom/server'; import GamePage from './src/pages/GamePage'; export { toGameState, hearts, respawnMs } from './src/game/roomView'; export { readRoomState, initialSession } from './src/services/gameSocket'; export function render(session, socket, cards) { return renderToStaticMarkup(createElement(GamePage, { session, socket, onExit: () => {}, initialCards: cards })); }",
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
  const dir = await mkdtemp(join(tmpdir(), 'paint-rush-respawn-'));
  const file = join(dir, 'respawn.cjs');
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

function player(id, overrides = {}) {
  const spawn = id === 1 ? [0, 0] : [19, 19];
  return {
    user_id: id, color: id === 1 ? 'RED' : 'BLUE', spawn, position: spawn, alive: true, connected: true, tile_count: 0,
    character_id: 'character_01', character_name: id === 1 ? '페인터' : '상대', ultimate_id: null, ultimate_name: null,
    hp: 3, max_hp: 3, knocked_out: false, respawn_remaining_seconds: 0, ...overrides,
  };
}

function roomMessage(overrides = {}, players = [player(1), player(2)]) {
  return {
    type: 'room_state',
    room_id: ROOM,
    status: 'PLAYING',
    map_id: 'default',
    map: { width: 20, height: 20, tiles: Array.from({ length: 20 }, () => Array(20).fill('NORMAL')) },
    players,
    my_cost: 8,
    my_hand: ['move_dash', 'attack_slash', 'move_jump'],
    my_next_card: 'attack_bomb',
    my_ultimate_used: false,
    tile_owners: Array.from({ length: 20 }, () => Array(20).fill(null)),
    started_at: null,
    ready_remaining_seconds: 0,
    remaining_seconds: 200,
    max_cost: 10,
    ...overrides,
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
}

function playing() {
  let time = 1_000_000;
  const sockets = [];
  const session = new GameSocket(() => { const socket = new FakeSocket(); sockets.push(socket); return socket; }, 'ws://localhost/ws/game', () => time);
  session.start('jwt', 'character_01');
  const socket = sockets[0];
  socket.open();
  socket.receive({ type: 'auth_success', user_id: 1 });
  socket.receive({ type: 'queue_waiting', waiting: 1, match_size: 2 });
  socket.receive({ type: 'match_found', room_id: ROOM, map_id: 'default', players: [player(1), player(2)] });
  socket.receive(roomMessage());
  return { session, socket, advance: (ms) => { time += ms; }, now: () => time };
}

function fakeSocket() {
  return { calls: [], onEvent() { return () => {}; }, playCard(...args) { this.calls.push(['playCard', ...args]); return true; }, sync() { return true; }, retry() { return true; }, leaveRoom() { return true; } };
}

function render(message, overrides = {}) {
  const room = game.readRoomState(message, Date.now());
  return game.render({ ...game.initialSession(), phase: 'started', connected: true, userId: 1, roomId: ROOM, room, ...overrides }, fakeSocket(), CARDS);
}

const self = (session) => session.getState().room.players[0];
const other = (session) => session.getState().room.players[1];

test('player_damaged updates only the target HP from server values', () => {
  const { session, socket } = playing();
  socket.receive({ type: 'player_damaged', room_id: ROOM, user_id: 2, attacker_id: 1, hp: 2, max_hp: 3 });
  assert.equal(other(session).hp, 2);
  assert.equal(other(session).maxHp, 3);
  assert.equal(self(session).hp, 3);
  assert.equal(session.getState().phase, 'started');
  session.close();
});

test('player_knocked_out marks the player knocked out with a server based respawn time', () => {
  const { session, socket, now } = playing();
  socket.receive({ type: 'player_damaged', room_id: ROOM, user_id: 1, attacker_id: 2, hp: 0, max_hp: 3 });
  socket.receive({ type: 'player_knocked_out', room_id: ROOM, user_id: 1, attacker_id: 2, respawn_remaining_seconds: 10 });
  const knocked = self(session);
  assert.equal(knocked.hp, 0);
  assert.equal(knocked.knockedOut, true);
  assert.equal(knocked.alive, false);
  assert.equal(knocked.respawnAt, now() + 10000);
  assert.deepEqual(knocked.position, [0, 0]);
  session.close();
});

test('player_respawned restores HP and moves to the server position without painting', () => {
  const { session, socket, advance } = playing();
  socket.receive({ type: 'player_moved', user_id: 1, x: 5, y: 5, path: [[5, 5]] });
  socket.receive({ type: 'player_knocked_out', room_id: ROOM, user_id: 1, attacker_id: 2, respawn_remaining_seconds: 10 });
  advance(12000);
  assert.equal(self(session).knockedOut, true);
  const owners = session.getState().room.tileOwners;
  socket.receive({ type: 'player_respawned', room_id: ROOM, user_id: 1, hp: 3, max_hp: 3, x: 0, y: 0 });
  const back = self(session);
  assert.equal(back.hp, 3);
  assert.equal(back.knockedOut, false);
  assert.equal(back.alive, true);
  assert.equal(back.respawnAt, null);
  assert.deepEqual(back.position, [0, 0]);
  assert.equal(session.getState().room.tileOwners, owners);
  session.close();
});

test('room_state restores HP, knockout and remaining respawn time after reconnect', () => {
  const { session, socket, now } = playing();
  socket.receive(roomMessage({}, [player(1, { hp: 0, alive: false, knocked_out: true, respawn_remaining_seconds: 4, position: [3, 4] }), player(2, { hp: 1 })]));
  assert.equal(self(session).hp, 0);
  assert.equal(self(session).knockedOut, true);
  assert.equal(self(session).respawnAt, now() + 4000);
  assert.deepEqual(self(session).position, [3, 4]);
  assert.equal(other(session).hp, 1);
  const state = game.toGameState(session.getState().room, 1, now() + 1500);
  assert.equal(state.players[0].respawnMs, 2500);
  assert.equal(game.toGameState(session.getState().room, 1, now() + 9000).players[0].respawnMs, 0);
  session.close();
});

test('missing HP data is not shown as full health', () => {
  const room = game.readRoomState(roomMessage({}, [player(1, { hp: undefined, max_hp: undefined, knocked_out: undefined, respawn_remaining_seconds: undefined }), player(2)]));
  assert.equal(room.players[0].hp, null);
  assert.equal(room.players[0].knockedOut, false);
  assert.equal(game.hearts(null, 3), null);
  assert.equal(game.hearts(3, 3), '♥ ♥ ♥');
  assert.equal(game.hearts(1, 3), '♥ ♡ ♡');
  assert.equal(game.hearts(0, 3), '♡ ♡ ♡');
  assert.equal(game.respawnMs(1000, 5000), 0);
});

test('HUD shows own and opponent hearts', () => {
  const html = render(roomMessage({}, [player(1), player(2, { hp: 2 })]));
  assert.match(html, /data-testid="hp-1"[^]*?나[^]*?aria-label="HP 3 \/ 3">♥ ♥ ♥</);
  assert.match(html, /data-testid="hp-2"[^]*?상대[^]*?aria-label="HP 2 \/ 3">♥ ♥ ♡</);
  assert.doesNotMatch(html, /knockout-banner/);
});

test('own knockout shows countdown, blocks every card and keeps the board visible', () => {
  const html = render(roomMessage({}, [player(1, { hp: 0, alive: false, knocked_out: true, respawn_remaining_seconds: 7 }), player(2)]));
  assert.match(html, /class="knockout-banner" role="status"><strong>기절!<\/strong><span>부활까지 7초<\/span>/);
  assert.match(html, /data-testid="hp-1"[^]*?♡ ♡ ♡<\/b><em>기절<\/em>/);
  assert.equal((html.match(/server-hand-card"[^>]*disabled/g) ?? []).length, 3);
  assert.doesNotMatch(html, /death-overlay/);
  assert.doesNotMatch(html, /battle-card-hint/);
  assert.match(html, /class="arena-canvas"/);
});

test('cards are usable again after respawn and countdown is gone', () => {
  const html = render(roomMessage());
  assert.doesNotMatch(html, /knockout-banner/);
  assert.doesNotMatch(html, /aria-label="1번 손패 DASH 사용"[^>]*disabled/);
  assert.doesNotMatch(html, /aria-label="2번 손패 SLASH 사용"[^>]*disabled/);
});

test('countdown is cleared when the match finishes', () => {
  const html = render(roomMessage({ status: 'FINISHED', remaining_seconds: 0 }, [player(1, { hp: 0, alive: false, knocked_out: true, respawn_remaining_seconds: 5 }), player(2)]), { phase: 'finished' });
  assert.doesNotMatch(html, /knockout-banner/);
  assert.match(html, /경기 종료 · 카드 사용 중지/);
});

test('knockout events from another room are ignored', () => {
  const { session, socket } = playing();
  socket.receive({ type: 'player_knocked_out', room_id: 'other-room', user_id: 1, attacker_id: 2, respawn_remaining_seconds: 10 });
  assert.equal(self(session).knockedOut, false);
  session.close();
});
