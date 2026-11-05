import type { MatchPhase, MatchPlayer, MatchSession, Point, RoomPlayer, RoomSnapshot, RoomStatus, ServerMessage } from '../types/game';

export interface SocketLike {
  readyState: number;
  send(data: string): void;
  close(code?: number, reason?: string): void;
  onopen: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  onclose: ((event: { code: number; reason: string }) => void) | null;
}

const OPEN = 1;
const CONNECT_TIMEOUT = 10000;
const ACTIVE_PHASES: MatchPhase[] = ['connecting', 'authenticating', 'joining', 'waiting', 'cancelling', 'matched', 'ready', 'started'];
const ROOM_PHASES: MatchPhase[] = ['matched', 'ready', 'started', 'finished'];
const STATUSES: RoomStatus[] = ['READY', 'PLAYING', 'DEATHMATCH', 'FINISHED'];
const CRITICAL_EVENTS = ['auth_success', 'match_found', 'room_state'];
const QUEUE_ERRORS = ['unknown_character', 'profile_unavailable', 'deck_unavailable', 'invalid_deck'];

class ProtocolError extends Error {}

function fail(): never {
  throw new ProtocolError('서버 응답 형식이 올바르지 않습니다.');
}

function text(value: unknown): string {
  return typeof value === 'string' && value.length > 0 ? value : fail();
}

function optionalText(value: unknown): string | null {
  return value === null ? null : text(value);
}

function integer(value: unknown, min = 0): number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= min ? value : fail();
}

function flag(value: unknown): boolean {
  return typeof value === 'boolean' ? value : fail();
}

function point(value: unknown): Point {
  return Array.isArray(value) && value.length === 2 ? [integer(value[0]), integer(value[1])] : fail();
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : fail();
}

function status(value: unknown): RoomStatus {
  return STATUSES.includes(value as RoomStatus) ? value as RoomStatus : fail();
}

function matchPlayer(value: unknown): MatchPlayer {
  const player = record(value);
  return {
    userId: integer(player.user_id, 1),
    color: text(player.color),
    spawn: point(player.spawn),
    characterId: text(player.character_id),
    characterName: optionalText(player.character_name),
    ultimateId: optionalText(player.ultimate_id),
    ultimateName: optionalText(player.ultimate_name),
  };
}

function playerList<T extends MatchPlayer>(value: unknown, read: (item: unknown) => T): T[] {
  if (!Array.isArray(value) || value.length === 0) fail();
  const players = value.map(read);
  if (new Set(players.map((player) => player.userId)).size !== players.length) fail();
  return players;
}

function roomPlayer(value: unknown): RoomPlayer {
  const player = record(value);
  return {
    ...matchPlayer(player),
    position: point(player.position),
    alive: flag(player.alive),
    connected: flag(player.connected),
    tileCount: integer(player.tile_count),
  };
}

export function readRoomState(message: ServerMessage): RoomSnapshot {
  const map = record(message.map);
  const width = integer(map.width, 1);
  const height = integer(map.height, 1);
  if (!Array.isArray(map.tiles) || map.tiles.length !== height || map.tiles.some((row) => !Array.isArray(row) || row.length !== width)) fail();
  const players = playerList(message.players, roomPlayer);
  const ids = new Set(players.map((player) => player.userId));
  const owners = message.tile_owners;
  if (!Array.isArray(owners) || owners.length !== height
    || owners.some((row) => !Array.isArray(row) || row.length !== width || row.some((owner) => owner !== null && !ids.has(owner as number)))) fail();
  const hand = message.my_hand;
  if (!Array.isArray(hand)) fail();
  return {
    roomId: text(message.room_id),
    status: status(message.status),
    mapId: text(message.map_id),
    map: { width, height, tiles: (map.tiles as unknown[][]).map((row) => [...row]) },
    players,
    myCost: integer(message.my_cost),
    myHand: hand.map(text),
    myNextCard: optionalText(message.my_next_card),
    myUltimateUsed: flag(message.my_ultimate_used),
    tileOwners: (owners as (number | null)[][]).map((row) => [...row]),
    startedAt: optionalText(message.started_at),
    readyRemainingSeconds: integer(message.ready_remaining_seconds),
    remainingSeconds: integer(message.remaining_seconds),
    maxCost: integer(message.max_cost, 1),
  };
}

