export type Phase = 'lobby' | 'queue' | 'ready' | 'playing' | 'deathmatch' | 'finished';
export type Color = 'RED' | 'BLUE' | 'GREEN' | 'YELLOW';
export const COLORS: Record<Color, string> = { RED: '#ef5964', BLUE: '#459cf4', GREEN: '#76c969', YELLOW: '#efc94f' };
export interface Point { x: number; y: number }
export interface TileMap {
  version: 1; id: string; name: string; width: number; height: number;
  tiles: number[][];
  legend: Record<string, { name: string; color: string; walkable: boolean }>;
}
export interface Player {
  id: number; name: string; level: number; character: string; color: Color;
  position: Point; spawn: Point; alive: boolean; connected: boolean;
  respawnMs: number; deck: string[]; hand: string[]; cost: number;
  tiles: number; painted: number; kills: number; cardsUsed: number; costUsed: number;
  queueOrder: number; xpGained?: number;
}
export interface GameState {
  phase: Phase; roomId: string; map: TileMap; players: Player[]; selfId: number;
  owners: (number | null)[][]; remainingMs: number; readyMs: number;
  queueMs: number; receivedAt: number; winnerId?: number;
}
export const CARDS = [
  { id: 'move', name: '잉크 스텝', type: '이동', cost: 2, icon: '↗', description: '인접한 타일로 이동' },
  { id: 'attack', name: '컬러 버스트', type: '공격', cost: 4, icon: '✳', description: '주변 타일에 잉크 분사' },
  { id: 'defend', name: '페인트 가드', type: '방어', cost: 3, icon: '⬡', description: '자신의 타일 방어' },
  { id: 'dash', name: '롱 스트로크', type: '이동', cost: 3, icon: '⇢', description: '두 타일 잉크 이동' },
  { id: 'splash', name: '스플래시', type: '공격', cost: 5, icon: '✦', description: '넓은 범위 잉크 분사' },
  { id: 'shield', name: '잉크 실드', type: '방어', cost: 2, icon: '◇', description: '자신의 타일 방어' },
] as const;
export const DEFAULT_DECK = CARDS.map(card => card.id);
export function formatTime(ms: number) {
  const seconds = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`;
}
export function rankPlayers(players: Player[]): Player[] {
  return [...players].sort((a, b) => b.tiles - a.tiles || b.painted - a.painted || b.kills - a.kills
    || a.cardsUsed - b.cardsUsed || a.costUsed - b.costUsed || a.queueOrder - b.queueOrder);
}
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
export function parseMap(value: unknown): TileMap {
  if (!record(value) || value.version !== 1 || typeof value.id !== 'string' || !value.id
    || typeof value.name !== 'string' || !value.name || value.name.length > 100
    || !Number.isInteger(value.width) || !Number.isInteger(value.height)
    || Number(value.width) < 2 || Number(value.width) > 100 || Number(value.height) < 2 || Number(value.height) > 100
    || !record(value.legend) || !Array.isArray(value.tiles)) throw new Error('지원하지 않는 맵 형식입니다.');
  const width = Number(value.width), height = Number(value.height);
  const legend = value.legend;
  for (const entry of Object.values(legend)) {
    if (!record(entry) || typeof entry.name !== 'string' || typeof entry.color !== 'string'
      || !/^#[0-9a-f]{6}$/i.test(entry.color) || typeof entry.walkable !== 'boolean') throw new Error('타일 정의가 올바르지 않습니다.');
  }
  if (value.tiles.length !== height || value.tiles.some(row => !Array.isArray(row) || row.length !== width
    || row.some(id => !Number.isInteger(id) || !Object.hasOwn(legend, String(id))))) throw new Error('맵 크기 또는 타일 ID를 확인해 주세요.');
  const map = value as unknown as TileMap;
  for (const [x, y] of [[0, 0], [width - 1, 0], [0, height - 1], [width - 1, height - 1]]) {
    if (!map.legend[String(map.tiles[y][x])].walkable) throw new Error('네 꼭짓점은 스폰 가능한 타일이어야 합니다.');
  }
  return map;
}
export function canUseCard(state: GameState, player: Player, cardId: string) {
  const card = CARDS.find(card => card.id === cardId);
  return (state.phase === 'playing' || state.phase === 'deathmatch') && player.alive && player.connected
    && !!card && player.hand.includes(cardId) && player.cost >= card.cost;
}

/** Boundary adapter for the server's snake_case GameRoom/PlayerState data. */
export function parseSnapshot(value: unknown, now = Date.now()): GameState {
  if (!record(value)) throw new Error('잘못된 룸 데이터입니다.');
  const map = parseMap(value.map);
  const phases: Record<string, Phase> = { READY: 'ready', PLAYING: 'playing', DEATHMATCH: 'deathmatch', FINISHED: 'finished' };
  const phase = Object.hasOwn(phases, String(value.status)) ? phases[String(value.status)] : undefined;
  if (!phase || typeof value.room_id !== 'string' || !record(value.players) || !Array.isArray(value.player_order)) throw new Error('룸 상태가 올바르지 않습니다.');
  const number = (v: unknown, name: string, max = Number.MAX_SAFE_INTEGER) => {
    if (typeof v !== 'number' || !Number.isFinite(v) || v < 0 || v > max) throw new Error(`${name} 값이 올바르지 않습니다.`);
    return v;
  };
  const position = (v: unknown): Point => {
    if (!Array.isArray(v) || v.length !== 2 || !v.every(Number.isInteger)) throw new Error('플레이어 좌표가 올바르지 않습니다.');
    return { x: number(v[0], 'x', map.width - 1), y: number(v[1], 'y', map.height - 1) };
  };
  const strings = (v: unknown): string[] => {
    if (!Array.isArray(v) || v.length > 50 || v.some(item => typeof item !== 'string' || item.length > 100)) throw new Error('덱 데이터가 올바르지 않습니다.');
    return v;
  };
  if (value.player_order.length < 2 || value.player_order.length > 4 || !value.player_order.every(id => Number.isSafeInteger(id) && id > 0)
    || new Set(value.player_order).size !== value.player_order.length) throw new Error('인원 또는 플레이어 순서가 올바르지 않습니다.');
  const playerRecord = value.players;
  const counts = value.tile_counts;
  if (!record(counts)) throw new Error('타일 집계가 없습니다.');
  const players = value.player_order.map((id, queueOrder): Player => {
    const p = playerRecord[String(id)];
    if (!record(p) || p.user_id !== id || !Object.hasOwn(COLORS, String(p.color))
      || typeof p.alive !== 'boolean' || typeof p.connected !== 'boolean' || typeof p.character_id !== 'string') throw new Error('플레이어 정보가 올바르지 않습니다.');
    return {
      id, name: typeof p.username === 'string' ? p.username.slice(0, 40) : `플레이어 ${id}`,
      level: number(p.level ?? 1, '레벨'), character: p.character_id, color: p.color as Color,
      position: position(p.position), spawn: position(p.spawn), alive: p.alive, connected: p.connected,
      respawnMs: number(p.respawn_remaining_ms ?? 0, '리스폰 시간'), deck: strings(p.deck), hand: strings(p.hand ?? []),
      cost: number(p.cost, '코스트', 10), tiles: number(counts[String(id)] ?? 0, '타일', map.width * map.height),
      painted: number(p.total_painted, '누적 도색'), kills: number(p.kills, '처치'),
      cardsUsed: number(p.cards_used, '사용 카드'), costUsed: number(p.cost_used, '사용 코스트'), queueOrder,
      xpGained: p.xp_gained === undefined ? undefined : number(p.xp_gained, '경험치'),
    };
  });
  const ids = new Set(players.map(p => p.id));
  if (!ids.has(Number(value.self_id)) || !Number.isSafeInteger(value.self_id)) throw new Error('내 플레이어 정보를 찾을 수 없습니다.');
  if (new Set(players.map(p => p.color)).size !== players.length) throw new Error('플레이어 색상이 중복됩니다.');
  const owners = value.tile_owners;
  if (!Array.isArray(owners) || owners.length !== map.height || owners.some(row => !Array.isArray(row) || row.length !== map.width
    || row.some(id => id !== null && !ids.has(id)))) throw new Error('타일 소유 정보가 올바르지 않습니다.');
  const winnerId = value.winner_id === undefined || value.winner_id === null ? undefined : Number(value.winner_id);
  if (phase === 'finished' && (winnerId === undefined || !ids.has(winnerId))) throw new Error('우승자 정보가 없습니다.');
  return { phase, roomId: value.room_id, map, players, selfId: Number(value.self_id), owners,
    remainingMs: number(value.remaining_ms, '남은 시간', 300000), readyMs: number(value.ready_remaining_ms ?? 0, '준비 시간', 10000),
    queueMs: 0, receivedAt: now, winnerId };
}
