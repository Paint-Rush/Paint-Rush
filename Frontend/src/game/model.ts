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
  queueOrder: number; xpGained?: number; rank?: number; deaths?: number;
  hp?: number | null; maxHp?: number | null; knockedOut?: boolean; respawnAt?: number | null;
}
export interface GameState {
  phase: Phase; roomId: string; map: TileMap; players: Player[]; selfId: number;
  owners: (number | null)[][]; remainingMs: number; readyMs: number;
  queueMs: number; receivedAt: number; winnerId?: number;
  nextCard?: string | null; maxCost?: number; authoritative?: boolean; inputReady?: boolean;
  /** Local preview draw queue. Never read from or applied to a live server game. */
  previewQueue?: string[];
  cardCosts?: Record<string, number>;
  previewAction?: { id: number; cardId: string; tiles: Point[]; from: Point; to: Point };
}
export function formatTime(ms: number) {
  const seconds = Math.max(0, Math.ceil(ms / 1000));
  return `${Math.floor(seconds / 60).toString().padStart(2, '0')}:${(seconds % 60).toString().padStart(2, '0')}`;
}
function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
export function parseMap(value: unknown): TileMap {
  if (record(value) && Array.isArray(value.tiles) && !('version' in value)) {
    const ids: Record<string, number> = { NORMAL: 0, GLASS: 1, BLOCKED: 2 };
    const tiles = value.tiles.map(row => {
      if (!Array.isArray(row)) throw new Error('맵 행이 올바르지 않습니다.');
      return row.map(tile => {
        if (typeof tile !== 'string' || !Object.hasOwn(ids, tile)) throw new Error('지원하지 않는 타일입니다.');
        return ids[tile];
      });
    });
    return parseMap({ version: 1, id: value.map_id ?? 'server-map', name: value.name ?? String(value.map_id ?? '서버 맵'),
      width: value.width, height: value.height, tiles, legend: {
        '0': { name: 'NORMAL', color: '#ffffff', walkable: true },
        '1': { name: 'GLASS', color: '#e9f7fb', walkable: true },
        '2': { name: 'BLOCKED', color: '#68717a', walkable: false },
      } });
  }
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
