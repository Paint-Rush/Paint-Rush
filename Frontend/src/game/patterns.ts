import type { Point, TileMap } from './model';

export type Pattern = 'criss_cross' | 'swastika' | 'the_line';
export type Direction = 'up' | 'down' | 'left' | 'right';
export const PATTERNS: { id: Pattern; name: string; description: string }[] = [
  { id: 'criss_cross', name: 'CRISS CROSS', description: '중심을 기준으로 X자 범위' },
  { id: 'swastika', name: 'SWASTIKA', description: '중심을 기준으로 만자 모양 범위' },
  { id: 'the_line', name: 'THE LINE', description: '선택한 동서남북 방향으로 앞의 4칸' },
];

export interface PreviewRequest { pattern: Pattern; origin: Point; map: TileMap; direction: Direction }
// Fixed shapes transcribed from the storyboards, not NPC decision/pathfinding logic.
const CROSS: readonly Point[] = [
  { x: -2, y: -2 }, { x: -1, y: -1 }, { x: 0, y: 0 }, { x: 1, y: 1 }, { x: 2, y: 2 },
  { x: 2, y: -2 }, { x: 1, y: -1 }, { x: -1, y: 1 }, { x: -2, y: 2 },
];
const WIND: readonly Point[] = [
  { x: 0, y: 0 }, { x: 0, y: -1 }, { x: 0, y: -2 }, { x: 1, y: -2 }, { x: 2, y: -2 },
  { x: 1, y: 0 }, { x: 2, y: 0 }, { x: 2, y: 1 }, { x: 2, y: 2 },
  { x: 0, y: 1 }, { x: 0, y: 2 }, { x: -1, y: 2 }, { x: -2, y: 2 },
  { x: -1, y: 0 }, { x: -2, y: 0 }, { x: -2, y: -1 }, { x: -2, y: -2 },
];
const LINES: Record<Direction, readonly Point[]> = {
  left: [{ x: -1, y: 0 }, { x: -2, y: 0 }, { x: -3, y: 0 }, { x: -4, y: 0 }],
  right: [{ x: 1, y: 0 }, { x: 2, y: 0 }, { x: 3, y: 0 }, { x: 4, y: 0 }],
  up: [{ x: 0, y: -1 }, { x: 0, y: -2 }, { x: 0, y: -3 }, { x: 0, y: -4 }],
  down: [{ x: 0, y: 1 }, { x: 0, y: 2 }, { x: 0, y: 3 }, { x: 0, y: 4 }],
};

export function patternTiles({ pattern, origin, map, direction }: PreviewRequest): readonly Point[] {
  const offsets = pattern === 'criss_cross' ? CROSS : pattern === 'swastika' ? WIND : LINES[direction];
  return offsets.map(point => ({ x: origin.x + point.x, y: origin.y + point.y })).filter(({ x, y }) => {
    if (x < 0 || y < 0 || x >= map.width || y >= map.height) return false;
    const tile = map.legend[String(map.tiles[y][x])];
    return tile.walkable && tile.name !== 'GLASS';
  });
}
