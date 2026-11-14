import type { RoomSnapshot, RoomStatus } from '../types/game';
import { COLORS, parseMap, type Color, type GameState, type Phase, type Player, type Point, type TileMap } from './model';

const PHASES: Record<RoomStatus, Phase> = { READY: 'ready', PLAYING: 'playing', DEATHMATCH: 'deathmatch', FINISHED: 'finished' };
const DIRECTIONS = [[0, -1], [1, 0], [0, 1], [-1, 0]];

export const MOVE_STEPS = 1;
export const ATTACK_RANGE: Record<string, number> = { attack_slash: 1, attack_bomb: 3 };
export const COLOR_BURST = 'ultimate_01';
export const COLOR_STEAL = 'ultimate_02';
export const STEAL_RANGE = 3;

export function toGameState(room: RoomSnapshot, selfId: number, now = Date.now()): GameState {
  const map = parseMap({ map_id: room.mapId, name: room.mapId, width: room.map.width, height: room.map.height, tiles: room.map.tiles });
  const players = room.players.map((player, queueOrder): Player => {
    if (!Object.hasOwn(COLORS, player.color)) throw new Error('플레이어 색상이 올바르지 않습니다.');
    const isSelf = player.userId === selfId;
    return {
      id: player.userId,
      name: player.characterName ?? `플레이어 ${player.userId}`,
      level: 1,
      character: player.characterId,
      color: player.color as Color,
      position: { x: player.position[0], y: player.position[1] },
      spawn: { x: player.spawn[0], y: player.spawn[1] },
      alive: player.alive,
      connected: player.connected,
      respawnMs: respawnMs(player.respawnAt, now),
      hp: player.hp,
      maxHp: player.maxHp,
      knockedOut: player.knockedOut,
      respawnAt: player.respawnAt,
      ultimateId: player.ultimateId,
      ultimateName: player.ultimateName,
      deck: [],
      hand: isSelf ? [...room.myHand] : [],
      cost: isSelf ? room.myCost : 0,
      tiles: player.tileCount,
      painted: 0,
      kills: 0,
      cardsUsed: 0,
      costUsed: 0,
      queueOrder,
    };
  });
  if (!players.some((player) => player.id === selfId)) throw new Error('내 플레이어 정보를 찾을 수 없습니다.');
  return {
    phase: PHASES[room.status],
    roomId: room.roomId,
    map,
    players,
    selfId,
    owners: room.tileOwners,
    remainingMs: room.remainingSeconds * 1000,
    readyMs: room.readyRemainingSeconds * 1000,
    queueMs: 0,
    receivedAt: now,
    nextCard: room.myNextCard,
    maxCost: room.maxCost,
    ultimateUsed: room.myUltimateUsed,
    authoritative: true,
    inputReady: true,
  };
}

export function respawnMs(respawnAt: number | null | undefined, now: number): number {
  return respawnAt === null || respawnAt === undefined ? 0 : Math.max(0, respawnAt - now);
}

export function hearts(hp: number | null | undefined, maxHp: number | null | undefined): string | null {
  if (hp === null || hp === undefined || maxHp === null || maxHp === undefined) return null;
  const filled = Math.max(0, Math.min(hp, maxHp));
  return Array.from({ length: maxHp }, (_, index) => index < filled ? '♥' : '♡').join(' ');
}

export function moveTargets(map: TileMap, origin: Point, steps = MOVE_STEPS): Point[] {
  const walkable = ({ x, y }: Point) => x >= 0 && y >= 0 && x < map.width && y < map.height && map.legend[String(map.tiles[y][x])].walkable;
  const seen = new Set([`${origin.x},${origin.y}`]);
  const targets: Point[] = [];
  let frontier = [origin];
  for (let step = 0; step < steps; step++) {
    const next: Point[] = [];
    for (const point of frontier) for (const [dx, dy] of DIRECTIONS) {
      const target = { x: point.x + dx, y: point.y + dy };
      const key = `${target.x},${target.y}`;
      if (seen.has(key) || !walkable(target)) continue;
      seen.add(key);
      targets.push(target);
      next.push(target);
    }
    frontier = next;
  }
  return targets;
}

