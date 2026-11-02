import { test, expect, type Page, type WebSocketRoute } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import path from 'node:path';

const index = path.resolve('index.html');
const html = readFileSync(index, 'utf8');
async function enterGame(page: Page) {
  await page.goto(pathToFileURL(index).href);
  await page.getByRole('button', { name: '게임 화면 체험하기' }).click();
  await page.getByRole('button', { name: '매칭 화면 체험' }).click();
  await page.getByText('화면 체험 도구', { exact: true }).click();
  await page.getByRole('button', { name: '게임 5분', exact: true }).click();
  await expect(page.locator('.ingame-board canvas')).toBeVisible();
}
async function noOverflow(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
}

test('offline ingame follows the card/grid storyboard with fixed attack previews', async ({ page }, info) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await enterGame(page);
  await expect(page.getByRole('img', { name: 'CRISS CROSS 콘티 원본 카드' })).toBeVisible();
  await expect(page.getByText('중심을 기준으로 X자 범위')).toBeVisible();
  await expect(page.locator('.server-hand-card')).toHaveCount(3);
  const left = await page.locator('.ingame-card-panel').boundingBox(), right = await page.locator('.ingame-map-panel').boundingBox();
  expect(left!.x + left!.width).toBeCloseTo(right!.x, 0);
  expect(left!.width / (left!.width + right!.width)).toBeCloseTo(0.435, 2);
  const position = await page.getByTestId('player-position').innerText();
  await page.keyboard.press('ArrowRight'); await expect(page.getByTestId('player-position')).toHaveText(position);
  await page.waitForTimeout(800);
  await page.screenshot({ path: info.outputPath('ingame-criss-cross.png'), fullPage: true });
  await page.getByRole('button', { name: 'SWASTIKA', exact: true }).click();
  await expect(page.getByRole('img', { name: 'SWASTIKA 콘티 원본 카드' })).toBeVisible();
  await page.screenshot({ path: info.outputPath('ingame-swastika.png'), fullPage: true });
  await page.getByRole('button', { name: 'THE LINE', exact: true }).click();
  await page.getByRole('button', { name: '직선 오른쪽', exact: true }).click();
  await expect(page.getByRole('button', { name: '직선 오른쪽' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText('상·하·좌·우 중 선택한 방향으로 직선 범위')).toBeVisible();
  await page.screenshot({ path: info.outputPath('ingame-the-line.png'), fullPage: true });
  await noOverflow(page);
  expect(errors).toEqual([]);
});

test('ingame fits mobile and camera dragging does not move the avatar', async ({ page }, info) => {
  await page.setViewportSize({ width: 390, height: 844 }); await enterGame(page);
  await page.getByRole('button', { name: 'THE LINE', exact: true }).click();
  const position = await page.getByTestId('player-position').innerText();
  const canvas = await page.locator('canvas').boundingBox();
  await page.mouse.move(canvas!.x + 90, canvas!.y + 120); await page.mouse.down();
  await page.mouse.move(canvas!.x + 40, canvas!.y + 180, { steps: 4 }); await page.mouse.up();
  await expect(page.getByRole('button', { name: '내 캐릭터 추적' })).toHaveAttribute('aria-pressed', 'false');
  await expect(page.getByTestId('player-position')).toHaveText(position);
  await page.getByRole('button', { name: '내 캐릭터 추적' }).click();
  await expect(page.getByRole('button', { name: '내 캐릭터 추적' })).toHaveAttribute('aria-pressed', 'true');
  await noOverflow(page); await page.screenshot({ path: info.outputPath('ingame-mobile.png'), fullPage: true });
});

function roomState() {
  return { type: 'room_state', room_id: 'server-room', status: 'PLAYING', map_id: 'default',
    map: { width: 20, height: 20, tiles: Array.from({ length: 20 }, () => Array(20).fill('NORMAL')) },
    players: [
      { user_id: 1, character_id: 'character_01', color: 'RED', spawn: [0, 0], position: [10, 10], alive: true, connected: true, tile_count: 0 },
      { user_id: 2, character_id: 'character_02', color: 'BLUE', spawn: [19, 19], position: [19, 19], alive: true, connected: true, tile_count: 0 },
    ], my_cost: 6, my_hand: ['move_dash', 'attack_slash', 'move_dash'], my_next_card: 'attack_bomb', my_ultimate_used: false,
    max_cost: 10, ready_remaining_seconds: 0, remaining_seconds: 300,
    tile_owners: Array.from({ length: 20 }, () => Array(20).fill(null)) };
}

test('actual server protocol: auth, authoritative hand/cost, indexed use, cross-validation and reconnect', async ({ page }, info) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  const messages: Record<string, unknown>[] = []; let server: WebSocketRoute;
  let matched = false, holdState = false, connectionCount = 0;
  let state = roomState();
  await page.route('http://paint-rush.test/**', async route => {
    if (route.request().url().endsWith('/api/v1/auth/login')) await route.fulfill({ json: { access_token: 'test-token', token_type: 'bearer' } });
    else await route.fulfill({ contentType: 'text/html', body: html });
  });
  await page.routeWebSocket('ws://paint-rush.test/ws/game', ws => {
    server = ws; connectionCount++;
    ws.onMessage(raw => {
      const data = JSON.parse(String(raw)); messages.push(data);
      if (data.type === 'auth') ws.send(JSON.stringify({ type: 'auth_success', user_id: 1 }));
      if (data.type === 'room_state' && !holdState) ws.send(JSON.stringify(matched ? state : { type: 'error', code: 'not_in_room', message: '참가 중인 게임 룸이 없습니다.' }));
      if (data.type === 'queue_join') {
        matched = true; ws.send(JSON.stringify({ type: 'queue_waiting', waiting: 1, match_size: 2 }));
        ws.send(JSON.stringify({ type: 'match_found', room_id: 'server-room', map_id: 'default', players: state.players }));
      }
      if (data.type === 'card_use') holdState = true;
    });
  });
  await page.goto('http://paint-rush.test/');
  await page.getByLabel('아이디', { exact: true }).fill('painter'); await page.getByLabel('비밀번호', { exact: true }).fill('password123');
  await page.getByRole('button', { name: '확인', exact: true }).click();
  await page.getByLabel('플레이 방식').selectOption('server'); await page.getByRole('button', { name: '매칭 시작' }).click();
  await expect(page.locator('.ingame-board canvas')).toBeVisible();
  expect(messages.find(data => data.type === 'auth')).toEqual({ type: 'auth', token: 'test-token' });
  expect(messages.find(data => data.type === 'queue_join')).toEqual({ type: 'queue_join', character_id: 'character_01' });
  await expect(page.locator('.server-hand-card')).toHaveCount(3);
  await expect(page.getByTestId('player-position')).toHaveText('10, 10');
  await page.locator('.server-hand-card').nth(1).click();
  await expect.poll(() => messages.filter(data => data.type === 'card_use').length).toBe(1);
  expect(messages.find(data => data.type === 'card_use')).toEqual({ type: 'card_use', hand_index: 1 });
  await expect(page.locator('.hand-heading strong b')).toHaveText('6');
  await expect(page.locator('.server-hand-card').first()).toBeDisabled();
  await expect(page.getByTestId('player-position')).toHaveText('10, 10');
  state = { ...state, my_cost: 3, my_hand: ['move_dash', 'attack_bomb', 'move_dash'], my_next_card: 'attack_slash' };
  server!.send(JSON.stringify({ type: 'hand_updated', hand: state.my_hand, cost: 3 }));
  server!.send(JSON.stringify({ type: 'attack_requested', user_id: 1, card_id: 'attack_slash' }));
  holdState = false; server!.send(JSON.stringify(state));
  await expect(page.locator('.hand-heading strong b')).toHaveText('3');
  await expect(page.locator('.server-hand-card').nth(1)).toContainText('폭탄');
  await expect(page.locator('.next-card')).toContainText('슬래시');
  await expect(page.getByTestId('player-position')).toHaveText('10, 10');
  await page.screenshot({ path: info.outputPath('ingame-server.png'), fullPage: true });
  holdState = true;
  server!.send(JSON.stringify({ ...state, my_cost: 99 }));
  await expect(page.locator('.connection-overlay')).toBeVisible();
  await expect(page.locator('.hand-heading strong b')).toHaveText('3');
  server!.send(JSON.stringify({ ...state, my_cost: 8 }));
  await expect(page.locator('.connection-overlay')).toHaveCount(0);
  await expect(page.locator('.hand-heading strong b')).toHaveText('8');
  server!.close(); await expect(page.locator('.connection-overlay')).toBeVisible();
  holdState = false;
  await page.getByRole('button', { name: '재접속', exact: true }).click();
  await expect(page.locator('.ingame-board canvas')).toBeVisible();
  expect(connectionCount).toBe(2);
  expect(messages.filter(data => data.type === 'queue_join')).toHaveLength(1);
  state = { ...state, status: 'FINISHED', remaining_seconds: 0 };
  server!.send(JSON.stringify({ type: 'game_finished', room_id: 'server-room' }));
  await expect(page.getByRole('status').filter({ hasText: '서버 결과를 기다립니다' })).toBeVisible();
  expect(errors).toEqual([]);
});