function phaseOf(roomStatus: RoomStatus): MatchPhase {
  if (roomStatus === 'READY') return 'ready';
  if (roomStatus === 'FINISHED') return 'finished';
  return 'started';
}

export function initialSession(): MatchSession {
  return {
    phase: 'idle',
    connected: false,
    userId: null,
    characterId: null,
    queue: null,
    roomId: null,
    mapId: null,
    players: [],
    readySeconds: null,
    remainingSeconds: null,
    durationSeconds: null,
    syncedAt: null,
    room: null,
    result: null,
    error: null,
    warning: null,
  };
}

export function gameSocketUrl(location: { protocol: string; host: string }): string {
  return `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws/game`;
}

export function readySecondsLeft(session: MatchSession, now: number): number | null {
  if (session.readySeconds === null || session.syncedAt === null) return null;
  return Math.max(0, session.readySeconds - Math.floor((now - session.syncedAt) / 1000));
}

export class GameSocket {
  private state = initialSession();
  private socket: SocketLike | null = null;
  private socketToken: string | null = null;
  private token: string | null = null;
  private wantsQueue = false;
  private resuming = false;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private listeners = new Set<() => void>();
  private eventListeners = new Set<(message: ServerMessage) => void>();

  private readonly createSocket: (url: string) => SocketLike;
  private readonly url: string;
  private readonly now: () => number;

  constructor(createSocket: (url: string) => SocketLike, url: string, now: () => number = Date.now) {
    this.createSocket = createSocket;
    this.url = url;
    this.now = now;
  }

  getState = (): MatchSession => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  onEvent(listener: (message: ServerMessage) => void): () => void {
    this.eventListeners.add(listener);
    return () => { this.eventListeners.delete(listener); };
  }

  start(token: string, characterId: string): boolean {
    if (this.resuming && this.socket !== null && this.token === token && characterId) {
      this.resuming = false;
      this.wantsQueue = true;
      this.update({ characterId });
      return true;
    }
    if (ACTIVE_PHASES.includes(this.state.phase) || !token || !characterId) return false;
    this.token = token;
    this.wantsQueue = true;
    this.resuming = false;
    const reusable = this.isOpen() && this.socketToken === token && this.state.userId !== null;
    this.state = { ...initialSession(), connected: reusable, userId: reusable ? this.state.userId : null, characterId };
    if (reusable) this.join();
    else this.open();
    return true;
  }

  resume(token: string): boolean {
    if (this.socket !== null || !token || this.state.phase !== 'idle') return false;
    this.token = token;
    this.wantsQueue = false;
    this.open();
    this.resuming = this.socket !== null;
    return this.resuming;
  }

  playCard(handIndex: number, target?: { x: number; y: number }): boolean {
    const { phase, room } = this.state;
    if (phase !== 'started' || !room || (room.status !== 'PLAYING' && room.status !== 'DEATHMATCH')) return false;
    if (!Number.isSafeInteger(handIndex) || handIndex < 0 || handIndex >= room.myHand.length) return false;
    return this.send(target
      ? { type: 'card_use', hand_index: handIndex, target_x: target.x, target_y: target.y }
      : { type: 'card_use', hand_index: handIndex });
  }

  sync(): boolean {
    if (this.state.roomId === null) return false;
    return this.send({ type: 'room_state' });
  }

  cancel(): boolean {
    if (this.state.phase !== 'waiting' || !this.send({ type: 'queue_cancel' })) return false;
    this.update({ phase: 'cancelling' });
    return true;
  }

  retry(): boolean {
    if (this.state.phase !== 'error' || !this.token || this.state.error?.code === 'auth_failed') return false;
    const inRoom = this.state.roomId !== null;
    this.wantsQueue = !inRoom;
    this.resuming = false;
    this.update({ error: null, warning: null });
    if (!inRoom && this.isOpen() && this.state.userId !== null) this.join();
    else this.open();
    return true;
  }

  leaveRoom(): boolean {
    if (this.state.phase !== 'finished' || !this.send({ type: 'room_leave' })) return false;
    this.update({ warning: null });
    return true;
  }

