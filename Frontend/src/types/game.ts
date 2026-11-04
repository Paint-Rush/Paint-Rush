export type RoomStatus = 'READY' | 'PLAYING' | 'DEATHMATCH' | 'FINISHED';

export type MatchPhase =
  | 'idle'
  | 'connecting'
  | 'authenticating'
  | 'joining'
  | 'waiting'
  | 'cancelling'
  | 'cancelled'
  | 'matched'
  | 'ready'
  | 'started'
  | 'finished'
  | 'error';

export type Point = [number, number];

export interface MatchPlayer {
  userId: number;
  color: string;
  spawn: Point;
  characterId: string;
  characterName: string | null;
  ultimateId: string | null;
  ultimateName: string | null;
}

export interface RoomPlayer extends MatchPlayer {
  position: Point;
  alive: boolean;
  connected: boolean;
  tileCount: number;
}

export interface RoomSnapshot {
  roomId: string;
  status: RoomStatus;
  mapId: string;
  map: { width: number; height: number; tiles: unknown[][] };
  players: RoomPlayer[];
  myCost: number;
  myHand: string[];
  myNextCard: string | null;
  myUltimateUsed: boolean;
  tileOwners: (number | null)[][];
  startedAt: string | null;
  readyRemainingSeconds: number;
  remainingSeconds: number;
  maxCost: number;
}

export interface GameResultRow {
  userId: number;
  rank: number;
  isWinner: boolean;
}

export interface MatchError {
  code: string;
  message: string;
}

export interface MatchSession {
  phase: MatchPhase;
  connected: boolean;
  userId: number | null;
  characterId: string | null;
  queue: { waiting: number; matchSize: number } | null;
  roomId: string | null;
  mapId: string | null;
  players: MatchPlayer[];
  readySeconds: number | null;
  remainingSeconds: number | null;
  durationSeconds: number | null;
  syncedAt: number | null;
  room: RoomSnapshot | null;
  result: { winnerId: number; results: GameResultRow[] } | null;
  error: MatchError | null;
  warning: string | null;
}

export interface ServerMessage {
  type: string;
  [key: string]: unknown;
}
