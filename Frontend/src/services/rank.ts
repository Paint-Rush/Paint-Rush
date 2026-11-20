import type { AuthToken } from './auth';

export type TierName = 'BRONZE' | 'SILVER' | 'GOLD' | 'PLATINUM' | 'DIAMOND' | 'MASTER' | 'CHALLENGER';

export interface MyRank {
  userId: number;
  totalRp: number;
  tier: TierName;
  division: number | null;
  nextTier: TierName | null;
  nextDivision: number | null;
  rpToNext: number | null;
  rankingPosition: number | null;
}

export interface RankChange {
  matchId: string;
  placement: number;
  rpBefore: number;
  placementRpChange: number;
  rpChange: number;
  rpAfter: number;
  createdAt: string;
}

export interface RankingEntry {
  position: number;
  userId: number;
  username: string;
  displayName: string;
  totalRp: number;
  tier: TierName;
}

export interface RankingList {
  total: number;
  rankings: RankingEntry[];
}

export interface RankProgress {
  percent: number | null;
  label: string;
}

export interface RankSettlement {
  roomId: string;
  matchId: string;
  placement: number;
  rpBefore: number;
  placementRpChange: number;
  rpChange: number;
  rpAfter: number;
  tierBefore: TierName | null;
  divisionBefore: number | null;
  tierAfter: TierName | null;
  divisionAfter: number | null;
}

export type RankSettlementState =
  | { status: 'applied'; roomId: string; settlement: RankSettlement }
  | { status: 'unranked'; roomId: string; reason: string }
  | { status: 'error'; roomId: string; message: string };

export type MatchRankState =
  | { status: 'pending' }
  | { status: 'applied'; settlement: RankSettlement }
  | { status: 'unranked'; reason: string }
  | { status: 'error'; message: string };

export type TierMove = 'up' | 'down' | 'same';

export const TIERS: TierName[] = ['BRONZE', 'SILVER', 'GOLD', 'PLATINUM', 'DIAMOND', 'MASTER', 'CHALLENGER'];
export const TIER_LABELS: Record<TierName, string> = {
  BRONZE: 'Bronze',
  SILVER: 'Silver',
  GOLD: 'Gold',
  PLATINUM: 'Platinum',
  DIAMOND: 'Diamond',
  MASTER: 'Master',
  CHALLENGER: 'Challenger',
};
export const RANKED_PLAYERS = 4;
export const CHALLENGER_LIMIT = 100;
export const RANKING_PAGE_SIZE = 100;
export const HISTORY_LIMIT = 20;
const DIVISION_SPAN = 100;
const MASTER_SPAN = 300;

const BAD_RESPONSE = '서버 응답을 확인할 수 없습니다.';

async function getRankJson(path: string, token: AuthToken, request: typeof fetch): Promise<unknown> {
  let response: Response;
  try {
    response = await request(path, {
      headers: { Authorization: `Bearer ${token.access_token}` },
      signal: AbortSignal.timeout(10000),
    });
  } catch {
    throw new Error('서버에 연결할 수 없습니다.');
  }

  if (response.status === 401) throw new Error('로그인이 만료되었습니다. 다시 로그인해 주세요.');
  if (!response.ok) throw new Error('랭크 정보를 불러오지 못했습니다.');

  try {
    return await response.json();
  } catch {
    throw new Error(BAD_RESPONSE);
  }
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(BAD_RESPONSE);
  return value as Record<string, unknown>;
}

function integer(value: unknown, min = 0): number {
  if (!Number.isInteger(value) || (value as number) < min) throw new Error(BAD_RESPONSE);
  return value as number;
}

function signedInteger(value: unknown): number {
  if (!Number.isInteger(value)) throw new Error(BAD_RESPONSE);
  return value as number;
}

function nullable<T>(value: unknown, read: (item: unknown) => T): T | null {
  return value === null ? null : read(value);
}

function tier(value: unknown): TierName {
  if (!TIERS.includes(value as TierName)) throw new Error(BAD_RESPONSE);
  return value as TierName;
}

