import type { AuthToken } from './auth';

export interface LobbyAccount {
  displayName: string;
  level: number;
  totalExperience: number;
}

async function getAccountJson(path: string, token: AuthToken, request: typeof fetch): Promise<unknown> {
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
  if (!response.ok) throw new Error('계정 정보를 불러오지 못했습니다.');

  try {
    return await response.json();
  } catch {
    throw new Error('서버 응답을 확인할 수 없습니다.');
  }
}

export async function loadLobbyAccount(token: AuthToken, request: typeof fetch = fetch): Promise<LobbyAccount> {
  const [profile, level] = await Promise.all([
    getAccountJson('/users/me/profile', token, request),
    getAccountJson('/users/me/level', token, request),
  ]);
  if (!profile || typeof profile !== 'object' || !('display_name' in profile)
    || typeof profile.display_name !== 'string' || !profile.display_name
    || !level || typeof level !== 'object' || !('level' in level) || !('total_experience' in level)
    || !Number.isInteger(level.level) || (level.level as number) < 1
    || !Number.isInteger(level.total_experience) || (level.total_experience as number) < 0) {
    throw new Error('서버 응답을 확인할 수 없습니다.');
  }
  return { displayName: profile.display_name, level: level.level as number, totalExperience: level.total_experience as number };
}

export interface LevelProgress {
  current: number;
  needed: number;
  remaining: number;
  percent: number;
}

const EXPERIENCE_STEP = 50;

export function levelProgress(level: number, totalExperience: number): LevelProgress {
  const start = EXPERIENCE_STEP * level * (level - 1);
  const next = EXPERIENCE_STEP * level * (level + 1);
  const needed = next - start;
  const current = Math.max(0, Math.min(needed, totalExperience - start));
  return { current, needed, remaining: Math.max(0, next - totalExperience), percent: (current / needed) * 100 };
}

export async function loadLobbyWallet(token: AuthToken, request: typeof fetch = fetch): Promise<number> {
  const wallet = await getAccountJson('/users/me/wallet', token, request);
  if (!wallet || typeof wallet !== 'object' || !('gold' in wallet) || !Number.isSafeInteger(wallet.gold) || (wallet.gold as number) < 0) {
    throw new Error('서버 응답을 확인할 수 없습니다.');
  }
  return wallet.gold as number;
}
