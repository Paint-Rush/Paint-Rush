import { COLORS, DEFAULT_DECK, canUseCard, getCard, getCardCost, rankPlayers, type GameState, type Player, type Point, type TileMap } from './model';
import { resolveCardAction } from './cardActions';

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
    remainingMs: 300000, readyMs: 10000, queueMs: 0, receivedAt: Date.now(), nextCard: deck[3] ?? null, previewQueue: deck.slice(3) };
}

export function paint(state: GameState, player: Player, targets: Point[]) {
  const owners = state.owners.map(row => [...row]);
  for (const { x, y } of targets) {
    if (x < 0 || y < 0 || x >= state.map.width || y >= state.map.height
      || !state.map.legend[String(state.map.tiles[y][x])].walkable
      || state.map.legend[String(state.map.tiles[y][x])].name === 'GLASS' || owners[y][x] === player.id) continue;
    owners[y][x] = player.id;
    player.painted++;
  }
  state.owners = owners;
  for (const p of state.players) p.tiles = owners.flat().filter(id => id === p.id).length;
}

/** Exercise the current card/cost/movement contract in the offline preview only. */
export function usePreviewCard(state: GameState, handIndex: number, target?: Point): GameState {
  if (state.authoritative) throw new Error('서버 경기에는 체험 데이터를 적용할 수 없습니다.');
  const self = state.players.find(player => player.id === state.selfId);
  if (!self || !Number.isInteger(handIndex) || handIndex < 0 || handIndex >= self.hand.length) throw new Error('현재 손패를 선택해 주세요.');
  const used = self.hand[handIndex], card = getCard(used);
  if (!card || state.remainingMs <= 0 || state.inputReady === false || !canUseCard(state, self, used)) throw new Error('현재 상태나 코스트로는 카드를 사용할 수 없습니다.');
  const action = resolveCardAction(used, self.position, state.map, target);
  const queue = state.previewQueue ?? self.deck.slice(3);
  if (queue.length !== 3 || queue.some(id => !getCard(id))) throw new Error('체험 덱을 다시 불러와 주세요.');
  const next = structuredClone(state), player = next.players.find(value => value.id === next.selfId)!;
  const cost = getCardCost(state, used);
  if (cost === null) throw new Error('카드 코스트를 확인할 수 없습니다.');
  player.cost -= cost;
  player.costUsed += cost;
  player.cardsUsed++;
  player.hand[handIndex] = queue[0];
  next.previewQueue = [...queue.slice(1), used];
  next.nextCard = next.previewQueue[0];
  player.position = action.destination;
  paint(next, player, action.tiles);
  next.previewAction = { id: player.cardsUsed, cardId: used, tiles: action.tiles, from: { ...self.position }, to: { ...action.destination } };
  next.receivedAt = Date.now();
  return next;
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
