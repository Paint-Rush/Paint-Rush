import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const index = path.resolve('index.html');
const html = readFileSync(index, 'utf8');
const map = JSON.parse(readFileSync('src/game/maps/crossroads.json', 'utf8'));
async function noOverflow(page: import('@playwright/test').Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
}

test('offline storyboard flow, camera, cards, death and results without a server', async ({ page }, info) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(pathToFileURL(index).href);
  await page.getByRole('button', { name: '게임 화면 체험하기' }).click();
  await expect(page.getByRole('heading', { name: '도시를 물들일' })).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath('01-lobby.png'), fullPage: true });
  await page.getByRole('button', { name: '매칭 화면 체험' }).click();
  await expect(page.getByRole('heading', { name: '매칭 중' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('02-matchmaking.png'), fullPage: true });
  await expect(page.getByRole('heading', { name: '플레이어 준비 화면' })).toBeAttached({ timeout: 7000 });
  await expect(page.locator('.versus-player')).toHaveCount(4);
  await page.screenshot({ path: info.outputPath('03-ready.png'), fullPage: true });
  await page.getByText('화면 체험 도구', { exact: true }).click();
  await page.getByRole('button', { name: '게임 5분', exact: true }).click();
  await expect(page.locator('canvas')).toBeVisible();
  await page.waitForTimeout(1500);
  await expect(page.getByText('게임 화면을 그릴 수 없습니다.', { exact: false })).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('04-game.png'), fullPage: true });
  await page.getByRole('button', { name: '카메라 →', exact: true }).click();
  await expect(page.getByText('자유 카메라', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '내 아바타 추적', exact: false }).click();
  await expect(page.getByText('내 아바타 추적 중', { exact: true })).toBeVisible();
  for (const name of ['흔들림', '왜곡', '충격']) await page.getByRole('button', { name, exact: true }).click();
  await page.locator('.hand-card.card-attack').click();
  await expect(page.locator('.cost-panel>span')).toHaveText('사용한 코스트 4');
  await page.getByRole('button', { name: '사망·리스폰 10초' }).click();
  await expect(page.getByRole('heading', { name: '다시 칠할 기회' })).toBeVisible();
  await expect(page.locator('.hand-card').first()).toBeDisabled();
  await page.waitForTimeout(10300);
  await expect(page.locator('.death-overlay')).toHaveCount(0);
  await page.getByRole('button', { name: '데스매치', exact: true }).click();
  await expect(page.getByText('DEATHMATCH', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '결과', exact: true }).click();
  await expect(page.locator('.result-table-panel tbody tr')).toHaveCount(4);
  await expect(page.locator('.result-table-panel tbody td').filter({ hasText: /^승리$/ })).toHaveCount(1);
  await page.screenshot({ path: info.outputPath('05-results.png'), fullPage: true });
  await page.getByRole('button', { name: '로비로 돌아가기' }).click();
  await expect(page.getByRole('button', { name: '매칭 화면 체험' })).toBeVisible();
  expect(errors).toEqual([]);
});

test('mobile screens, queue cancellation and JSON validation', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(pathToFileURL(index).href);
  await page.getByRole('button', { name: '게임 화면 체험하기' }).click();
  await noOverflow(page);
  await page.getByText('맵 불러오기 · 연결 설정', { exact: true }).click();
  await page.locator('input[type=file]').setInputFiles({ name: 'bad.json', mimeType: 'application/json', buffer: Buffer.from('{"version":1}') });
  await expect(page.getByRole('status')).toContainText('맵 형식');
  await page.getByRole('button', { name: '안내 닫기' }).click();
  await page.locator('input[type=file]').setInputFiles({ name: 'map.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({ ...map, name: '테스트 광장' })) });
  await expect(page.getByRole('status')).toContainText('테스트 광장 맵을 불러왔습니다');
  await page.getByRole('button', { name: '안내 닫기' }).click();
  await page.getByRole('button', { name: '매칭 화면 체험' }).click();
  await noOverflow(page);
  await page.getByRole('button', { name: '매칭 취소' }).click();
  await page.waitForTimeout(3300);
  await expect(page.getByRole('button', { name: '매칭 화면 체험' })).toBeVisible();
  await page.getByRole('button', { name: '매칭 화면 체험' }).click();
  await page.getByText('화면 체험 도구', { exact: true }).click();
  await page.getByRole('button', { name: '준비 10초' }).click();
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath('mobile-ready.png'), fullPage: true });
  await page.getByRole('button', { name: '게임 5분' }).click();
  await expect(page.locator('canvas')).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: info.outputPath('mobile-game.png'), fullPage: true });
});

