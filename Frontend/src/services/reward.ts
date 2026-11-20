export interface RewardSettlement {
  roomId: string;
  matchId: string;
  placement: number;
  goldEarned: number;
  goldBefore: number;
  goldAfter: number;
  experienceEarned: number;
  experienceBefore: number;
  experienceAfter: number;
  levelBefore: number;
  levelAfter: number;
  leveledUp: boolean;
}

export type RewardSettlementState =
  | { status: 'applied'; roomId: string; reward: RewardSettlement }
  | { status: 'unrewarded'; roomId: string; reason: string }
  | { status: 'error'; roomId: string; message: string };

const BAD_RESPONSE = '서버 응답을 확인할 수 없습니다.';
const MAX_PLACEMENT = 4;

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(BAD_RESPONSE);
  return value as Record<string, unknown>;
}

function integer(value: unknown, min = 0): number {
  if (!Number.isSafeInteger(value) || (value as number) < min) throw new Error(BAD_RESPONSE);
  return value as number;
}

function text(value: unknown): string {
  if (typeof value !== 'string' || !value) throw new Error(BAD_RESPONSE);
  return value;
}

export function readRewardSettlement(value: unknown): RewardSettlementState {
  const data = record(value);
  const roomId = text(data.room_id);
  const matchId = text(data.match_id);
  if (matchId !== roomId) throw new Error(BAD_RESPONSE);
  if (data.applied === false) return { status: 'unrewarded', roomId, reason: text(data.reason) };
  if (data.applied !== true || typeof data.leveled_up !== 'boolean') throw new Error(BAD_RESPONSE);
  const reward: RewardSettlement = {
    roomId,
    matchId,
    placement: integer(data.placement, 1),
    goldEarned: integer(data.gold_earned),
    goldBefore: integer(data.gold_before),
    goldAfter: integer(data.gold_after),
    experienceEarned: integer(data.experience_earned),
    experienceBefore: integer(data.experience_before),
    experienceAfter: integer(data.experience_after),
    levelBefore: integer(data.level_before, 1),
    levelAfter: integer(data.level_after, 1),
    leveledUp: data.leveled_up,
  };
  if (
    reward.placement > MAX_PLACEMENT
    || reward.goldBefore + reward.goldEarned !== reward.goldAfter
    || reward.experienceBefore + reward.experienceEarned !== reward.experienceAfter
    || reward.levelAfter < reward.levelBefore
    || reward.leveledUp !== reward.levelAfter > reward.levelBefore
  ) {
    throw new Error(BAD_RESPONSE);
  }
  return { status: 'applied', roomId, reward };
}

export type MatchRewardState =
  | { status: 'pending' }
  | { status: 'applied'; reward: RewardSettlement }
  | { status: 'unrewarded' }
  | { status: 'error'; message: string };

export function matchRewardState(state: RewardSettlementState | null, roomId: string | null): MatchRewardState {
  if (!state || state.roomId !== roomId) return { status: 'pending' };
  if (state.status === 'applied') return { status: 'applied', reward: state.reward };
  if (state.status === 'error') return { status: 'error', message: state.message };
  return state.reason === 'unrewarded_match' ? { status: 'unrewarded' } : { status: 'pending' };
}

export interface CelebrationStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const CELEBRATED_KEY = 'paint-rush:level-up-shown';
const celebrated = new Set<string>();

function sessionStore(): CelebrationStore | null {
  try {
    return typeof sessionStorage === 'undefined' ? null : sessionStorage;
  } catch {
    return null;
  }
}

export function claimCelebration(matchId: string, store: CelebrationStore | null = sessionStore()): boolean {
  if (celebrated.has(matchId)) return false;
  celebrated.add(matchId);
  try {
    if (!store) return true;
    const shown: string[] = JSON.parse(store.getItem(CELEBRATED_KEY) ?? '[]');
    if (Array.isArray(shown) && shown.includes(matchId)) return false;
    store.setItem(CELEBRATED_KEY, JSON.stringify([...(Array.isArray(shown) ? shown : []), matchId].slice(-20)));
  } catch {
    return true;
  }
  return true;
}

export function celebrationMatch(state: MatchRewardState | null): string | null {
  return state?.status === 'applied' && state.reward.leveledUp ? state.reward.matchId : null;
}
