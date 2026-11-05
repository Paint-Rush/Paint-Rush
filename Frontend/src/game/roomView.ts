import type { RoomSnapshot, RoomStatus } from '../types/game';
import { COLORS, parseMap, type Color, type GameState, type Phase, type Player, type Point, type TileMap } from './model';

const PHASES: Record<RoomStatus, Phase> = { READY: 'ready', PLAYING: 'playing', DEATHMATCH: 'deathmatch', FINISHED: 'finished' };
const DIRECTIONS = [[0, -1], [1, 0], [0, 1], [-1, 0]];

export const MOVE_STEPS = 1;

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
      respawnMs: 0,
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
    authoritative: true,
    inputReady: true,
  };
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
