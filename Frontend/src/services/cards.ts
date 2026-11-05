export type CardType = 'MOVE' | 'ATTACK';

export interface ServerCard {
  cardId: string;
  type: CardType;
  cost: number;
}

export type CardCatalog = Record<string, ServerCard>;

export function cardName(cardId: string): string {
  return cardId.replace(/^(move|attack)_/, '').replace(/_/g, ' ').toUpperCase();
}

export async function loadCards(request: typeof fetch = fetch): Promise<CardCatalog> {
  let response: Response;
  try {
    response = await request('/cards', { signal: AbortSignal.timeout(10000) });
  } catch {
    throw new Error('서버에 연결할 수 없습니다.');
  }
  if (!response.ok) throw new Error('카드 목록을 불러오지 못했습니다.');

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new Error('서버 응답을 확인할 수 없습니다.');
  }
  if (!body || typeof body !== 'object' || !('cards' in body) || !Array.isArray(body.cards)) {
    throw new Error('서버 응답을 확인할 수 없습니다.');
  }

  const catalog: CardCatalog = {};
  for (const item of body.cards as unknown[]) {
    if (!item || typeof item !== 'object') throw new Error('서버 응답을 확인할 수 없습니다.');
    const { card_id: cardId, type, cost } = item as Record<string, unknown>;
    if (typeof cardId !== 'string' || !cardId || (type !== 'MOVE' && type !== 'ATTACK')
      || typeof cost !== 'number' || !Number.isSafeInteger(cost) || cost < 0 || Object.hasOwn(catalog, cardId)) {
      throw new Error('서버 응답을 확인할 수 없습니다.');
    }
    catalog[cardId] = { cardId, type, cost };
  }
  return catalog;
}