function text(value: unknown): string {
  if (typeof value !== 'string' || !value) throw new Error(BAD_RESPONSE);
  return value;
}

function division(value: unknown): number {
  const result = integer(value, 1);
  if (result > 3) throw new Error(BAD_RESPONSE);
  return result;
}

export function readMyRank(value: unknown): MyRank {
  const data = record(value);
  const rank: MyRank = {
    userId: integer(data.user_id, 1),
    totalRp: integer(data.total_rp),
    tier: tier(data.tier),
    division: nullable(data.division, division),
    nextTier: nullable(data.next_tier, tier),
    nextDivision: nullable(data.next_division, division),
    rpToNext: nullable(data.rp_to_next, (item) => integer(item, 1)),
    rankingPosition: nullable(data.ranking_position, (item) => integer(item, 1)),
  };
  const ranked = rank.tier === 'MASTER' || rank.tier === 'CHALLENGER';
  if (ranked !== (rank.division === null)) throw new Error(BAD_RESPONSE);
  if (rank.tier === 'CHALLENGER' && rank.rankingPosition === null) throw new Error(BAD_RESPONSE);
  return rank;
}

export function readRankChange(value: unknown): RankChange {
  const data = record(value);
  const change: RankChange = {
    matchId: text(data.match_id),
    placement: integer(data.placement, 1),
    rpBefore: integer(data.rp_before),
    placementRpChange: signedInteger(data.placement_rp_change),
    rpChange: signedInteger(data.rp_change),
    rpAfter: integer(data.rp_after),
    createdAt: text(data.created_at),
  };
  if (change.placement > RANKED_PLAYERS || change.rpBefore + change.rpChange !== change.rpAfter) throw new Error(BAD_RESPONSE);
  return change;
}

export function readRanking(value: unknown): RankingList {
  const data = record(value);
  if (!Array.isArray(data.rankings)) throw new Error(BAD_RESPONSE);
  const rankings = data.rankings.map((item) => {
    const entry = record(item);
    return {
      position: integer(entry.position, 1),
      userId: integer(entry.user_id, 1),
      username: text(entry.username),
      displayName: text(entry.display_name),
      totalRp: integer(entry.total_rp),
      tier: tier(entry.tier),
    };
  });
  if (rankings.some((entry) => entry.tier !== 'MASTER' && entry.tier !== 'CHALLENGER')) throw new Error(BAD_RESPONSE);
  return { total: integer(data.total), rankings };
}

export async function loadMyRank(token: AuthToken, request: typeof fetch = fetch): Promise<MyRank> {
  return readMyRank(await getRankJson('/users/me/rank', token, request));
}

export async function loadRankHistory(token: AuthToken, request: typeof fetch = fetch, limit = HISTORY_LIMIT): Promise<RankChange[]> {
  const data = await getRankJson(`/users/me/rank/history?limit=${limit}`, token, request);
  if (!Array.isArray(data)) throw new Error(BAD_RESPONSE);
  return data.map(readRankChange);
}

export async function loadRanking(token: AuthToken, page: number, request: typeof fetch = fetch): Promise<RankingList> {
  const offset = Math.max(0, Math.floor(page)) * RANKING_PAGE_SIZE;
  return readRanking(await getRankJson(`/rankings?limit=${RANKING_PAGE_SIZE}&offset=${offset}`, token, request));
}

export async function loadChallengers(token: AuthToken, request: typeof fetch = fetch): Promise<RankingList> {
  return readRanking(await getRankJson('/rankings/challengers', token, request));
}

export function tierLabel(name: TierName | null, divisionValue: number | null = null): string {
  if (!name) return '';
  return divisionValue === null ? TIER_LABELS[name] : `${TIER_LABELS[name]} ${divisionValue}`;
}

