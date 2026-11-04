import type { AuthToken } from './auth';
import { CARDS } from '../game/model';

export interface CharacterOption { id: string; name: string; role: string }
export interface PlayerLoadout { characters: CharacterOption[]; character: string; deck: string[]; displayName: string; cardCosts: Record<string, number> }
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('서버의 준비 정보를 확인할 수 없습니다.');
  return value as Record<string, unknown>;
}
function readDeck(value: unknown): string[] {
  const cards = record(value).cards;
  if (!Array.isArray(cards) || cards.length !== 6 || cards.some(id => !CARDS.some(card => card.id === id))) throw new Error('서버 덱은 등록된 공격·이동 카드 6장이어야 합니다.');
  return [...cards];
}
async function requestJson(path: string, token: AuthToken, request: typeof fetch, method = 'GET', body?: object): Promise<unknown> {
  let response: Response;
  try {
    response = await request(path, { method, headers: { Authorization: `Bearer ${token.access_token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) }, ...(body ? { body: JSON.stringify(body) } : {}), signal: AbortSignal.timeout(10000) });
  } catch { throw new Error('플레이 준비 정보를 서버와 연결하지 못했습니다. 다시 시도해 주세요.'); }
  if (response.status === 401) throw new Error('로그인이 만료되었습니다. 다시 로그인해 주세요.');
  if (!response.ok) throw new Error('캐릭터·덱 정보를 처리하지 못했습니다. 다시 시도해 주세요.');
  try { return await response.json(); } catch { throw new Error('서버의 준비 정보를 확인할 수 없습니다.'); }
}

export async function loadPlayerLoadout(token: AuthToken, request: typeof fetch = fetch): Promise<PlayerLoadout> {
  const [characterData, cardData, deckData, profileData] = await Promise.all([
    requestJson('/characters', token, request), requestJson('/cards', token, request),
    requestJson('/users/me/deck', token, request), requestJson('/users/me/profile', token, request),
  ]);
  const rawCharacters = record(characterData).characters, catalog = record(cardData), profile = record(profileData);
  if (!Array.isArray(rawCharacters) || !rawCharacters.length || catalog.deck_size !== 6 || !Array.isArray(catalog.cards)) throw new Error('서버 캐릭터·카드 목록이 올바르지 않습니다.');
  const characters = rawCharacters.map(value => {
    const entry = record(value);
    if (typeof entry.character_id !== 'string' || !entry.character_id || typeof entry.name !== 'string' || !entry.name || typeof entry.ultimate_name !== 'string') throw new Error('서버 캐릭터가 올바르지 않습니다.');
    return { id: entry.character_id, name: entry.name, role: entry.ultimate_name };
  });
  if (new Set(characters.map(c => c.id)).size !== characters.length) throw new Error('서버 캐릭터가 중복됩니다.');
  const cardCosts: Record<string, number> = {};
  for (const value of catalog.cards) {
    const entry = record(value), local = CARDS.find(card => card.id === entry.card_id);
    if (!local) throw new Error('서버 카드 목록이 제공된 여섯 카드와 다릅니다. 서버에 새 카드 등록이 필요합니다.');
    if (entry.cost !== local.cost || Object.hasOwn(cardCosts, local.id)
      || (local.type === '이동' ? 'MOVE' : 'ATTACK') !== entry.type) throw new Error('서버 카드의 코스트·종류를 확인할 수 없습니다.');
    cardCosts[local.id] = entry.cost;
  }
  if (CARDS.some(card => !Object.hasOwn(cardCosts, card.id))) throw new Error('서버에 여섯 카드가 모두 등록되어야 합니다.');
  const deck = readDeck(deckData);
  if (deck.some(id => !(catalog.cards as unknown[]).some(value => record(value).card_id === id)) || !characters.some(c => c.id === profile.preferred_character_id) || typeof profile.display_name !== 'string' || !profile.display_name) throw new Error('서버의 선택 캐릭터·덱 정보를 확인할 수 없습니다.');
  return { characters, character: profile.preferred_character_id as string, deck, displayName: profile.display_name, cardCosts };
}

export async function savePlayerLoadout(token: AuthToken, character: string, deck: string[], request: typeof fetch = fetch): Promise<void> {
  readDeck({ cards: deck });
  if (!character) throw new Error('캐릭터를 선택해 주세요.');
  const savedDeck = readDeck(await requestJson('/users/me/deck', token, request, 'PUT', { cards: deck }));
  if (savedDeck.some((id, index) => id !== deck[index])) throw new Error('서버에 저장된 덱이 선택한 덱과 다릅니다.');
  const profile = record(await requestJson('/users/me/profile', token, request, 'PATCH', { preferred_character_id: character }));
  if (profile.preferred_character_id !== character) throw new Error('서버에 저장된 캐릭터가 선택한 캐릭터와 다릅니다.');
}
