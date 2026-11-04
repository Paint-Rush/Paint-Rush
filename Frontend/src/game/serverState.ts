import { COLORS, getCard, parseMap, type GameState, type Phase, type Player, type Point } from './model';

export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('서버 메시지 형식이 올바르지 않습니다.');
  return value as Record<string, unknown>;
}
export function integer(value: unknown, name: string, max = Number.MAX_SAFE_INTEGER, min = 0) {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < min || value > max) throw new Error(`${name} 값이 올바르지 않습니다.`);
  return value;
}
export function parseHand(value: unknown): string[] {
  if (!Array.isArray(value) || value.length !== 3 || value.some(id => typeof id !== 'string' || !id || id.length > 80)) throw new Error('서버 손패는 3장이어야 합니다.');
  if (value.some(id => !getCard(id))) throw new Error('서버 손패가 제공된 여섯 카드와 다릅니다. 카드 등록을 확인해 주세요.');
  return [...value];
}
export function parsePhase(value: unknown): Phase {
  switch (value) {
    case 'READY': return 'ready';
    case 'PLAYING': return 'playing';
    case 'DEATHMATCH': return 'deathmatch';
    case 'FINISHED': return 'finished';
    default: throw new Error('알 수 없는 게임 단계입니다.');
  }
}

/** Adapt the actual room_state payload. Private fields of other players stay undisclosed. */
export function readRoomState(raw: unknown, selfId: number, previous: GameState | null, now = Date.now(), cardCosts?: Record<string, number>): GameState {
  const data = object(raw);
  if (data.type !== 'room_state' || typeof data.room_id !== 'string' || !data.room_id || typeof data.map_id !== 'string') throw new Error('룸 정보를 확인할 수 없습니다.');
  const map = parseMap({ ...object(data.map), map_id: data.map_id });
  if (map.width !== 20 || map.height !== 20) throw new Error('서버 맵은 20×20이어야 합니다.');
  const phase = parsePhase(data.status);
  if (!Array.isArray(data.players) || data.players.length < 2 || data.players.length > 4) throw new Error('참가자 수가 올바르지 않습니다.');
  const maxCost = integer(data.max_cost, '최대 코스트', 10, 10);
  const selfCost = integer(data.my_cost, '내 코스트', maxCost);
  const hand = parseHand(data.my_hand);
  const nextCard = data.my_next_card;
  if (nextCard !== null && (typeof nextCard !== 'string' || !nextCard || nextCard.length > 80)) throw new Error('다음 카드 정보가 올바르지 않습니다.');
  if (typeof nextCard === 'string' && !getCard(nextCard)) throw new Error('서버의 다음 카드가 제공된 여섯 카드와 다릅니다.');
  const position = (rawPosition: unknown): Point => {
    if (!Array.isArray(rawPosition) || rawPosition.length !== 2) throw new Error('좌표 형식이 올바르지 않습니다.');
    return { x: integer(rawPosition[0], 'x 좌표', 19), y: integer(rawPosition[1], 'y 좌표', 19) };
  };
  const sameRoom = previous?.roomId === data.room_id;
  const players = data.players.map((item, queueOrder): Player => {
    const p = object(item), id = integer(p.user_id, '계정 ID', Number.MAX_SAFE_INTEGER, 1);
    if (typeof p.color !== 'string' || !Object.hasOwn(COLORS, p.color) || typeof p.character_id !== 'string'
      || typeof p.alive !== 'boolean' || typeof p.connected !== 'boolean') throw new Error('플레이어 데이터가 올바르지 않습니다.');
    const spawn = position(p.spawn);
    if (![0, 19].includes(spawn.x) || ![0, 19].includes(spawn.y)) throw new Error('스폰은 꼭짓점이어야 합니다.');
    const old = sameRoom ? previous.players.find(player => player.id === id) : undefined;
    return {
      id, name: typeof p.username === 'string' ? p.username.slice(0, 40) : `플레이어 ${id}`,
      level: typeof p.level === 'number' ? integer(p.level, '레벨') : 1, character: p.character_id, color: p.color as Player['color'],
      spawn, position: position(p.position), alive: p.alive, connected: p.connected,
      respawnMs: p.respawn_remaining_seconds === undefined ? 0 : integer(p.respawn_remaining_seconds, '리스폰 시간', 10) * 1000,
      // The live server does not disclose decks or opponents' hands/costs. Do not invent a full deck.
      deck: [], hand: id === selfId ? hand : [], cost: id === selfId ? selfCost : 0,
      tiles: integer(p.tile_count, '영역 수', 400), painted: old?.painted ?? 0, kills: old?.kills ?? 0,
      cardsUsed: old?.cardsUsed ?? 0, costUsed: old?.costUsed ?? 0, queueOrder, rank: old?.rank, deaths: old?.deaths,
    };
  });
  const ids = new Set(players.map(p => p.id));
  if (ids.size !== players.length || !ids.has(selfId) || new Set(players.map(p => p.color)).size !== players.length
    || new Set(players.map(p => `${p.spawn.x},${p.spawn.y}`)).size !== players.length) throw new Error('플레이어 ID·색상·스폰이 올바르지 않습니다.');
  if (!Array.isArray(data.tile_owners) || data.tile_owners.length !== 20) throw new Error('타일 소유 정보가 올바르지 않습니다.');
  const owners = data.tile_owners.map(row => {
    if (!Array.isArray(row) || row.length !== 20 || row.some(id => id !== null && !ids.has(id))) throw new Error('타일 소유 ID가 올바르지 않습니다.');
    return [...row] as (number | null)[];
  });
  // Cross-check aggregate counts against the tile matrix before accepting a complete snapshot.
  for (const player of players) if (owners.flat().filter(id => id === player.id).length !== player.tiles) throw new Error('영역 수와 타일 소유 데이터가 일치하지 않습니다. 재동기화합니다.');
  return {
    phase, roomId: data.room_id, map, players, selfId, owners, maxCost, nextCard: nextCard as string | null,
    remainingMs: integer(data.remaining_seconds, '남은 시간', 300) * 1000,
    readyMs: integer(data.ready_remaining_seconds, '준비 시간', 10) * 1000,
    queueMs: 0, receivedAt: now, authoritative: true, inputReady: true,
    winnerId: sameRoom ? previous.winnerId : undefined,
    cardCosts: cardCosts ?? previous?.cardCosts,
  };
}