export function rankProgress(rank: MyRank): RankProgress {
  if (rank.tier === 'CHALLENGER') {
    return { percent: null, label: `챌린저 ${rank.rankingPosition}위 / 상위 ${CHALLENGER_LIMIT}위 경쟁 중` };
  }
  if (rank.tier === 'MASTER') {
    if (rank.rpToNext === null) {
      const position = rank.rankingPosition === null ? '' : ` · 현재 ${rank.rankingPosition}위`;
      return { percent: 100, label: `챌린저 RP 조건 달성 · 상위 ${CHALLENGER_LIMIT}위 진입 필요${position}` };
    }
    return {
      percent: Math.max(0, Math.min(100, ((MASTER_SPAN - rank.rpToNext) / MASTER_SPAN) * 100)),
      label: `Challenger 최소 조건까지 ${rank.rpToNext} RP`,
    };
  }
  if (rank.rpToNext === null) return { percent: null, label: '' };
  return {
    percent: Math.max(0, Math.min(100, DIVISION_SPAN - rank.rpToNext)),
    label: `${tierLabel(rank.nextTier, rank.nextDivision)}까지 ${rank.rpToNext} RP`,
  };
}

export function formatRp(change: number): string {
  return `${change > 0 ? '+' : ''}${change} RP`;
}

function tierDivision(name: TierName, value: unknown): number | null {
  const ranked = name === 'MASTER' || name === 'CHALLENGER';
  if (ranked) {
    if (value !== null) throw new Error(BAD_RESPONSE);
    return null;
  }
  return division(value);
}

export function readRankSettlement(value: unknown): RankSettlementState {
  const data = record(value);
  const roomId = text(data.room_id);
  const matchId = text(data.match_id);
  if (matchId !== roomId) throw new Error(BAD_RESPONSE);
  if (data.applied === false) return { status: 'unranked', roomId, reason: text(data.reason) };
  if (data.applied !== true) throw new Error(BAD_RESPONSE);
  const legacy = data.tier_before === null && data.tier_after === null;
  const tierBefore = legacy ? null : tier(data.tier_before);
  const tierAfter = legacy ? null : tier(data.tier_after);
  const settlement: RankSettlement = {
    roomId,
    matchId,
    placement: integer(data.placement, 1),
    rpBefore: integer(data.rp_before),
    placementRpChange: signedInteger(data.placement_rp_change),
    rpChange: signedInteger(data.rp_change),
    rpAfter: integer(data.rp_after),
    tierBefore,
    divisionBefore: tierBefore === null ? nullable(data.division_before, () => { throw new Error(BAD_RESPONSE); }) : tierDivision(tierBefore, data.division_before),
    tierAfter,
    divisionAfter: tierAfter === null ? nullable(data.division_after, () => { throw new Error(BAD_RESPONSE); }) : tierDivision(tierAfter, data.division_after),
  };
  if (settlement.placement > RANKED_PLAYERS || settlement.rpBefore + settlement.rpChange !== settlement.rpAfter) throw new Error(BAD_RESPONSE);
  return { status: 'applied', roomId, settlement };
}

export function matchRankState(state: RankSettlementState | null, roomId: string | null): MatchRankState {
  if (!state || state.roomId !== roomId) return { status: 'pending' };
  if (state.status === 'applied') return { status: 'applied', settlement: state.settlement };
  if (state.status === 'unranked') return { status: 'unranked', reason: state.reason };
  return { status: 'error', message: state.message };
}

export function tierMove(settlement: RankSettlement): TierMove | null {
  if (settlement.tierBefore === null || settlement.tierAfter === null) return null;
  const before = TIERS.indexOf(settlement.tierBefore);
  const after = TIERS.indexOf(settlement.tierAfter);
  if (before !== after) return after > before ? 'up' : 'down';
  if (settlement.divisionBefore === settlement.divisionAfter) return 'same';
  return (settlement.divisionAfter ?? 0) > (settlement.divisionBefore ?? 0) ? 'up' : 'down';
}

export function tierName(name: TierName, divisionValue: number | null): string {
  return divisionValue === null ? name : `${name} ${divisionValue}`;
}
