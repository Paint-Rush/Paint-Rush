import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';

const root = fileURLToPath(new URL('../', import.meta.url));

async function loadRenderer() {
  const result = await build({
    stdin: {
      contents: "import { createElement } from 'react'; import { renderToStaticMarkup } from 'react-dom/server'; import MatchmakingPage from './src/pages/MatchmakingPage'; import { initialSession } from './src/services/gameSocket'; const noop = () => {}; export function render(change) { return renderToStaticMarkup(createElement(MatchmakingPage, { session: { ...initialSession(), ...change }, now: 1000000, onCancel: noop, onRetry: noop, onBack: noop, onLeaveRoom: noop })); }",
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
  const dir = await mkdtemp(join(tmpdir(), 'paint-rush-match-'));
  const file = join(dir, 'matchmaking-page.cjs');
  await writeFile(file, result.outputFiles[0].text);
  const { render } = (await import(pathToFileURL(file).href)).default;
  return { render, cleanup: () => rm(dir, { recursive: true, force: true }) };
}

const players = [
  { userId: 1, color: 'RED', spawn: [0, 0], characterId: 'character_02', characterName: '테스트 캐릭터 2', ultimateId: 'ultimate_02', ultimateName: '테스트 궁극기 2' },
  { userId: 2, color: 'BLUE', spawn: [0, 19], characterId: 'character_01', characterName: '테스트 캐릭터 1', ultimateId: 'ultimate_01', ultimateName: '테스트 궁극기 1' },
];

test('matchmaking page renders each server phase on the storyboard', async () => {
  const { render, cleanup } = await loadRenderer();
  try {
    const waiting = render({ phase: 'waiting', connected: true, queue: { waiting: 1, matchSize: 2 } });
    assert.match(waiting, /storyboard\/matching\.png/);
    assert.match(waiting, /상대를 찾는 중/);
    assert.match(waiting, /대기열 1명 · 2명이 모이면 시작/);
    assert.match(waiting, /<button type="button">매칭 취소<\/button>/);
    assert.equal(waiting.match(/class="match-dots"[^>]*><i><\/i><i><\/i><i><\/i>/g).length, 1);

    const cancelling = render({ phase: 'cancelling', connected: true });
    assert.match(cancelling, /<button type="button" disabled="">취소 중…<\/button>/);

    const failed = render({ phase: 'error', error: { code: 'queue_disconnected', message: '매칭 대기 중 서버 연결이 끊겼습니다. 다시 시도해 주세요.' } });
    assert.match(failed, /class="match-detail is-error">매칭 대기 중 서버 연결이 끊겼습니다/);
    assert.match(failed, />다시 시도</);
    assert.match(failed, />캐릭터 선택으로</);

    const expired = render({ phase: 'error', error: { code: 'auth_failed', message: '로그인 정보가 만료되었거나 올바르지 않습니다. 다시 로그인해 주세요.' } });
    assert.doesNotMatch(expired, />다시 시도</);
    assert.match(expired, /다시 로그인/);
  } finally {
    await cleanup();
  }
});

test('match found page shows only the real participants and the server ready time', async () => {
  const { render, cleanup } = await loadRenderer();
  try {
    const ready = render({ phase: 'ready', connected: true, userId: 1, roomId: '7f9c2b1e-0000-4000-8000-000000000001', players, readySeconds: 7, syncedAt: 1000000 });
    assert.match(ready, /storyboard\/match-found\.png/);
    assert.equal(ready.match(/class="match-player( is-me)?"/g).length, 2);
    assert.match(ready, /class="match-player is-me"[^>]*><strong[^>]*>테스트 캐릭터 2<\/strong>.*플레이어 #1 \(나\)/);
    assert.match(ready, /테스트 캐릭터 1<\/strong>.*파랑 · 플레이어 #2</);
    assert.equal(ready.match(/class="match-veil"/g).length, 4);
    assert.match(ready, /role="timer">7<\/strong>/);
    assert.match(ready, /방 7f9c2b1e · 연결 유지 중/);
    assert.doesNotMatch(ready, /레벨|전적|Lv\./);

    const started = render({ phase: 'started', connected: true, userId: 1, roomId: 'room-1', players, readySeconds: 0, syncedAt: 1000000 });
    assert.match(started, /경기 시작!/);
    assert.match(started, /인게임 화면 준비 중/);
    assert.match(started, /role="timer">GO</);

    const lost = render({ phase: 'error', roomId: 'room-1', players, error: { code: 'game_disconnected', message: '게임 서버 연결이 끊겼습니다. 다시 연결해 주세요.' } });
    assert.match(lost, /match-found\.png/);
    assert.match(lost, />다시 연결</);
  } finally {
    await cleanup();
  }
});

test('the app keeps one game socket outside the matchmaking page', async () => {
  const app = await readFile(new URL('../src/App.tsx', import.meta.url), 'utf8');
  assert.match(app, /useState\(\(\) => new GameSocket\(/);
  assert.match(app, /useSyncExternalStore\(gameSocket\.subscribe, gameSocket\.getState\)/);
  assert.match(app, /gameSocket\.start\(token\.access_token, characterId\)/);
  assert.match(app, /gameSocket\.close\(\)/);
  const page = await readFile(new URL('../src/pages/MatchmakingPage.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(page, /new WebSocket|GameSocket/);
  const config = await readFile(new URL('../vite.config.ts', import.meta.url), 'utf8');
  assert.equal(config.match(/'\/ws': \{ target: 'ws:\/\/127\.0\.0\.1:8000', ws: true \}/g).length, 2);
  for (const path of ["'/api'", "'/users'", "'/characters'"]) assert.equal(config.split(path).length - 1, 2);
});

test('matchmaking storyboards are the full-resolution Figma originals', async () => {
  for (const [name, size] of [['matching', [1808, 1018]], ['match-found', [1805, 1013]]]) {
    const png = await readFile(new URL(`../public/storyboard/${name}.png`, import.meta.url));
    assert.equal(png.toString('ascii', 1, 4), 'PNG');
    assert.deepEqual([png.readUInt32BE(16), png.readUInt32BE(20)], size);
  }
});
