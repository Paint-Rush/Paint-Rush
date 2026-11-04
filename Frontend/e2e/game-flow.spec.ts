import { test, expect, type Page, type WebSocketRoute } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import path from 'node:path';
const index = path.resolve('index.html'), html = readFileSync(index, 'utf8');
const deck = ['jumper', 'dash', 'sliding', 'criss_cross', 'swastika', 'the_line'];
const profile = { user_id: 1, username: 'painter', display_name: '팀 페인터', preferred_character_id: 'character_01' };
const characters = { characters: [{ character_id: 'character_01', name: '테스트 캐릭터 1', ultimate_id: 'ultimate_01', ultimate_name: '테스트 궁극기 1' }, { character_id: 'character_02', name: '테스트 캐릭터 2', ultimate_id: 'ultimate_02', ultimate_name: '테스트 궁극기 2' }] };
const cards = { deck_size: 6, cards: [{ card_id: 'dash', type: 'MOVE', cost: 3 }, { card_id: 'jumper', type: 'MOVE', cost: 4 }, { card_id: 'criss_cross', type: 'ATTACK', cost: 5 }, { card_id: 'swastika', type: 'ATTACK', cost: 6 }, { card_id: 'sliding', type: 'MOVE', cost: 4 }, { card_id: 'the_line', type: 'ATTACK', cost: 3 }] };
async function login(page: Page) {
  await page.goto('http://paint-rush.test/');
  await page.getByLabel('아이디', { exact: true }).fill('painter');
  await page.getByLabel('비밀번호', { exact: true }).fill('password123');
  await page.getByRole('button', { name: '로그인', exact: true }).click();
  await expect(page.getByRole('main', { name: '로비', exact: true })).toBeVisible();
}
async function mockApi(page: Page, writes: unknown[] = [], failSave = false) {
  await page.route('http://paint-rush.test/**', async route => {
    const request = route.request(), url = new URL(request.url()).pathname;
    if (url === '/api/v1/auth/login') return route.fulfill({ json: { access_token: 'test-token', token_type: 'bearer' } });
    if (request.method() === 'PUT' || request.method() === 'PATCH') {
      writes.push({ url, body: request.postDataJSON() });
      return route.fulfill(failSave ? { status: 500, json: {} } : { json: url.endsWith('deck') ? { cards: request.postDataJSON().cards } : { ...profile, ...request.postDataJSON() } });
    }
    const data: Record<string, unknown> = { '/characters': characters, '/cards': cards, '/users/me/deck': { cards: deck }, '/users/me/profile': profile, '/users/me/level': { level: 4, total_experience: 2345 } };
    if (url in data) return route.fulfill({ json: data[url] });
    await route.fulfill({ contentType: 'text/html', body: html });
  });
}
async function toSettings(page: Page) {
  await page.getByRole('button', { name: 'GAME', exact: true }).click();
  await expect(page.getByRole('main', { name: '캐릭터 선택', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Play', exact: true }).click();
}
async function mapTilePoint(page: Page, x: number, y: number) {
  await expect.poll(() => page.locator('.battle-map canvas').evaluate(canvas => {
    const bounds = canvas.getBoundingClientRect(), padding = Math.min(16, Math.min(bounds.width, bounds.height) * 0.04);
    const fit = Math.min(bounds.width - padding * 2, bounds.height - padding * 2) / (20 * 48);
    return Math.abs(Number((canvas as HTMLElement).dataset.scale) - fit * Number((canvas as HTMLElement).dataset.zoom));
  })).toBeLessThan(0.002);
  const box = (await page.locator('.battle-map canvas').boundingBox())!;
  const camera = await page.locator('.battle-map canvas').evaluate(canvas => ({ scale: Number(canvas.dataset.scale), x: Number(canvas.dataset.centerX), y: Number(canvas.dataset.centerY) }));
  const tile = camera.scale * 48;
  return { x: box.x + box.width / 2 + (x + 0.5 - camera.x) * tile,
    y: box.y + box.height / 2 + (y + 0.5 - camera.y) * tile };
}
async function chooseMapTile(page: Page, x: number, y: number) {
  const point = await mapTilePoint(page, x, y);
  await page.mouse.click(point.x, point.y);
  await expect(page.locator('.battle-player-info span').last()).toHaveText(`목적지 ${x}, ${y}`);
}

async function checkMapCorners(page: Page) {
  for (const [x, y] of [[0, 0], [19, 0], [0, 19], [19, 19]]) await chooseMapTile(page, x, y);
}
async function checkPlayableMapSize(page: Page, minimumTile: number) {
  const box = (await page.locator('.battle-map canvas').boundingBox())!;
  const padding = Math.min(16, Math.min(box.width, box.height) * 0.04);
  expect((Math.min(box.width, box.height) - padding * 2) / 20).toBeGreaterThanOrEqual(minimumTile);
  const scene = (await page.getByRole('main', { name: '인게임', exact: true }).boundingBox())!;
  expect(scene.height).toBeCloseTo(page.viewportSize()!.height, 0);
  expect(scene.y).toBe(0);
}
function roomState() {
  return { type: 'room_state', room_id: 'server-room', status: 'READY', map_id: 'default', map: { width: 20, height: 20, tiles: Array.from({ length: 20 }, () => Array(20).fill('NORMAL')) },
    players: [{ user_id: 1, character_id: 'character_02', color: 'RED', spawn: [0, 0], position: [10, 10], alive: true, connected: true, tile_count: 0 }, { user_id: 2, character_id: 'character_01', color: 'BLUE', spawn: [19, 19], position: [19, 19], alive: true, connected: true, tile_count: 0 }],
    my_cost: 6, my_hand: ['dash', 'criss_cross', 'dash'], my_next_card: 'swastika', my_ultimate_used: false, max_cost: 10, ready_remaining_seconds: 10, remaining_seconds: 300,
    tile_owners: Array.from({ length: 20 }, () => Array(20).fill(null)) };
}

test('offline preview keeps the team lobby and connects character selection to the combat storyboard', async ({ page }, info) => {
  const errors: string[] = []; page.on('pageerror', error => errors.push(error.message));
  await page.goto(pathToFileURL(index).href);
  await expect(page.getByRole('button', { name: 'START', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '체험하기', exact: true }).click();
  await expect(page.getByText('화면 체험 · 게스트')).toBeVisible();
  await page.screenshot({ path: info.outputPath('team-lobby-integrated.png') });
  await toSettings(page);
  await page.getByRole('button', { name: '매칭 화면 체험' }).click();
  await page.getByText('화면 체험 도구', { exact: true }).click();
  await page.getByRole('button', { name: '게임 5분', exact: true }).click();
  await expect(page.locator('.battle-map canvas')).toBeVisible();
  await expect(page.locator('.server-hand-card')).toHaveCount(3);
  await expect(page.getByRole('region', { name: '타일 채색 비율' })).toContainText('내 영역 1칸');
  await page.getByText('화면 체험 도구', { exact: true }).click();
  await checkMapCorners(page);
  await checkPlayableMapSize(page, 34);
  await page.mouse.move(0, 0);
  await page.screenshot({ path: info.outputPath('battle-desktop.png') });
  await page.setViewportSize({ width: 1280, height: 720 });
  await checkMapCorners(page);
  await checkPlayableMapSize(page, 24);
  await page.screenshot({ path: info.outputPath('battle-laptop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await checkMapCorners(page);
  await checkPlayableMapSize(page, 17);
  await page.screenshot({ path: info.outputPath('battle-mobile.png') });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await checkMapCorners(page);
  const position = await page.getByTestId('player-position').innerText();
  const canvas = page.locator('.battle-map canvas'), box = (await canvas.boundingBox())!;
  const center = () => canvas.evaluate(element => [Number(element.dataset.centerX), Number(element.dataset.centerY)]);
  await expect(page.getByRole('button', { name: '내 중심', exact: true })).toHaveCount(0);
  await expect(page.getByRole('slider', { name: '카메라 확대' })).toHaveCount(0);
  await expect(page.locator('.battle-minimap')).toHaveCount(0);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -150); await page.mouse.wheel(0, -150);
  await expect.poll(() => canvas.evaluate(element => Number(element.dataset.zoom))).toBeGreaterThan(2);
  await expect(page.locator('.battle-minimap')).toBeVisible();
  await chooseMapTile(page, 8, 8);
  const beforeDrag = await center();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down({ button: 'right' });
  await page.mouse.move(box.x + box.width / 2 - 130, box.y + box.height / 2 - 70, { steps: 5 });
  await page.mouse.up({ button: 'right' });
  await expect.poll(center).not.toEqual(beforeDrag);
  await expect(page.getByTestId('player-position')).toHaveText(position);
  await expect(page.getByText('목적지 8, 8', { exact: true })).toBeVisible();
  const zoom = Number(await canvas.getAttribute('data-zoom'));
  const [px, py] = position.split(',').map(Number), tile = Number(await canvas.getAttribute('data-scale')) * 48;
  const halfX = box.width / (2 * tile), halfY = box.height / (2 * tile);
  const expectedCenter = [halfX >= 10 ? 10 : Math.max(halfX, Math.min(20 - halfX, px + 0.5)), halfY >= 10 ? 10 : Math.max(halfY, Math.min(20 - halfY, py + 0.5))];
  await page.keyboard.press('Space');
  await expect.poll(async () => (await center()).map(value => value.toFixed(3))).toEqual(expectedCenter.map(value => value.toFixed(3)));
  expect(Number(await canvas.getAttribute('data-zoom'))).toBe(zoom);
  await page.screenshot({ path: info.outputPath('battle-player-focus.png') });
  const mini = (await page.locator('.battle-minimap svg').boundingBox())!;
  await page.mouse.click(mini.x + mini.width / 2, mini.y + mini.height / 2);
  await expect.poll(center).toEqual([10, 10]);
  const firstCard = page.locator('.server-hand-card').first(), originalSize = (await firstCard.boundingBox())!;
  await firstCard.hover();
  await expect(firstCard).toHaveAttribute('aria-pressed', 'true');
  await expect(firstCard.locator('.card-selected-label')).toHaveText('선택됨');
  await expect.poll(async () => (await firstCard.boundingBox())!.width).toBeGreaterThan(originalSize.width * 1.09);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await expect(firstCard).toHaveClass(/active-card/);
  await page.screenshot({ path: info.outputPath('selected-card-and-minimap.png') });
  await page.keyboard.press('Escape');
  await expect(firstCard).not.toHaveClass(/active-card/);
  await page.getByRole('button', { name: '전체 맵 크기로 돌아가기', exact: true }).click();
  await expect.poll(() => canvas.getAttribute('data-zoom')).toBe('1');
  await expect(page.locator('.battle-minimap')).toHaveCount(0);
  await checkMapCorners(page);
  expect(errors).toEqual([]);
});

test('all six supplied cards execute their movement and painting in offline preview', async ({ page }, info) => {
  const errors: string[] = [], sockets: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('websocket', socket => sockets.push(socket.url()));
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.clock.install();
  await page.addInitScript(() => { Math.random = () => 0.999; });
  await page.goto(pathToFileURL(index).href);
  await page.getByRole('button', { name: '체험하기', exact: true }).click();
  await toSettings(page);
  await expect(page.locator('.deck-grid .mini-card')).toHaveCount(6);
  for (const name of ['JUMPER', 'DASH', 'SLIDING', 'CRISS CROSS', 'SWASTIKA', 'THE LINE']) await expect(page.locator('.deck-grid')).toContainText(name);
  await page.getByText('맵 불러오기 · 연결 설정', { exact: true }).click();
  await page.locator('input[type=file]').setInputFiles({ name: 'card-test.json', mimeType: 'application/json', buffer: Buffer.from(JSON.stringify({ map_id: 'card-test', width: 20, height: 20, tiles: Array.from({ length: 20 }, () => Array(20).fill('NORMAL')) })) });
  await page.getByRole('button', { name: '매칭 화면 체험' }).click();
  await page.getByText('화면 체험 도구', { exact: true }).click();
  await page.getByRole('button', { name: '게임 5분', exact: true }).click();
  await expect(page.locator('.battle-map canvas')).toBeVisible();
  await page.getByText('화면 체험 도구', { exact: true }).click();
  await page.clock.pauseAt(await page.evaluate(() => Date.now()) + 200);
  await page.clock.fastForward(4000);
  await expect(page.locator('.hand-heading b')).toHaveText('10');
  await expect(page.getByText('미정', { exact: false })).toHaveCount(0);
  await expect(page.getByTestId('player-position')).toHaveText('0, 0');
  const coverage = page.getByRole('region', { name: '타일 채색 비율' });
  const card = (name: string) => page.getByRole('button', { name: new RegExp(`번 손패 ${name} 사용$`) });
  const choose = async (name: string, tx: number, ty: number) => {
    await page.mouse.move(0, 0);
    await page.clock.runFor(1000);
    await card(name).hover();
    await expect(card(name)).toHaveAttribute('aria-pressed', 'true');
    const point = await mapTilePoint(page, tx, ty);
    await page.mouse.move(point.x, point.y);
    await page.clock.runFor(32);
    await chooseMapTile(page, tx, ty);
  };
  await expect(page.locator('.server-hand-card.usable')).toHaveCount(3);
  await expect(card('DASH')).toHaveCSS('background-color', 'rgb(227, 245, 232)');
  const beforeHover = await page.locator('.battle-map canvas').screenshot();
  await card('JUMPER').hover();
  await page.clock.runFor(32);
  expect(beforeHover.equals(await page.locator('.battle-map canvas').screenshot())).toBe(false);
  await expect(page.getByTestId('player-position')).toHaveText('0, 0');
  await expect(page.locator('.hand-heading b')).toHaveText('10');
  await expect(coverage).toContainText('내 영역 1칸');
  await page.screenshot({ path: info.outputPath('jumper-hover-range.png') });
  await chooseMapTile(page, 3, 0);
  await expect(page.locator('.battle-card-hint')).toContainText('5×5');
  await expect(page.getByTestId('player-position')).toHaveText('0, 0');
  await expect(page.locator('.hand-heading b')).toHaveText('10');
  await chooseMapTile(page, 2, 2);
  await expect(page.locator('.hand-heading b')).toHaveText('6');
  await expect(page.getByTestId('player-position')).toHaveText('2, 2');
  await expect(page.locator('.server-hand-card').first()).toContainText('CRISS CROSS');
  await expect(page.locator('.next-card')).toContainText('SWASTIKA');
  await card('DASH').hover();
  await chooseMapTile(page, 5, 2);
  await expect(page.locator('.battle-card-hint')).toContainText('정확히 4칸');
  await expect(page.getByTestId('player-position')).toHaveText('2, 2');
  await choose('DASH', 6, 2);
  await expect(page.getByTestId('player-position')).toHaveText('6, 2');
  await choose('SLIDING', 8, 4);
  await expect(page.getByTestId('player-position')).toHaveText('8, 4');
  await expect(coverage).toContainText('내 영역 9칸 · 2.3%');
  await page.mouse.move(0, 0);
  await page.clock.fastForward(9000);
  await expect(page.locator('.hand-heading b')).toHaveText('10');
  await expect(page.locator('.hand-card-art')).toHaveCount(3);
  await expect(page.getByRole('button', { name: '1번 손패 CRISS CROSS 사용', exact: true })).toHaveClass(/usable/);
  await expect(page.getByRole('button', { name: '2번 손패 SWASTIKA 사용', exact: true })).toHaveClass(/usable/);
  await expect(page.getByRole('button', { name: '3번 손패 THE LINE 사용', exact: true })).toHaveClass(/usable/);
  await page.screenshot({ path: info.outputPath('three-figma-attack-cards.png') });
  await page.getByRole('button', { name: '1번 손패 CRISS CROSS 사용', exact: true }).click();
  await expect(page.locator('.hand-heading b')).toHaveText('5');
  await expect(page.getByRole('button', { name: '2번 손패 SWASTIKA 사용', exact: true })).toBeDisabled();
  await expect(page.getByRole('button', { name: '2번 손패 SWASTIKA 사용', exact: true })).not.toHaveClass(/usable/);
  await expect(coverage).toContainText('내 영역 15칸 · 3.8%');
  await expect(page.getByTestId('player-position')).toHaveText('8, 4');
  await page.clock.runFor(900);
  await page.getByRole('button', { name: '2번 손패 SWASTIKA 사용', exact: true }).click();
  await expect(coverage).toContainText('내 영역 27칸 · 6.8%');
  await page.clock.fastForward(3000);
  await choose('THE LINE', 8, 5);
  await expect(coverage).toContainText('내 영역 29칸 · 7.2%');
  await expect(page.getByTestId('player-position')).toHaveText('8, 4');
  await expect(page.locator('.server-hand-card').nth(0)).toContainText('JUMPER');
  await expect(page.locator('.server-hand-card').nth(1)).toContainText('DASH');
  await expect(page.locator('.server-hand-card').nth(2)).toContainText('SLIDING');
  await page.clock.runFor(1000);
  await page.mouse.move(0, 0);
  await page.screenshot({ path: info.outputPath('six-cards-executed.png') });
  await page.clock.fastForward(4000);
  await card('JUMPER').hover();
  await page.clock.runFor(32);
  await expect(page.locator('.battle-card-hint')).toContainText('점선·빗금');
  await expect(coverage).toContainText('내 영역 29칸');
  await page.screenshot({ path: info.outputPath('range-over-red-owned-tiles.png') });
  await page.keyboard.press('Escape');
  await expect(page.locator('.battle-card-hint')).toHaveCount(0);
  await expect(page.getByTestId('player-position')).toHaveText('8, 4');
  const origin = await mapTilePoint(page, 8, 4);
  await page.mouse.move(origin.x, origin.y);
  for (let step = 0; step < 3; step++) { await page.mouse.wheel(0, -150); await page.clock.runFor(32); }
  await expect(page.locator('.battle-minimap')).toBeVisible();
  await card('JUMPER').hover();
  const costBefore = Number(await page.locator('.hand-heading b').innerText());
  const canvas = page.locator('.battle-map canvas');
  const center = () => canvas.evaluate(element => [Number(element.dataset.centerX), Number(element.dataset.centerY)]);
  const box = (await canvas.boundingBox())!;
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down({ button: 'right' });
  await page.mouse.move(box.x + box.width / 2 + 50, box.y + box.height / 2 + 30, { steps: 3 });
  await page.mouse.up({ button: 'right' });
  await page.clock.runFor(32);
  await expect(page.getByTestId('player-position')).toHaveText('8, 4');
  await expect(page.locator('.hand-heading b')).toHaveText(String(costBefore));
  await expect(card('JUMPER')).toHaveAttribute('aria-pressed', 'true');
  const cameraBeforeUse = await center();
  await chooseMapTile(page, 9, 5);
  await expect(page.getByTestId('player-position')).toHaveText('9, 5');
  await expect(page.locator('.hand-heading b')).toHaveText(String(costBefore - 4));
  await page.clock.runFor(32);
  expect(await center()).toEqual(cameraBeforeUse);
  await expect(page.locator('.card-selected-label')).toHaveCount(0);
  await page.screenshot({ path: info.outputPath('zoomed-card-execution.png') });
  expect(sockets).toEqual([]);
  expect(errors).toEqual([]);
});

test('six-card API contract keeps movement, paint, costs and game end authoritative', async ({ page }, info) => {
  const errors: string[] = [], writes: unknown[] = [], messages: Record<string, unknown>[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await mockApi(page, writes);
  let server: WebSocketRoute, matched = false, hold = false, connections = 0;
  let state = roomState();
  await page.routeWebSocket('ws://paint-rush.test/ws/game', ws => {
    server = ws; connections++;
    ws.onMessage(raw => {
      const data = JSON.parse(String(raw)); messages.push(data);
      if (data.type === 'auth') ws.send(JSON.stringify({ type: 'auth_success', user_id: 1 }));
      if (data.type === 'room_state' && !hold) ws.send(JSON.stringify(matched ? state : { type: 'error', code: 'not_in_room' }));
      if (data.type === 'queue_join') { matched = true; ws.send(JSON.stringify({ type: 'match_found', room_id: 'server-room' })); }
      if (data.type === 'card_use') hold = true;
      if (data.type === 'room_leave') ws.send(JSON.stringify({ type: 'room_left', room_id: 'server-room' }));
    });
  });
  await login(page);
  await expect(page.getByText('팀 페인터', { exact: true })).toBeVisible();
  await expect(page.locator('.lobby-level')).toContainText('4');
  await page.getByRole('button', { name: 'GAME', exact: true }).click();
  await page.locator('.storyboard-character-picker > summary').click();
  await page.getByRole('radio', { name: '테스트 캐릭터 2', exact: true }).check();
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await page.getByRole('button', { name: '매칭 시작' }).click();
  await expect(page.getByRole('heading', { name: '플레이어 준비 화면' })).toBeVisible();
  expect(writes).toEqual([{ url: '/users/me/deck', body: { cards: deck } }, { url: '/users/me/profile', body: { preferred_character_id: 'character_02' } }]);
  expect(messages.find(m => m.type === 'queue_join')).toEqual({ type: 'queue_join', character_id: 'character_02' });
  state = { ...state, status: 'PLAYING', ready_remaining_seconds: 0 };
  server!.send(JSON.stringify(state));
  await expect(page.locator('.battle-map canvas')).toBeVisible();
  await expect(page.locator('.hand-heading b')).toHaveText('6');
  await expect(page.locator('.server-hand-card').first()).toBeEnabled();
  await page.locator('.server-hand-card').first().hover();
  await chooseMapTile(page, 11, 10);
  expect(messages.filter(m => m.type === 'card_use')).toHaveLength(0);
  await expect(page.locator('.battle-card-hint')).toContainText('정확히 4칸');
  await chooseMapTile(page, 14, 10);
  expect(messages.find(m => m.type === 'card_use')).toEqual({ type: 'card_use', hand_index: 0, target_x: 14, target_y: 10 });
  await expect(page.getByTestId('player-position')).toHaveText('10, 10');
  await expect(page.locator('.hand-heading b')).toHaveText('6');
  state = structuredClone(state); state.players[0].position = [14, 10]; state.players[0].tile_count = 4; state.my_cost = 3; state.my_hand = ['swastika', 'criss_cross', 'dash'];
  for (let x = 11; x <= 14; x++) state.tile_owners[10][x] = 1;
  hold = false;
  server!.send(JSON.stringify({ type: 'player_moved', user_id: 1, x: 14, y: 10, path: [[10, 10], [11, 10], [12, 10], [13, 10], [14, 10]] }));
  server!.send(JSON.stringify({ type: 'tiles_updated', room_id: 'server-room', tiles: [11, 12, 13, 14].map(x => ({ x, y: 10, new_owner: 1, previous_owner: null })) }));
  await expect(page.getByTestId('player-position')).toHaveText('14, 10');
  await expect(page.locator('.hand-heading b')).toHaveText('3');
  await expect(page.getByRole('region', { name: '타일 채색 비율' })).toContainText('내 영역 4칸 · 1.0%');
  await page.screenshot({ path: info.outputPath('battle-server-movement.png') });
  state = { ...state, my_cost: 8 };
  server!.send(JSON.stringify({ type: 'cost_updated', cost: 8, max_cost: 10 }));
  await expect(page.locator('.hand-heading b')).toHaveText('8');
  await page.locator('.server-hand-card').nth(1).click();
  expect(messages.filter(m => m.type === 'card_use').at(-1)).toEqual({ type: 'card_use', hand_index: 1 });
  await expect(page.locator('.hand-heading b')).toHaveText('8');
  state = { ...state, my_cost: 3, my_hand: ['swastika', 'jumper', 'dash'], my_next_card: 'criss_cross' };
  server!.send(JSON.stringify({ type: 'hand_updated', hand: state.my_hand, cost: 3 }));
  hold = false; server!.send(JSON.stringify(state));
  await expect(page.locator('.hand-heading b')).toHaveText('3');
  await expect(page.locator('.server-hand-card.usable')).toHaveCount(1);
  await expect(page.locator('.server-hand-card').nth(2)).toHaveClass(/usable/);
  await expect(page.locator('.next-card')).toContainText('CRISS CROSS');
  await expect(page.getByTestId('player-position')).toHaveText('14, 10');
  state = structuredClone(state); state.players[0].alive = false;
  server!.send(JSON.stringify(state));
  await expect(page.locator('.death-overlay')).toBeVisible();
  await expect(page.locator('.server-hand-card').nth(1)).toBeDisabled();
  state = structuredClone(state); state.players[0].alive = true;
  server!.send(JSON.stringify(state));
  await expect(page.locator('.death-overlay')).toHaveCount(0);
  server!.send(JSON.stringify({ type: 'game_tick', status: 'DEATHMATCH', remaining_seconds: 59 }));
  await expect(page.locator('.battle-timer')).toContainText('데스매치');
  await expect(page.locator('.battle-timer strong')).toHaveText('00:59');
  state = { ...state, status: 'DEATHMATCH', remaining_seconds: 59 };
  hold = true; server!.send(JSON.stringify({ ...state, my_cost: 99 }));
  await expect(page.locator('.connection-overlay')).toBeVisible();
  await expect(page.locator('.hand-heading b')).toHaveText('3');
  hold = false; server!.send(JSON.stringify(state));
  await expect(page.locator('.connection-overlay')).toHaveCount(0);
  server!.close(); await expect(page.locator('.connection-overlay')).toBeVisible();
  await page.getByRole('button', { name: '재접속', exact: true }).click();
  await expect(page.locator('.battle-map canvas')).toBeVisible();
  expect(connections).toBe(2); expect(messages.filter(m => m.type === 'queue_join')).toHaveLength(1);
  state = { ...state, status: 'FINISHED', remaining_seconds: 0 };
  server!.send(JSON.stringify({ type: 'game_finished', room_id: 'server-room' }));
  await expect(page.getByText('경기 종료 · 서버 결과를 기다립니다.', { exact: true })).toBeVisible();
  server!.send(JSON.stringify({ type: 'game_result', room_id: 'server-room', winner_id: 1, results: [
    { user_id: 1, rank: 1, is_winner: true, painted_tiles: 4, total_painted: 4, kills: 0, deaths: 0, cards_used: 2, cost_used: 8 },
    { user_id: 2, rank: 2, is_winner: false, painted_tiles: 0, total_painted: 0, kills: 0, deaths: 0, cards_used: 0, cost_used: 0 },
  ] }));
  await page.getByRole('button', { name: '로비로 돌아가기', exact: false }).click();
  await expect(page.getByRole('main', { name: '로비', exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test('a failed deck save blocks WebSocket matchmaking and lets the user retry', async ({ page }) => {
  const writes: unknown[] = []; let connections = 0;
  await mockApi(page, writes, true);
  await page.routeWebSocket('ws://paint-rush.test/ws/game', () => { connections++; });
  await login(page); await toSettings(page);
  await page.getByRole('button', { name: '매칭 시작' }).click();
  await expect(page.getByRole('status').filter({ hasText: '처리하지 못했습니다' })).toBeVisible();
  expect(connections).toBe(0); expect(writes).toHaveLength(1);
  await expect(page.getByRole('button', { name: '매칭 시작' })).toBeEnabled();
});

test('team registration keeps password confirmation and the automatic intro offers preview without authentication', async ({ page }) => {
  let calls = 0;
  await page.route('http://paint-rush.test/**', async route => {
    if (route.request().url().endsWith('/api/v1/auth/register')) {
      calls++;
      expect(route.request().postDataJSON()).toEqual({ username: 'painter', password: 'password123' });
      return route.fulfill({ json: { id: 1, username: 'painter' } });
    }
    return route.fulfill({ contentType: 'text/html', body: html });
  });
  await page.goto('http://paint-rush.test/');
  await expect(page.getByRole('button', { name: 'START', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '회원가입', exact: true }).click();
  await page.getByLabel('아이디', { exact: true }).fill('painter');
  await page.getByLabel('비밀번호', { exact: true }).fill('password123');
  await page.getByLabel('비밀번호 확인', { exact: true }).fill('mismatch123');
  await page.getByRole('button', { name: '회원가입', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('일치하지 않습니다'); expect(calls).toBe(0);
  await page.getByLabel('비밀번호 확인', { exact: true }).fill('password123');
  await page.getByRole('button', { name: '회원가입', exact: true }).click();
  await expect(page.getByRole('status')).toContainText('회원가입이 완료되었습니다'); expect(calls).toBe(1);
  await expect(page.getByLabel('비밀번호 확인', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '체험하기', exact: true }).click();
  await expect(page.getByRole('main', { name: '로비', exact: true })).toBeVisible();
});
