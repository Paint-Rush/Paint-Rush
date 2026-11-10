import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { GameSocket, initialSession, readRoomState } from '../src/services/gameSocket.ts';
import { ResultExit } from '../src/services/resultExit.ts';

const root = fileURLToPath(new URL('../', import.meta.url));

async function loadRenderer() {
  const result = await build({
    stdin: {
      contents: "import { createElement } from 'react'; import { renderToStaticMarkup } from 'react-dom/server'; import ResultPage from './src/pages/ResultPage'; export function render(session, socket) { return renderToStaticMarkup(createElement(ResultPage, { session, socket, onLeft: () => {} })); }",
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
  const dir = await mkdtemp(join(tmpdir(), 'paint-rush-result-'));
  const file = join(dir, 'result-page.cjs');
  await writeFile(file, result.outputFiles[0].text);
  const module = (await import(pathToFileURL(file).href)).default;
  await rm(dir, { recursive: true, force: true });
  return module.render;
}

const render = await loadRenderer();

class FakeSocket {
  static created = [];
  readyState = 0;
  sent = [];
  onopen = null;
  onmessage = null;
  onerror = null;
  onclose = null;

  constructor(url) {
    this.url = url;
    FakeSocket.created.push(this);
  }

  send(data) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.readyState = 3;
  }

  open() {
    this.readyState = 1;
    this.onopen?.({});
  }

  receive(message) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  drop() {
    this.readyState = 3;
    this.onclose?.({ code: 1006, reason: '' });
  }

  types() {
    return this.sent.map((message) => message.type);
  }
}

const ROOM = '7f9c2b1e-0000-4000-8000-000000000001';
const NEXT_ROOM = '7f9c2b1e-0000-4000-8000-000000000002';
const COLORS = ['RED', 'BLUE', 'GREEN', 'YELLOW'];
const SPAWNS = [[0, 0], [0, 19], [19, 0], [19, 19]];

function wirePlayers(count) {
  return Array.from({ length: count }, (_, index) => ({
    user_id: index + 1,
    color: COLORS[index],
    spawn: SPAWNS[index],
    character_id: `character_0${index + 1}`,
    character_name: `테스트 캐릭터 ${index + 1}`,
    ultimate_id: null,
    ultimate_name: null,
  }));
}

function roomState(overrides = {}, count = 2) {
  return {
    type: 'room_state',
    room_id: ROOM,
    status: 'FINISHED',
    map_id: 'default',
    map: { width: 20, height: 20, tiles: Array.from({ length: 20 }, () => Array(20).fill('NORMAL')) },
    players: wirePlayers(count).map((player) => ({ ...player, position: player.spawn, alive: true, connected: true, tile_count: 0 })),
    my_cost: 6,
    my_hand: ['move_dash', 'attack_slash', 'move_jump'],
    my_next_card: 'attack_bomb',
    my_ultimate_used: false,
    tile_owners: Array.from({ length: 20 }, () => Array(20).fill(null)),
    started_at: '2026-11-09T00:00:00+00:00',
    ready_remaining_seconds: 0,
    remaining_seconds: 0,
    max_cost: 10,
    ...overrides,
  };
}

function row(userId, rank, stats = {}) {
  return { user_id: userId, rank, is_winner: rank === 1, painted_tiles: 40 - rank * 5, total_painted: 60 - rank, kills: 0, deaths: 0, cards_used: 10 + rank, cost_used: 25 + rank, ...stats };
}

function gameResult(rows, overrides = {}) {
  return { type: 'game_result', room_id: ROOM, winner_id: rows.find((item) => item.is_winner)?.user_id ?? rows[0].user_id, results: rows, ...overrides };
}

function setup() {
  FakeSocket.created = [];
  const session = new GameSocket((url) => new FakeSocket(url), 'ws://localhost:5173/ws/game');
  return { session, sockets: FakeSocket.created, last: () => FakeSocket.created.at(-1) };
}

function playing(count = 2) {
  const context = setup();
  context.session.start('jwt-token', 'character_01');
  const socket = context.last();
  socket.open();
  socket.receive({ type: 'auth_success', user_id: 1 });
  socket.receive({ type: 'match_found', room_id: ROOM, map_id: 'default', players: wirePlayers(count) });
  socket.receive(roomState({ status: 'PLAYING', remaining_seconds: 30 }, count));
  return { ...context, socket };
}

function finishedSession(rows, me = 1, count = rows?.length ?? 2, overrides = {}) {
  const room = readRoomState(roomState({}, count));
  const result = rows ? { roomId: ROOM, winnerId: rows.find((item) => item.isWinner)?.userId ?? rows[0].userId, results: rows } : null;
  return { ...initialSession(), phase: 'finished', connected: true, userId: me, roomId: ROOM, players: room.players, room, result, ...overrides };
}

function readRow(userId, rank, stats = {}) {
  return { userId, rank, isWinner: rank === 1, paintedTiles: 40 - rank * 5, totalPainted: 60 - rank, kills: 0, deaths: 0, cardsUsed: 10 + rank, costUsed: 25 + rank, ...stats };
}

function exitSocket(sendOk = true) {
  const listeners = new Set();
  return {
    sent: 0,
    onEvent(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    leaveRoom() { if (sendOk) this.sent += 1; return sendOk; },
    emit(event) { for (const listener of listeners) listener(event); },
    listeners,
  };
}

function rowsOf(html) {
  return [...html.matchAll(/<li class="result-row[^"]*" data-user="(\d+)">.*?<\/li>/g)].map(([text, user]) => ({ user: Number(user), text }));
}

test('game_finished moves to the finished phase without inventing a result', () => {
  const { session, socket } = playing();
  socket.receive({ type: 'game_finished', room_id: ROOM });
  const state = session.getState();
  assert.equal(state.phase, 'finished');
  assert.equal(state.room.status, 'FINISHED');
  assert.equal(state.result, null);
  session.close();
});

test('the result page shows the tally state until game_result arrives', () => {
  const html = render(finishedSession(null), exitSocket());
  assert.match(html, /class="result-page players-2"/);
  assert.match(html, /경기 종료/);
  assert.match(html, /role="status">결과 집계 중</);
  assert.match(html, /서버에서 경기 결과를 집계하고 있습니다/);
  assert.equal(rowsOf(html).length, 2);
  assert.doesNotMatch(html, /WIN|LOSE|\d+위/);
  assert.match(html, /<button type="button" class="result-button is-back" disabled="">/);
});

test('game_result is stored with the real server stats and shown as the final result', () => {
  const { session, socket } = playing();
  socket.receive({ type: 'game_finished', room_id: ROOM });
  socket.receive(gameResult([row(1, 1), row(2, 2)]));
  const { result } = session.getState();
  assert.equal(result.roomId, ROOM);
  assert.equal(result.winnerId, 1);
  assert.deepEqual(result.results[1], { userId: 2, rank: 2, isWinner: false, paintedTiles: 30, totalPainted: 58, kills: 0, deaths: 0, cardsUsed: 12, costUsed: 27 });
  const html = render(session.getState(), exitSocket());
  assert.match(html, /role="status">WIN</);
  assert.doesNotMatch(html, /<button type="button" class="result-button is-back" disabled/);
  session.close();
});

test('shows WIN for the server winner and LOSE for the others', () => {
  const rows = [readRow(1, 1), readRow(2, 2)];
  const win = render(finishedSession(rows, 1), exitSocket());
  assert.match(win, /class="result-outcome is-win" role="status">WIN</);
  assert.match(win, /내 순위 1위 · 승자 테스트 캐릭터 1/);
  const lose = render(finishedSession(rows, 2), exitSocket());
  assert.match(lose, /class="result-outcome is-lose" role="status">LOSE</);
  assert.match(lose, /내 순위 2위 · 승자 테스트 캐릭터 1/);
});

test('keeps the server tie-break and shared ranks instead of recalculating', () => {
  const tied = [readRow(2, 1, { paintedTiles: 30, kills: 0 }), readRow(1, 2, { paintedTiles: 30, kills: 3 })];
  const html = render(finishedSession(tied, 1), exitSocket());
  assert.match(html, /LOSE/);
  assert.match(html, /승자 테스트 캐릭터 2 · 점령 타일 동점, 서버 판정 우선순위 적용/);
  assert.deepEqual(rowsOf(html).map(({ user }) => user), [2, 1]);

  const shared = [readRow(1, 1), { ...readRow(2, 1), isWinner: true }, readRow(3, 3)];
  const sharedHtml = render(finishedSession(shared, 2), exitSocket());
  assert.match(sharedHtml, /WIN/);
  assert.equal(sharedHtml.match(/class="result-rank">1위</g).length, 2);
  assert.equal(sharedHtml.match(/class="result-rank">3위</g).length, 1);
  assert.doesNotMatch(sharedHtml, />2위</);
});

test('uses the server rank and winner even when the tiles would suggest otherwise', () => {
  const rows = [readRow(1, 1, { paintedTiles: 10 }), readRow(2, 2, { paintedTiles: 90 })];
  const html = render(finishedSession(rows, 2), exitSocket());
  assert.match(html, /LOSE/);
  assert.deepEqual(rowsOf(html).map(({ user }) => user), [1, 2]);
  assert.match(html, /aria-label="우승자".*테스트 캐릭터 1.*플레이어 #1 · 10칸/);
});

test('shows final tiles and only the stats the server sent for each participant', () => {
  const rows = [readRow(1, 1), readRow(2, 2, { totalPainted: null, kills: null, deaths: null, cardsUsed: null, costUsed: null })];
  const html = render(finishedSession(rows), exitSocket());
  const [first, second] = rowsOf(html);
  assert.match(first.text, /<b>35<\/b>칸<\/span><small>최종 점령/);
  assert.match(second.text, /<b>30<\/b>칸<\/span><small>최종 점령/);
  assert.match(first.text, /누적 채색 <b>59<\/b>.*카드 <b>11<\/b>.*코스트 <b>26<\/b>.*처치 <b>0<\/b>.*사망 <b>0<\/b>/);
  assert.doesNotMatch(second.text, /누적 채색|카드|코스트|처치|사망/);
  assert.doesNotMatch(html, /XP|레벨 ?업|경험치 \+/);
  assert.match(html, /경험치 보상 시스템 준비 중/);

  const { session, socket } = playing();
  socket.receive(gameResult([{ user_id: 1, rank: 1, is_winner: true }, { user_id: 2, rank: 2, is_winner: false }]));
  assert.equal(session.getState().result.results[0].paintedTiles, null);
  const bare = render(session.getState(), exitSocket());
  assert.doesNotMatch(bare, /칸<\/span><small>/);
  assert.doesNotMatch(bare, /누적 채색/);
  session.close();
});

test('renders two-player and four-player results with each character and color', () => {
  const two = render(finishedSession([readRow(1, 1), readRow(2, 2)]), exitSocket());
  assert.equal(rowsOf(two).length, 2);
  assert.match(two, /result-avatar is-red/);
  assert.match(two, /result-avatar is-blue/);

  const { session, socket } = playing(4);
  socket.receive({ type: 'game_finished', room_id: ROOM });
  socket.receive(gameResult([row(3, 1), row(1, 2), row(4, 3), row(2, 4)]));
  const four = render(session.getState(), exitSocket());
  assert.match(four, /class="result-page players-4"/);
  assert.deepEqual(rowsOf(four).map(({ user }) => user), [3, 1, 4, 2]);
  assert.match(rowsOf(four)[0].text, /result-avatar is-green.*1위.*테스트 캐릭터 3.*초록 · 플레이어 #3/);
  assert.match(rowsOf(four)[3].text, /result-avatar is-blue.*4위.*테스트 캐릭터 2.*파랑 · 플레이어 #2/);
  assert.match(four, /LOSE/);
  assert.match(four, /result-portrait is-green/);
  session.close();
});

test('highlights only my own row', () => {
  const html = render(finishedSession([readRow(1, 1), readRow(2, 2), readRow(3, 3)], 2), exitSocket());
  const rows = rowsOf(html);
  assert.deepEqual(rows.filter(({ text }) => /class="result-row is-me/.test(text)).map(({ user }) => user), [2]);
  assert.match(rows[1].text, /플레이어 #2 \(나\)/);
  assert.equal(html.match(/\(나\)/g).length, 1);
});

test('duplicate game_result events are ignored once the room result is fixed', () => {
  const { session, socket } = playing();
  socket.receive({ type: 'game_finished', room_id: ROOM });
  socket.receive(gameResult([row(1, 1), row(2, 2)]));
  const first = session.getState().result;
  socket.receive(gameResult([row(2, 1), row(1, 2)]));
  socket.receive(gameResult([row(1, 1), row(2, 2)]));
  assert.equal(session.getState().result, first);
  assert.equal(session.getState().result.results.length, 2);
  assert.equal(rowsOf(render(session.getState(), exitSocket())).length, 2);
  session.close();
});

test('game_result before game_finished still ends the game with the server result', () => {
  const { session, socket } = playing();
  socket.receive(gameResult([row(2, 1), row(1, 2)]));
  assert.equal(session.getState().phase, 'finished');
  assert.equal(session.getState().room.status, 'FINISHED');
  socket.receive({ type: 'game_finished', room_id: ROOM });
  socket.receive({ type: 'game_tick', status: 'FINISHED', remaining_seconds: 0 });
  assert.equal(session.getState().phase, 'finished');
  assert.equal(session.getState().result.winnerId, 2);
  assert.equal(session.playCard(0), false);
  socket.receive(gameResult([row(1, 1), row(2, 2)], { room_id: NEXT_ROOM }));
  assert.equal(session.getState().result.winnerId, 2);
  session.close();
});

test('back to lobby sends room_leave and leaves only after room_left', () => {
  const { session, socket } = playing();
  socket.receive({ type: 'game_finished', room_id: ROOM });
  socket.receive(gameResult([row(1, 1), row(2, 2)]));
  const states = [];
  let left = 0;
  const exit = new ResultExit(session, (state) => states.push(state), () => { left += 1; });
  assert.equal(exit.leave(), true);
  assert.deepEqual(socket.sent.at(-1), { type: 'room_leave' });
  assert.equal(exit.getState().leaving, true);
  assert.equal(left, 0);
  assert.equal(session.getState().roomId, ROOM);
  socket.receive({ type: 'room_left', room_id: ROOM });
  assert.equal(left, 1);
  assert.equal(exit.getState().leaving, false);
  assert.equal(session.getState().phase, 'idle');
  assert.equal(session.getState().roomId, null);
  assert.equal(session.getState().result, null);
  exit.dispose();
  session.close();
});

test('blocks duplicate clicks while the room is being cleaned up', () => {
  const socket = exitSocket();
  const exit = new ResultExit(socket, () => {}, () => {});
  assert.equal(exit.leave(), true);
  assert.equal(exit.leave(), false);
  assert.equal(exit.leave(), false);
  assert.equal(socket.sent, 1);
  const html = render(finishedSession([readRow(1, 1), readRow(2, 2)]), socket);
  assert.match(html, /로비로 돌아가기/);
  exit.dispose();
  assert.equal(socket.listeners.size, 0);
});

test('a failed cleanup shows the server error and can be retried', async () => {
  const socket = exitSocket();
  let left = 0;
  const exit = new ResultExit(socket, () => {}, () => { left += 1; }, 20);
  exit.leave();
  socket.emit({ type: 'error', code: 'result_save_failed', message: '경기 결과 저장에 실패했습니다. 다시 시도해주세요.' });
  assert.deepEqual(exit.getState(), { leaving: false, message: '경기 결과 저장에 실패했습니다. 다시 시도해주세요.' });
  assert.equal(left, 0);

  assert.equal(exit.leave(), true);
  assert.equal(exit.getState().message, '');
  await new Promise((resolve) => setTimeout(resolve, 40));
  assert.deepEqual(exit.getState(), { leaving: false, message: '서버 응답이 없습니다. 다시 시도해 주세요.' });

  assert.equal(exit.leave(), true);
  exit.disconnected();
  assert.equal(exit.getState().leaving, false);
  assert.match(exit.getState().message, /연결이 끊겼습니다/);

  assert.equal(exit.leave(), true);
  socket.emit({ type: 'room_left', room_id: ROOM });
  assert.equal(left, 1);
  assert.equal(socket.sent, 4);
  exit.dispose();

  const offline = new ResultExit(exitSocket(false), () => {}, () => {});
  assert.equal(offline.leave(), false);
  assert.match(offline.getState().message, /서버에 연결되어 있지 않아/);
  offline.dispose();

  const gone = exitSocket();
  let goneLeft = 0;
  const confirmed = new ResultExit(gone, () => {}, () => { goneLeft += 1; });
  gone.emit({ type: 'room_left' });
  assert.equal(goneLeft, 0);
  confirmed.leave();
  gone.emit({ type: 'error', code: 'not_in_room', message: '참가 중인 게임 룸이 없습니다.' });
  assert.equal(goneLeft, 1);
  confirmed.dispose();
});

test('a dropped result screen reconnects and restores the same result once', () => {
  const { session, socket, sockets, last } = playing();
  socket.receive({ type: 'game_finished', room_id: ROOM });
  socket.receive(gameResult([row(1, 1), row(2, 2)]));
  const result = session.getState().result;
  socket.drop();
  assert.equal(session.getState().phase, 'finished');
  assert.equal(session.getState().connected, false);
  const offline = render(session.getState(), exitSocket());
  assert.match(offline, />다시 연결</);
  assert.match(offline, /<button type="button" class="result-button is-back" disabled="">/);
  assert.match(offline, /WIN/);

  assert.equal(session.retry(), true);
  const next = last();
  next.open();
  next.receive({ type: 'auth_success', user_id: 1 });
  assert.deepEqual(next.types(), ['auth', 'room_state']);
  next.receive(roomState());
  next.receive(gameResult([row(1, 1), row(2, 2)]));
  next.receive(roomState());
  assert.equal(session.getState().phase, 'finished');
  assert.equal(session.getState().connected, true);
  assert.equal(session.getState().result, result);
  assert.equal(sockets.length, 2);
  session.close();
});

test('a refreshed page resumes into the result screen and shows the tally until game_result', () => {
  const { session, sockets, last } = setup();
  session.resume('jwt-token');
  const socket = last();
  socket.open();
  socket.receive({ type: 'auth_success', user_id: 2 });
  socket.receive(roomState());
  assert.equal(session.getState().phase, 'finished');
  assert.equal(session.getState().result, null);
  assert.match(render(session.getState(), exitSocket()), /결과 집계 중/);
  socket.receive(gameResult([row(1, 1), row(2, 2)]));
  const html = render(session.getState(), exitSocket());
  assert.match(html, /LOSE/);
  assert.match(html, /플레이어 #2 \(나\)/);
  assert.deepEqual(socket.types(), ['auth']);
  assert.equal(sockets.length, 1);
  session.close();
});

test('a new match never reuses the previous room, result, hand or tiles', () => {
  const { session, socket, sockets } = playing();
  socket.receive({ type: 'game_finished', room_id: ROOM });
  socket.receive(gameResult([row(1, 1), row(2, 2)]));
  session.leaveRoom();
  socket.receive({ type: 'room_left', room_id: ROOM });
  session.release();
  assert.deepEqual({ ...session.getState(), connected: false, userId: null }, initialSession());

  assert.equal(session.start('jwt-token', 'character_02'), true);
  assert.deepEqual(socket.sent.at(-1), { type: 'queue_join', character_id: 'character_02' });
  socket.receive({ type: 'match_found', room_id: NEXT_ROOM, map_id: 'default', players: wirePlayers(2) });
  assert.equal(session.getState().result, null);
  assert.equal(session.getState().room, null);
  socket.receive(gameResult([row(2, 1), row(1, 2)], { room_id: ROOM }));
  assert.equal(session.getState().result, null);
  assert.equal(session.getState().phase, 'matched');
  assert.equal(sockets.length, 1);
  session.close();
});

test('the result page reuses the App socket and opens no connection of its own', async () => {
  const app = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8');
  assert.match(app, /<ResultPage session=\{match\} socket=\{gameSocket\} onLeft=\{finishResult\} \/>/);
  assert.equal(app.match(/new GameSocket\(/g).length, 1);
  assert.match(app, /function finishResult\(\) \{\s+gameSocket\.release\(\);\s+goLobby\(\);/);
  for (const path of ['../src/pages/ResultPage.tsx', '../src/services/resultExit.ts']) {
    const source = await readFile(new URL(path, import.meta.url), 'utf8');
    assert.doesNotMatch(source, /new WebSocket|new GameSocket|queue_join|\/auth|level|experience/i);
  }
  const page = await readFile(new URL('../src/pages/GamePage.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(page, /경기 정리|서버 결과를 기다립니다/);
});