  release(): void {
    if (ACTIVE_PHASES.includes(this.state.phase)) return;
    this.wantsQueue = false;
    const keep = this.isOpen() && this.state.userId !== null;
    this.state = { ...initialSession(), connected: keep, userId: keep ? this.state.userId : null };
    this.notify();
  }

  close(): void {
    this.wantsQueue = false;
    this.resuming = false;
    this.token = null;
    this.closeSocket();
    this.state = initialSession();
    this.notify();
  }

  private isOpen(): boolean {
    return this.socket !== null && this.socket.readyState === OPEN;
  }

  private open(): void {
    this.closeSocket();
    const token = this.token;
    if (!token) return;
    this.update({ phase: 'connecting', connected: false, error: null });
    let socket: SocketLike;
    try {
      socket = this.createSocket(this.url);
    } catch {
      this.setError('connection_failed', '게임 서버에 연결할 수 없습니다.');
      return;
    }
    this.socket = socket;
    this.socketToken = token;
    this.timer = setTimeout(() => {
      if (socket !== this.socket) return;
      this.closeSocket();
      this.setError('connection_timeout', '게임 서버 응답이 없습니다. 다시 시도해 주세요.');
    }, CONNECT_TIMEOUT);
    socket.onopen = () => {
      if (socket !== this.socket) return;
      this.update({ phase: 'authenticating', connected: true });
      this.send({ type: 'auth', token });
    };
    socket.onmessage = (event) => {
      if (socket === this.socket) this.receive(event.data);
    };
    socket.onerror = () => {};
    socket.onclose = () => {
      if (socket === this.socket) this.lost();
    };
  }

  private closeSocket(): void {
    this.clearTimer();
    const socket = this.socket;
    this.socket = null;
    this.socketToken = null;
    if (!socket) return;
    socket.onopen = null;
    socket.onmessage = null;
    socket.onerror = null;
    socket.onclose = null;
    try {
      socket.close(1000, 'client closed');
    } catch {
      return;
    }
  }

