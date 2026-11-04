import test from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
const bundled = await build({ entryPoints: [fileURLToPath(new URL('../src/services/loadout.ts', import.meta.url))], bundle: true, write: false, format: 'esm', platform: 'node' });
const { loadPlayerLoadout, savePlayerLoadout } = await import(`data:text/javascript;base64,${Buffer.from(bundled.outputFiles[0].text).toString('base64')}`);
const token = { access_token: 'test', token_type: 'bearer' };
const deck = ['jumper', 'dash', 'sliding', 'criss_cross', 'swastika', 'the_line'];
const fixtures = {
  '/characters': { characters: [{ character_id: 'character_01', name: '테스트 1', ultimate_name: '궁극기 1' }, { character_id: 'character_02', name: '테스트 2', ultimate_name: '궁극기 2' }] },
  '/cards': { deck_size: 6, cards: [{ card_id: 'dash', type: 'MOVE', cost: 3 }, { card_id: 'jumper', type: 'MOVE', cost: 4 }, { card_id: 'criss_cross', type: 'ATTACK', cost: 5 }, { card_id: 'swastika', type: 'ATTACK', cost: 6 }, { card_id: 'sliding', type: 'MOVE', cost: 4 }, { card_id: 'the_line', type: 'ATTACK', cost: 3 }] },
  '/users/me/deck': { cards: deck },
  '/users/me/profile': { display_name: '페인터', preferred_character_id: 'character_02' },
};
test('loads registered characters and the saved six-card deck, preserving duplicate slots', async () => {
  const result = await loadPlayerLoadout(token, async (url, options) => { assert.equal(options.headers.Authorization, 'Bearer test'); return Response.json(fixtures[url]); });
  assert.equal(result.character, 'character_02'); assert.deepEqual(result.deck, deck); assert.equal(result.characters.length, 2);
});
test('rejects unregistered preferences, wrong catalog costs and invalid decks', async () => {
  for (const [path, value] of [['/users/me/profile', { display_name: 'x', preferred_character_id: 'invented' }], ['/cards', { deck_size: 6, cards: [{ card_id: 'dash', type: 'MOVE', cost: 3 }] }], ['/users/me/deck', { cards: ['dash'] }]]) {
    await assert.rejects(loadPlayerLoadout(token, async url => Response.json(url === path ? value : fixtures[url])));
  }
  await assert.rejects(loadPlayerLoadout(token, async url => Response.json(url === '/cards' ? { deck_size: 6, cards: [{ card_id: 'attack_bomb', type: 'ATTACK', cost: 5 }] } : fixtures[url])), /여섯 카드/);
  const wrongCost = { ...fixtures['/cards'], cards: fixtures['/cards'].cards.map(card => card.card_id === 'dash' ? { ...card, cost: 2 } : card) };
  await assert.rejects(loadPlayerLoadout(token, async url => Response.json(url === '/cards' ? wrongCost : fixtures[url])), /코스트/);
});
test('saves the deck and preferred character, and aborts on a failed or mismatched save', async () => {
  const calls = [];
  await savePlayerLoadout(token, 'character_02', deck, async (url, options) => { calls.push([url, options.method, JSON.parse(options.body)]); return Response.json(url.endsWith('deck') ? { cards: deck } : { preferred_character_id: 'character_02' }); });
  assert.deepEqual(calls, [['/users/me/deck', 'PUT', { cards: deck }], ['/users/me/profile', 'PATCH', { preferred_character_id: 'character_02' }]]);
  let requests = 0;
  await assert.rejects(savePlayerLoadout(token, 'character_02', deck, async () => { requests++; return new Response('', { status: 500 }); }));
  assert.equal(requests, 1);
  await assert.rejects(savePlayerLoadout(token, 'character_02', deck, async () => Response.json({ cards: [...deck].reverse() })), /저장된 덱/);
});
