import { getCard, type Point, type TileMap } from './model';
import { patternTiles, type Direction } from './patterns';

export function needsCardTarget(id: string) { return getCard(id)?.type === '이동' || id === 'the_line'; }

function inMap(map: TileMap, point: Point) {
  return Number.isInteger(point.x) && Number.isInteger(point.y) && point.x >= 0 && point.y >= 0 && point.x < map.width && point.y < map.height;
}
function walkable(map: TileMap, point: Point) { return inMap(map, point) && map.legend[String(map.tiles[point.y][point.x])].walkable; }

/** Fixed player-chosen card rules transcribed from the references; no NPC decisions or pathfinding. */
export function resolveCardAction(id: string, origin: Point, map: TileMap, target?: Point): { destination: Point; tiles: Point[] } {
  const card = getCard(id);
  if (!card) throw new Error('제공된 여섯 장 중에서 선택해 주세요.');
  if (!inMap(map, origin)) throw new Error('플레이어 위치를 확인해 주세요.');
  if (id === 'criss_cross' || id === 'swastika') return { destination: { ...origin }, tiles: [...patternTiles({ pattern: id, origin, map, direction: 'left' })] };
  if (!target || !inMap(map, target) || (origin.x === target.x && origin.y === target.y)) throw new Error('맵의 표시된 목적지 타일을 클릭하세요.');
  const dx = target.x - origin.x, dy = target.y - origin.y;
  const length = Math.max(Math.abs(dx), Math.abs(dy));
  if (id === 'the_line') {
    if (dx !== 0 && dy !== 0) throw new Error('THE LINE은 상하좌우 방향을 선택하세요.');
    if (length > 4) throw new Error('THE LINE은 상하좌우 4칸 안에서 방향을 선택하세요.');
    const direction: Direction = dx === 0 ? dy < 0 ? 'up' : 'down' : dx < 0 ? 'left' : 'right';
    return { destination: { ...origin }, tiles: [...patternTiles({ pattern: 'the_line', origin, map, direction })] };
  }
  if (!walkable(map, target)) throw new Error('장애물 타일로는 이동할 수 없습니다.');
  if (id === 'jumper') {
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) throw new Error('JUMPER는 자신을 중심으로 5×5 범위 안에서 선택하세요.');
    // Project the short jump onto the grid. Walls do not block a jump;
    // paint() still omits blocked/glass cells along the projected route.
    const tiles = Array.from({ length }, (_, i) => ({ x: Math.round(origin.x + dx * (i + 1) / length), y: Math.round(origin.y + dy * (i + 1) / length) }));
    return { destination: { ...target }, tiles };
  }
  if (id === 'dash' && dx !== 0 && dy !== 0) throw new Error('DASH는 상하좌우 직선 위의 타일을 선택하세요.');
  if (id === 'dash' && length !== 4) throw new Error('DASH는 상하좌우 정확히 4칸만 이동합니다.');
  if (id === 'sliding' && Math.abs(dx) !== Math.abs(dy)) throw new Error('SLIDING은 대각선 위의 타일을 선택하세요.');
  if (id === 'sliding' && length > 5) throw new Error('SLIDING은 대각선으로 최대 5칸까지 이동합니다.');
  const tiles: Point[] = [];
  for (let step = 1; step <= length; step++) {
    const point = { x: origin.x + Math.sign(dx) * step, y: origin.y + Math.sign(dy) * step };
    if (!walkable(map, point)) throw new Error('이동 경로에 장애물이 있습니다.');
    tiles.push(point);
  }
  return { destination: { ...target }, tiles };
}

export function cardTargetTiles(id: string, origin: Point, map: TileMap): Point[] {
  if (!needsCardTarget(id)) return [];
  const candidates: Point[] = [];
  if (id === 'jumper') {
    for (let dy = -2; dy <= 2; dy++) for (let dx = -2; dx <= 2; dx++) candidates.push({ x: origin.x + dx, y: origin.y + dy });
  } else {
    const directions = id === 'sliding' ? [[-1, -1], [-1, 1], [1, -1], [1, 1]] : [[-1, 0], [1, 0], [0, -1], [0, 1]];
    for (const [dx, dy] of directions) for (let step = id === 'dash' ? 4 : 1; step <= (id === 'sliding' ? 5 : 4); step++) {
      candidates.push({ x: origin.x + dx * step, y: origin.y + dy * step });
    }
  }
  const targets: Point[] = [];
  for (const target of candidates) {
    if (!inMap(map, target)) continue;
    try { if (resolveCardAction(id, origin, map, target).tiles.length) targets.push(target); } catch { /* Outside this card's legal destinations. */ }
  }
  return targets;
}