test('server snapshots drive ready, avatar, death and final winner; cancellation is sent', async ({ page }) => {
  const messages: { type: string; [key: string]: unknown }[] = [];
  await page.route('http://paint-rush.test/**', async route => {
    if (route.request().url().endsWith('/api/v1/auth/login')) await route.fulfill({ json: { access_token: 'test-token', token_type: 'bearer' } });
    else await route.fulfill({ contentType: 'text/html', body: html });
  });
  const players = Object.fromEntries([1, 2].map((id, i) => [id, { user_id: id, username: `서버페인터${id}`, character_id: 'painter', color: i ? 'BLUE' : 'RED',
    spawn: i ? [19, 19] : [0, 0], position: i ? [19, 19] : [0, 0], alive: true, connected: true, deck: ['move', 'attack', 'defend', 'dash', 'splash', 'shield'],
    hand: ['move', 'attack', 'defend', 'dash'], cost: 6, cost_used: 0, cards_used: 0, kills: 0, total_painted: 0 }]));
  const room = { room_id: 'server-room', map, status: 'READY', remaining_ms: 300000, ready_remaining_ms: 10000, self_id: 1, player_order: [1, 2],
    players, tile_counts: { '1': 0, '2': 0 }, tile_owners: Array.from({ length: 20 }, () => Array(20).fill(null)) };
  let server: import('@playwright/test').WebSocketRoute;
  await page.routeWebSocket('ws://paint-rush.test/api/v1/game/ws', ws => {
    server = ws;
    ws.onMessage(raw => {
      const message = JSON.parse(String(raw)); messages.push(message);
      if (message.type === 'authenticate') ws.send(JSON.stringify({ type: 'authenticated' }));
      if (message.type === 'queue.join') ws.send(JSON.stringify({ type: 'queued' }));
      if (message.type === 'card.use') ws.send(JSON.stringify({ type: 'error', message: '카드 범위 밖입니다.' }));
    });
  });
  await page.goto('http://paint-rush.test/');
  await page.getByLabel('아이디', { exact: true }).fill('painter');
  await page.getByLabel('비밀번호', { exact: true }).fill('password123');
  await page.getByRole('button', { name: '확인', exact: true }).click();
  await page.getByLabel('플레이 방식').selectOption('server');
  await page.getByRole('button', { name: '매칭 시작' }).click();
  await expect.poll(() => messages.some(m => m.type === 'queue.join')).toBe(true);
  await page.getByRole('button', { name: '매칭 취소' }).click();
  await expect.poll(() => messages.some(m => m.type === 'queue.cancel')).toBe(true);
  await page.getByRole('button', { name: '매칭 시작' }).click();
  await expect.poll(() => messages.filter(m => m.type === 'queue.join').length).toBe(2);
  server!.send(JSON.stringify({ type: 'snapshot', room }));
  await expect(page.locator('.versus-player')).toHaveCount(2);
  server!.send(JSON.stringify({ type: 'snapshot', room: { ...room, status: 'PLAYING' } }));
  await expect(page.locator('canvas')).toBeVisible();
  await page.locator('.hand-card.card-attack').click();
  await expect(page.getByRole('status')).toContainText('카드 범위 밖');
  await expect(page.locator('.connection-overlay')).toHaveCount(0);
  await expect(page.locator('.hand-card.card-attack')).toBeEnabled();
  const died = structuredClone(room); died.players['1'].alive = false;
  server!.send(JSON.stringify({ type: 'snapshot', room: { ...died, status: 'PLAYING', players: { ...died.players, '1': { ...died.players['1'], respawn_remaining_ms: 10000 } } } }));
  await expect(page.locator('.death-overlay')).toBeVisible();
  server!.send(JSON.stringify({ type: 'snapshot', room: { ...room, status: 'FINISHED', remaining_ms: 0, winner_id: 2, tile_counts: { '1': 1, '2': 5 } } }));
  await expect(page.getByRole('heading', { name: '다음 색을 준비해요' })).toBeVisible();
  await expect(page.locator('.winner-card h2')).toHaveText('서버페인터2');
});
