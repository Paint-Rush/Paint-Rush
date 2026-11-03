import assert from 'node:assert/strict';
import test from 'node:test';
import { loadLobbyAccount } from '../src/services/account.ts';

const token = { access_token: 'test-token', token_type: 'bearer' };

test('loads the profile and level with the bearer token', async () => {
  const calls = [];
  const account = await loadLobbyAccount(token, async (url, options) => {
    calls.push(url);
    assert.equal(options.headers.Authorization, 'Bearer test-token');
    if (url === '/users/me/profile') return Response.json({ user_id: 1, username: 'painter', display_name: '페인터', preferred_character_id: 'character_01' });
    if (url === '/users/me/level') return Response.json({ level: 3, total_experience: 1250 });
    assert.fail(`unexpected ${url}`);
  });
  assert.deepEqual(account, { displayName: '페인터', level: 3, totalExperience: 1250 });
  assert.deepEqual(calls.sort(), ['/users/me/level', '/users/me/profile']);
});

test('explains expired tokens, server errors, and connection failures', async () => {
  await assert.rejects(loadLobbyAccount(token, async () => new Response('', { status: 401 })), /다시 로그인/);
  await assert.rejects(loadLobbyAccount(token, async () => new Response('', { status: 500 })), /불러오지 못했습니다/);
  await assert.rejects(loadLobbyAccount(token, async () => { throw new TypeError('fetch failed'); }), /연결할 수 없습니다/);
});

test('rejects malformed profile or level responses', async () => {
  const profile = { display_name: '페인터' };
  const level = { level: 1, total_experience: 0 };
  for (const [badProfile, badLevel] of [[{}, level], [{ display_name: '' }, level], [profile, {}], [profile, { level: 0, total_experience: 0 }], [profile, { level: 1, total_experience: -1 }], [profile, { level: 1.5, total_experience: 0 }]]) {
    await assert.rejects(loadLobbyAccount(token, async (url) => Response.json(url.endsWith('profile') ? badProfile : badLevel)), /서버 응답/);
  }
});