  private clearTimer(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  private lost(): void {
    this.clearTimer();
    this.resuming = false;
    this.socket = null;
    this.socketToken = null;
    const { phase, error } = this.state;
    if (error || phase === 'idle' || phase === 'cancelled' || phase === 'finished') {
      this.update({ connected: false });
    } else if (phase === 'connecting') {
      this.setError('connection_failed', '게임 서버에 연결할 수 없습니다.');
    } else if (phase === 'authenticating') {
      this.setError('auth_closed', '인증이 끝나기 전에 서버 연결이 종료되었습니다.');
    } else if (ROOM_PHASES.includes(phase)) {
      this.setError('game_disconnected', '게임 서버 연결이 끊겼습니다. 다시 연결해 주세요.');
    } else {
      this.setError('queue_disconnected', '매칭 대기 중 서버 연결이 끊겼습니다. 다시 시도해 주세요.');
    }
  }

  private send(message: object): boolean {
    if (!this.isOpen()) return false;
    this.socket!.send(JSON.stringify(message));
    return true;
  }

  private join(): void {
    if (!this.state.characterId || !this.send({ type: 'queue_join', character_id: this.state.characterId })) return;
    this.update({ phase: 'joining', queue: null });
  }

  private receive(data: unknown): void {
    let message: ServerMessage;
    try {
      const parsed: unknown = typeof data === 'string' ? JSON.parse(data) : null;
      if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed) || typeof (parsed as ServerMessage).type !== 'string') throw new ProtocolError();
      message = parsed as ServerMessage;
    } catch {
      this.update({ warning: '형식이 올바르지 않은 서버 메시지를 무시했습니다.' });
      return;
    }
    try {
      if (!this.handle(message)) {
        this.update({ warning: `알 수 없는 서버 이벤트를 무시했습니다: ${message.type}` });
        return;
      }
    } catch (reason) {
      if (!(reason instanceof ProtocolError)) throw reason;
      if (CRITICAL_EVENTS.includes(message.type)) {
        this.closeSocket();
        this.setError('protocol_error', reason.message);
      } else {
        this.update({ warning: `${message.type} 메시지 형식이 올바르지 않아 무시했습니다.` });
      }
      return;
    }
    for (const listener of this.eventListeners) listener(message);
  }

  private sameRoom(message: ServerMessage): boolean {
    return message.room_id === undefined || message.room_id === this.state.roomId;
  }

  private updateRoom(change: (room: RoomSnapshot) => Partial<RoomSnapshot>): void {
    const { room } = this.state;
    if (room) this.update({ room: { ...room, ...change(room) } });
  }

  private handle(message: ServerMessage): boolean {
    const { phase } = this.state;
    switch (message.type) {
      case 'auth_success': {
        const userId = integer(message.user_id, 1);
        this.clearTimer();
        const resumed = this.resuming;
        this.resuming = false;
        this.update(resumed ? { phase: 'idle', userId, connected: true } : { userId, connected: true });
        if (resumed) return true;
        if (this.wantsQueue && this.state.roomId === null) this.join();
        else this.send({ type: 'room_state' });
        return true;
      }
      case 'room_state': {
        const room = readRoomState(message);
        this.wantsQueue = this.wantsQueue && room.status === 'FINISHED';
        this.update({
          phase: phaseOf(room.status),
          room,
          roomId: room.roomId,
          mapId: room.mapId,
          players: room.players.map(({ userId, color, spawn, characterId, characterName, ultimateId, ultimateName }) => ({ userId, color, spawn, characterId, characterName, ultimateId, ultimateName })),
          readySeconds: room.readyRemainingSeconds,
          remainingSeconds: room.remainingSeconds,
          syncedAt: this.now(),
          queue: null,
          error: null,
        });
        return true;
      }
      case 'queue_waiting':
        if (phase === 'joining') this.update({ phase: 'waiting', queue: { waiting: integer(message.waiting), matchSize: integer(message.match_size, 1) } });
        return true;
      case 'queue_cancelled':
        if (phase === 'cancelling' || phase === 'waiting') {
          this.wantsQueue = false;
          this.update({ phase: 'cancelled', queue: null });
        }
        return true;
      case 'match_found': {
        const roomId = text(message.room_id);
        const mapId = text(message.map_id);
        const players = playerList(message.players, matchPlayer);
        this.wantsQueue = false;
        this.update({ phase: 'matched', roomId, mapId, players, queue: null, error: null, readySeconds: null, syncedAt: null, room: null, result: null });
        this.send({ type: 'room_state' });
        return true;
      }
      case 'game_ready':
        if (!this.sameRoom(message)) return true;
        this.update({ phase: phase === 'matched' ? 'ready' : phase, readySeconds: integer(message.remaining_seconds), syncedAt: this.now() });
        return true;
      case 'game_tick': {
        const roomStatus = status(message.status);
        const seconds = integer(message.remaining_seconds);
        const ready = roomStatus === 'READY';
        const next = ROOM_PHASES.includes(phase) && phase !== 'finished' ? phaseOf(roomStatus) : phase;
        this.update(ready ? { phase: next, readySeconds: seconds, syncedAt: this.now() } : { phase: next, readySeconds: 0, remainingSeconds: seconds });
        this.updateRoom(() => ({ status: roomStatus, ...(ready ? { readyRemainingSeconds: seconds } : { readyRemainingSeconds: 0, remainingSeconds: seconds }) }));
        return true;
      }
      case 'game_started': {
        if (!this.sameRoom(message)) return true;
        const duration = integer(message.duration_seconds, 1);
        this.update({ phase: 'started', durationSeconds: duration, readySeconds: 0, remainingSeconds: duration });
        this.updateRoom(() => ({ status: 'PLAYING', readyRemainingSeconds: 0 }));
        this.send({ type: 'room_state' });
        return true;
      }
      case 'deathmatch_started': {
        const seconds = integer(message.remaining_seconds);
        this.update({ remainingSeconds: seconds });
        this.updateRoom(() => ({ status: 'DEATHMATCH', remainingSeconds: seconds }));
        return true;
      }
      case 'cost_updated': {
        const cost = integer(message.cost);
        const maxCost = integer(message.max_cost, 1);
        this.updateRoom(() => ({ myCost: cost, maxCost }));
        return true;
      }
      case 'hand_updated': {
        if (!Array.isArray(message.hand)) fail();
        const hand = message.hand.map(text);
        const cost = integer(message.cost);
        this.updateRoom(() => ({ myHand: hand, myCost: cost }));
        return true;
      }
      case 'player_moved': {
        const userId = integer(message.user_id, 1);
        const position: Point = [integer(message.x), integer(message.y)];
        this.updateRoom((room) => ({ players: room.players.map((player) => player.userId === userId ? { ...player, position } : player) }));
        return true;
      }
      case 'tiles_updated': {
        if (!Array.isArray(message.tiles)) fail();
        if (!this.sameRoom(message)) return true;
        const tiles = message.tiles.map((item) => {
          const tile = record(item);
          return { x: integer(tile.x), y: integer(tile.y), owner: integer(tile.new_owner, 1) };
        });
        this.updateRoom((room) => {
          const tileOwners = room.tileOwners.map((row) => [...row]);
          for (const { x, y, owner } of tiles) if (tileOwners[y] && x < tileOwners[y].length) tileOwners[y][x] = owner;
          const players = room.players.map((player) => ({ ...player, tileCount: tileOwners.flat().filter((owner) => owner === player.userId).length }));
          return { tileOwners, players };
        });
        return true;
      }
      case 'ultimate_activated':
        if (integer(message.user_id, 1) === this.state.userId) this.updateRoom(() => ({ myUltimateUsed: true }));
        return true;
      case 'player_disconnected':
      case 'player_reconnected': {
        const userId = integer(message.user_id, 1);
        const connected = message.type === 'player_reconnected';
        this.updateRoom((room) => ({ players: room.players.map((player) => player.userId === userId ? { ...player, connected } : player) }));
        return true;
      }
      case 'game_finished':
        if (!this.sameRoom(message)) return true;
        this.update({ phase: 'finished', remainingSeconds: 0 });
        this.updateRoom(() => ({ status: 'FINISHED', remainingSeconds: 0 }));
        return true;
      case 'game_result': {
        if (!Array.isArray(message.results)) fail();
        if (!this.sameRoom(message)) return true;
        const results = message.results.map((item) => {
          const row = record(item);
          return { userId: integer(row.user_id, 1), rank: integer(row.rank, 1), isWinner: flag(row.is_winner) };
        });
        this.update({ result: { winnerId: integer(message.winner_id, 1), results } });
        return true;
      }
      case 'room_left': {
        const keep = this.state;
        this.state = { ...initialSession(), connected: keep.connected, userId: keep.userId, characterId: keep.characterId };
        this.notify();
        if (this.wantsQueue) this.join();
        return true;
      }
      case 'session_replaced':
        this.wantsQueue = false;
        this.setError('session_replaced', '다른 창에서 같은 계정으로 접속하여 현재 연결이 종료되었습니다.');
        return true;
      case 'card_used':
      case 'move_requested':
      case 'attack_requested':
        return true;
      case 'error':
        this.handleError(text(message.code), typeof message.message === 'string' ? message.message : '서버에서 요청을 거부했습니다.');
        return true;
      default:
        return false;
    }
  }

  private handleError(code: string, message: string): void {
    const { phase, roomId } = this.state;
    if (code === 'auth_failed' || code === 'auth_required') {
      this.wantsQueue = false;
      this.setError('auth_failed', '로그인 정보가 만료되었거나 올바르지 않습니다. 다시 로그인해 주세요.');
    } else if (code === 'already_in_queue') {
      if (phase === 'joining') this.update({ phase: 'waiting' });
    } else if (code === 'already_in_game') {
      if (roomId === null) this.send({ type: 'room_state' });
    } else if (code === 'not_in_queue') {
      if (phase === 'cancelling') this.update({ phase: roomId === null ? 'cancelled' : phase });
    } else if (code === 'not_in_room' && phase === 'authenticating') {
      this.update({ roomId: null, mapId: null, players: [], room: null });
      this.setError('not_in_room', '진행 중인 경기가 없습니다. 캐릭터 선택에서 다시 매칭해 주세요.');
    } else if (QUEUE_ERRORS.includes(code) && phase === 'joining') {
      this.wantsQueue = false;
      this.setError(code, message);
    } else {
      this.update({ warning: message });
    }
  }

  private setError(code: string, message: string): void {
    this.update({ phase: 'error', error: { code, message }, connected: this.isOpen() });
  }

  private update(change: Partial<MatchSession>): void {
    this.state = { ...this.state, ...change };
    this.notify();
  }

  private notify(): void {
    for (const listener of this.listeners) listener();
  }
}
