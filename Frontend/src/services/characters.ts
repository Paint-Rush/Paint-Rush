import type { AuthToken } from './auth';

export interface PlayableCharacter {
  characterId: string;
  name: string;
  ultimateId: string;
  ultimateName: string;
}

export interface CharacterSelection {
  characters: PlayableCharacter[];
  initialCharacterId: string | null;
}

function isFilledString(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0;
}

export async function loadCharacters(request: typeof fetch = fetch): Promise<PlayableCharacter[]> {
  let response: Response;
  try {
    response = await request('/characters', { signal: AbortSignal.timeout(10000) });
  } catch {
    throw new Error('서버에 연결할 수 없습니다.');
  }
  if (!response.ok) throw new Error('캐릭터 목록을 불러오지 못했습니다.');

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new Error('서버 응답을 확인할 수 없습니다.');
  }
  if (!body || typeof body !== 'object' || !('characters' in body) || !Array.isArray(body.characters)) {
    throw new Error('서버 응답을 확인할 수 없습니다.');
  }

  const characters: PlayableCharacter[] = [];
  for (const item of body.characters as unknown[]) {
    if (!item || typeof item !== 'object'
      || !('character_id' in item) || !isFilledString(item.character_id)
      || !('name' in item) || !isFilledString(item.name)
      || !('ultimate_id' in item) || !isFilledString(item.ultimate_id)
      || !('ultimate_name' in item) || !isFilledString(item.ultimate_name)
      || characters.some((character) => character.characterId === item.character_id)) {
      throw new Error('서버 응답을 확인할 수 없습니다.');
    }
    characters.push({ characterId: item.character_id, name: item.name, ultimateId: item.ultimate_id, ultimateName: item.ultimate_name });
  }
  return characters;
}

export async function loadPreferredCharacterId(token: AuthToken, request: typeof fetch = fetch): Promise<string | null> {
  try {
    const response = await request('/users/me/profile', {
      headers: { Authorization: `Bearer ${token.access_token}` },
      signal: AbortSignal.timeout(10000),
    });
    if (!response.ok) return null;
    const profile: unknown = await response.json();
    if (!profile || typeof profile !== 'object' || !('preferred_character_id' in profile) || !isFilledString(profile.preferred_character_id)) return null;
    return profile.preferred_character_id;
  } catch {
    return null;
  }
}

export function findCharacter(characters: PlayableCharacter[], characterId: string | null): PlayableCharacter | null {
  return characters.find((character) => character.characterId === characterId) ?? null;
}

export async function loadCharacterSelection(token: AuthToken, request: typeof fetch = fetch): Promise<CharacterSelection> {
  const [characters, preferredId] = await Promise.all([loadCharacters(request), loadPreferredCharacterId(token, request)]);
  const initial = findCharacter(characters, preferredId) ?? characters[0] ?? null;
  return { characters, initialCharacterId: initial ? initial.characterId : null };
}

export function assignCharacterSlots(characters: PlayableCharacter[], slotCount: number): (PlayableCharacter | null)[] {
  return Array.from({ length: slotCount }, (_, index) => characters[index] ?? null);
}

export type CarouselPosition = 'center' | 'left' | 'right' | 'hidden';

export function arrangeCarousel(characters: PlayableCharacter[], selectedId: string | null): { character: PlayableCharacter; position: CarouselPosition }[] {
  const center = characters.findIndex((character) => character.characterId === selectedId);
  return characters.map((character, index) => {
    if (center < 0) return { character, position: 'hidden' };
    const offset = (index - center + characters.length) % characters.length;
    const position = offset === 0 ? 'center' : offset === 1 ? 'right' : offset === characters.length - 1 ? 'left' : 'hidden';
    return { character, position };
  });
}
