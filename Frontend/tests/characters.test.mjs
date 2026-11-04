import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
import { arrangeCarousel, assignCharacterSlots, findCharacter, loadCharacters, loadCharacterSelection } from '../src/services/characters.ts';

const token = { access_token: 'test-token', token_type: 'bearer' };
const serverCharacters = [
  { character_id: 'character_01', name: '테스트 캐릭터 1', ultimate_id: 'ultimate_01', ultimate_name: '테스트 궁극기 1' },
  { character_id: 'character_02', name: '테스트 캐릭터 2', ultimate_id: 'ultimate_02', ultimate_name: '테스트 궁극기 2' },
];

function server(preferred) {
  return async (url, options) => {
    if (url === '/characters') return Response.json({ characters: serverCharacters });
    if (url === '/users/me/profile') {
      assert.equal(options.headers.Authorization, 'Bearer test-token');
      return preferred instanceof Response ? preferred : Response.json({ display_name: '페인터', preferred_character_id: preferred });
    }
    assert.fail(`unexpected ${url}`);
  };
}

test('maps the server character list without inventing fields', async () => {
  const characters = await loadCharacters(server('character_01'));
  assert.deepEqual(characters, [
    { characterId: 'character_01', name: '테스트 캐릭터 1', ultimateId: 'ultimate_01', ultimateName: '테스트 궁극기 1' },
    { characterId: 'character_02', name: '테스트 캐릭터 2', ultimateId: 'ultimate_02', ultimateName: '테스트 궁극기 2' },
  ]);
});

test('starts with the preferred character when the server lists it', async () => {
  const selection = await loadCharacterSelection(token, server('character_02'));
  assert.equal(selection.initialCharacterId, 'character_02');
});

test('falls back to the first server character for unknown or unavailable preferences', async () => {
  for (const preferred of ['Lunar', '', new Response('', { status: 500 })]) {
    const selection = await loadCharacterSelection(token, server(preferred));
    assert.equal(selection.initialCharacterId, 'character_01');
  }
});

test('an empty list has no selectable character', async () => {
  const selection = await loadCharacterSelection(token, async (url) => Response.json(url === '/characters' ? { characters: [] } : { preferred_character_id: 'character_01' }));
  assert.deepEqual(selection, { characters: [], initialCharacterId: null });
});

test('finds only characters the server returned', async () => {
  const characters = await loadCharacters(server('character_01'));
  assert.equal(findCharacter(characters, 'character_02').name, '테스트 캐릭터 2');
  assert.equal(findCharacter(characters, 'Lunar'), null);
  assert.equal(findCharacter(characters, null), null);
});

test('explains server errors and connection failures', async () => {
  await assert.rejects(loadCharacters(async () => new Response('', { status: 500 })), /캐릭터 목록을 불러오지 못했습니다/);
  await assert.rejects(loadCharacters(async () => { throw new TypeError('fetch failed'); }), /연결할 수 없습니다/);
  await assert.rejects(loadCharacterSelection(token, async () => new Response('', { status: 503 })), /캐릭터 목록/);
});

test('rejects malformed character responses', async () => {
  const valid = serverCharacters[0];
  for (const body of [{}, { characters: {} }, { characters: [{ ...valid, name: '' }] }, { characters: [{ ...valid, ultimate_name: 1 }] }, { characters: [valid, valid] }]) {
    await assert.rejects(loadCharacters(async () => Response.json(body)), /서버 응답/);
  }
  await assert.rejects(loadCharacters(async () => new Response('<html></html>')), /서버 응답/);
});

test('fills card slots with server characters and leaves the rest empty', async () => {
  const characters = await loadCharacters(server('character_01'));
  assert.deepEqual(assignCharacterSlots(characters, 3).map((slot) => slot && slot.characterId), ['character_01', 'character_02', null]);
  assert.deepEqual(assignCharacterSlots([], 3), [null, null, null]);
  assert.deepEqual(assignCharacterSlots(characters, 1).map((slot) => slot.characterId), ['character_01']);
});

test('uses the full-resolution character select storyboard', async () => {
  const png = await readFile(new URL('../public/storyboard/character-select.png', import.meta.url));
  assert.equal(png.toString('ascii', 1, 4), 'PNG');
  assert.deepEqual([png.readUInt32BE(16), png.readUInt32BE(20)], [1672, 941]);
});

function carousel(ids, selectedId) {
  const characters = ids.map((id) => ({ characterId: id, name: id, ultimateId: `u_${id}`, ultimateName: `u_${id}` }));
  return Object.fromEntries(arrangeCarousel(characters, selectedId).map(({ character, position }) => [character.characterId, position]));
}

test('keeps the selected character in the center of the carousel', () => {
  assert.deepEqual(carousel(['a'], 'a'), { a: 'center' });
  assert.deepEqual(carousel(['a', 'b'], 'a'), { a: 'center', b: 'right' });
  assert.deepEqual(carousel(['a', 'b'], 'b'), { a: 'right', b: 'center' });
  assert.deepEqual(carousel(['a', 'b', 'c'], 'a'), { a: 'center', b: 'right', c: 'left' });
  assert.deepEqual(carousel(['a', 'b', 'c'], 'c'), { a: 'right', b: 'left', c: 'center' });
  assert.deepEqual(carousel(['a', 'b', 'c', 'd', 'e'], 'b'), { a: 'left', b: 'center', c: 'right', d: 'hidden', e: 'hidden' });
});

test('does not center a character the server did not return', () => {
  assert.deepEqual(carousel(['a', 'b'], 'Lunar'), { a: 'hidden', b: 'hidden' });
  assert.deepEqual(carousel(['a', 'b'], null), { a: 'hidden', b: 'hidden' });
  assert.deepEqual(arrangeCarousel([], 'a'), []);
});
