import { CARDS, COLORS, DEFAULT_DECK, canUseCard, rankPlayers, type GameState, type Player, type Point, type TileMap } from './model';

export function createDemo(map: TileMap, name: string, character: string, deck: string[], random = Math.random): GameState {
  const spawns = [{ x: 0, y: 0 }, { x: map.width - 1, y: 0 }, { x: 0, y: map.height - 1 }, { x: map.width - 1, y: map.height - 1 }];
  for (let i = spawns.length - 1; i > 0; i--) { const j = Math.floor(random() * (i + 1)); [spawns[i], spawns[j]] = [spawns[j], spawns[i]]; }
  const names = [name || '나의 페인터', '블루밍', '잉크러너', '옐로우킥'];
  const characters = [character, 'guard', 'runner', 'blaster'];
  const players = Object.keys(COLORS).map((color, i): Player => ({
    id: i + 1, name: names[i], level: 1, character: characters[i], color: color as Player['color'],
    spawn: { ...spawns[i] }, position: { ...spawns[i] }, alive: true, connected: true, respawnMs: 0,
    deck: i === 0 ? [...deck] : [...DEFAULT_DECK], hand: (i === 0 ? deck : DEFAULT_DECK).slice(0, 4),
    cost: 6, tiles: 0, painted: 0, kills: 0, cardsUsed: 0, costUsed: 0, queueOrder: i,
  }));
  return { phase: 'queue', roomId: 'local-preview', selfId: 1, map, players,
    owners: Array.from({ length: map.height }, () => Array<number | null>(map.width).fill(null)),
    remainingMs: 300000, readyMs: 10000, queueMs: 0, receivedAt: Date.now() };
}

export function paint(state: GameState, player: Player, targets: Point[]) {
  const owners = state.owners.map(row => [...row]);
  for (const { x, y } of targets) {
    if (x < 0 || y < 0 || x >= state.map.width || y >= state.map.height
      || !state.map.legend[String(state.map.tiles[y][x])].walkable || owners[y][x] === player.id) continue;
    owners[y][x] = player.id;
    player.painted++;
  }
  state.owners = owners;
  for (const p of state.players) p.tiles = owners.flat().filter(id => id === p.id).length;
}

export function demoUseCard(state: GameState, cardId: string, target?: Point): GameState {
  const next = structuredClone(state);
  const player = next.players.find(p => p.id === next.selfId)!;
  if (!canUseCard(next, player, cardId)) throw new Error('지금은 이 카드를 사용할 수 없습니다.');
  const card = CARDS.find(card => card.id === cardId)!;
  if (card.type === '이동') {
    const step = card.id === 'dash' ? 2 : 1;
    const point = target ?? { x: player.position.x + (player.position.x < state.map.width / 2 ? step : -step), y: player.position.y };
    const distance = Math.abs(point.x - player.position.x) + Math.abs(point.y - player.position.y);
    if (distance < 1 || distance > step || point.x < 0 || point.y < 0 || point.x >= state.map.width || point.y >= state.map.height
      || !state.map.legend[String(state.map.tiles[point.y][point.x])].walkable) throw new Error('이동 카드의 범위 안에서 빈 타일을 선택해 주세요.');
    // Check intermediate tiles for the two-tile straight dash.
    if (distance === 2 && (point.x === player.position.x || point.y === player.position.y)) {
      const x = (point.x + player.position.x) / 2, y = (point.y + player.position.y) / 2;
      if (!state.map.legend[String(state.map.tiles[y][x])].walkable) throw new Error('장애물을 통과할 수 없습니다.');
    }
    player.position = { ...point };
    paint(next, player, [point]);
  } else {
    const { x, y } = player.position;
    const targets = card.type === '공격' ? [{ x, y }, { x: x - 1, y }, { x: x + 1, y }, { x, y: y - 1 }, { x, y: y + 1 }] : [{ x, y }];
    paint(next, player, targets);
  }
  player.cost -= card.cost; player.costUsed += card.cost; player.cardsUsed++;
  const playedIndex = player.deck.indexOf(cardId);
  player.deck.push(...player.deck.splice(playedIndex, 1));
  player.hand = player.deck.slice(0, 4);
  return next;
}

/** Local preview only. Never used to decide a live server game's state. */
export class DemoClock {
  costMs = 0;
  enemyMs = 0;
  advance(state: GameState, deltaMs: number): GameState {
    const next = structuredClone(state);
    next.receivedAt = Date.now();
    if (next.phase === 'queue') {
      next.queueMs += deltaMs;
      if (next.queueMs >= 3000) next.phase = 'ready';
      return next;
    }
    if (next.phase === 'ready') {
      next.readyMs = Math.max(0, next.readyMs - deltaMs);
      if (next.readyMs === 0) {
        next.phase = 'playing';
        for (const player of next.players) paint(next, player, [player.spawn]);
      }
      return next;
    }
    if (next.phase !== 'playing' && next.phase !== 'deathmatch') return state;
    const actualDelta = Math.min(deltaMs, next.remainingMs);
    const normalMs = Math.min(actualDelta, Math.max(0, next.remainingMs - 60000));
    this.costMs += normalMs / 860 + (actualDelta - normalMs) / 450;
    const cost = Math.floor(this.costMs); this.costMs -= cost;
    for (const player of next.players) {
      if (!player.alive) {
        player.respawnMs = Math.max(0, player.respawnMs - actualDelta);
        if (player.respawnMs === 0) { player.alive = true; player.position = { ...player.spawn }; }
      }
      player.cost = Math.min(10, player.cost + cost);
    }
    this.enemyMs += actualDelta;
    if (this.enemyMs >= 1600) {
      this.enemyMs %= 1600;
      for (const p of next.players.slice(1)) {
        const dx = p.position.x < next.map.width / 2 ? 1 : -1;
        const x = p.position.x + dx, y = p.position.y;
        if (next.map.legend[String(next.map.tiles[y][x])]?.walkable) p.position = { x, y };
        else p.position.y = Math.max(0, Math.min(next.map.height - 1, y + (y < next.map.height / 2 ? 1 : -1)));
        paint(next, p, [p.position]);
      }
    }
    next.remainingMs = Math.max(0, next.remainingMs - actualDelta);
    next.phase = next.remainingMs === 0 ? 'finished' : next.remainingMs <= 60000 ? 'deathmatch' : 'playing';
    if (next.phase === 'finished') next.winnerId = rankPlayers(next.players)[0].id;
    return next;
  }
}