export function attackTargets(map: TileMap, origin: Point, cardId: string): Point[] {
  const range = ATTACK_RANGE[cardId];
  if (range === undefined) return [];
  const targets: Point[] = [];
  for (let y = Math.max(0, origin.y - range); y <= Math.min(map.height - 1, origin.y + range); y++) {
    for (let x = Math.max(0, origin.x - range); x <= Math.min(map.width - 1, origin.x + range); x++) {
      const distance = Math.abs(x - origin.x) + Math.abs(y - origin.y);
      if (cardId === 'attack_slash' ? distance === 1 && map.legend[String(map.tiles[y][x])].name === 'NORMAL' : distance <= range) targets.push({ x, y });
    }
  }
  return targets;
}

export function selectCard(active: { index: number; id: string } | null, index: number, id: string) {
  return active?.index === index && active.id === id ? null : { index, id };
}

export function slashBlocked(map: TileMap, origin: Point): Point[] {
  return DIRECTIONS.map(([dx, dy]) => ({ x: origin.x + dx, y: origin.y + dy }))
    .filter(({ x, y }) => x >= 0 && y >= 0 && x < map.width && y < map.height && map.legend[String(map.tiles[y][x])].name !== 'NORMAL');
}

export function bombArea(map: TileMap, center: Point): Point[] {
  const area: Point[] = [];
  for (let y = center.y - 1; y <= center.y + 1; y++) {
    for (let x = center.x - 1; x <= center.x + 1; x++) if (x >= 0 && y >= 0 && x < map.width && y < map.height) area.push({ x, y });
  }
  return area;
}

export function bombHits(map: TileMap, owners: (number | null)[][], area: Point[], selfId: number): Point[] {
  return area.filter(({ x, y }) => map.legend[String(map.tiles[y][x])].name === 'NORMAL' && owners[y][x] !== null && owners[y][x] !== selfId);
}

export function isUltimate(id: string | null | undefined): boolean {
  return id === COLOR_BURST || id === COLOR_STEAL;
}

export function ultimateTargets(map: TileMap, origin: Point, ultimateId: string | null | undefined): Point[] {
  if (ultimateId === COLOR_BURST) return [origin];
  if (ultimateId !== COLOR_STEAL) return [];
  const targets: Point[] = [];
  for (let y = Math.max(0, origin.y - STEAL_RANGE); y <= Math.min(map.height - 1, origin.y + STEAL_RANGE); y++) {
    for (let x = Math.max(0, origin.x - STEAL_RANGE); x <= Math.min(map.width - 1, origin.x + STEAL_RANGE); x++) {
      if (Math.abs(x - origin.x) + Math.abs(y - origin.y) <= STEAL_RANGE) targets.push({ x, y });
    }
  }
  return targets;
}

export function ultimateCenter(map: TileMap, origin: Point, ultimateId: string | null | undefined, point: Point | null): Point | null {
  if (ultimateId === COLOR_BURST) return origin;
  if (!point) return null;
  return ultimateTargets(map, origin, ultimateId).some(tile => tile.x === point.x && tile.y === point.y) ? point : null;
}

export function ultimateChanges(map: TileMap, owners: (number | null)[][], area: Point[], selfId: number, ultimateId: string | null | undefined): Point[] {
  if (ultimateId === COLOR_STEAL) return bombHits(map, owners, area, selfId);
  if (ultimateId !== COLOR_BURST) return [];
  return area.filter(({ x, y }) => map.legend[String(map.tiles[y][x])].name === 'NORMAL' && owners[y][x] !== selfId);
}

export function unpaintable(map: TileMap, area: Point[]): Point[] {
  return area.filter(({ x, y }) => map.legend[String(map.tiles[y][x])].name !== 'NORMAL');
}

export type Selection = { index: number; id: string } | 'ultimate' | null;

export function nextSelection(current: Selection, input: { index: number; id: string } | 'ultimate', ultimateReady: boolean): Selection {
  if (input === 'ultimate') return current === 'ultimate' ? null : ultimateReady ? 'ultimate' : current;
  return selectCard(current === 'ultimate' ? null : current, input.index, input.id);
}

export type GameShortcut = { type: 'select'; index: number } | { type: 'use' } | { type: 'recenter' } | { type: 'ultimate' } | null;

export function gameShortcut(event: { key: string; code: string; repeat: boolean; ctrlKey?: boolean; altKey?: boolean; metaKey?: boolean }, editable: boolean): GameShortcut {
  if (editable || event.repeat || event.ctrlKey || event.altKey || event.metaKey) return null;
  if (event.code === 'Space') return { type: 'use' };
  if (event.code === 'KeyR') return { type: 'recenter' };
  if (event.code === 'KeyQ') return { type: 'ultimate' };
  const index = ['1', '2', '3'].indexOf(event.key);
  return index >= 0 ? { type: 'select', index } : null;
}
