import { COLORS, DEFAULT_DECK, rankPlayers, type GameState, type Player, type Point, type TileMap } from './model';

export function createDemo(map: TileMap, name: string, character: string, deck: string[], random = Math.random): GameState {
  const spawns = [{ x: 0, y: 0 }, { x: map.width - 1, y: 0 }, { x: 0, y: map.height - 1 }, { x: map.width - 1, y: map.height - 1 }];
  for (let i = spawns.length - 1; i > 0; i--) { const j = Math.floor(random() * (i + 1)); [spawns[i], spawns[j]] = [spawns[j], spawns[i]]; }
  const names = [name || '나의 페인터', '블루밍', '잉크러너', '옐로우킥'];
  const characters = [character, 'guard', 'runner', 'blaster'];
  const players = Object.keys(COLORS).map((color, i): Player => ({
    id: i + 1, name: names[i], level: 1, character: characters[i], color: color as Player['color'],
    spawn: { ...spawns[i] }, position: { ...spawns[i] }, alive: true, connected: true, respawnMs: 0,
    deck: i === 0 ? [...deck] : [...DEFAULT_DECK], hand: (i === 0 ? deck : DEFAULT_DECK).slice(0, 3),
    cost: 6, tiles: 0, painted: 0, kills: 0, cardsUsed: 0, costUsed: 0, queueOrder: i,
  }));
  return { phase: 'queue', roomId: 'local-preview', selfId: 1, map, players,
    owners: Array.from({ length: map.height }, () => Array<number | null>(map.width).fill(null)),
    remainingMs: 300000, readyMs: 10000, queueMs: 0, receivedAt: Date.now(), nextCard: deck[3] ?? null };
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

/** Local preview only. Never used to decide a live server game's state. */
export class DemoClock {
  costMs = 0;
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
    next.remainingMs = Math.max(0, next.remainingMs - actualDelta);
    next.phase = next.remainingMs === 0 ? 'finished' : next.remainingMs <= 60000 ? 'deathmatch' : 'playing';
    if (next.phase === 'finished') next.winnerId = rankPlayers(next.players)[0].id;
    return next;
  }
}
