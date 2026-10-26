import assert from 'node:assert/strict';
import test from 'node:test';
import { login } from '../src/auth.ts';

test('sends the backend JSON credentials and returns its bearer token', async () => {
  const result = await login('Painter123', ' password123 ', async (url, options) => {
    assert.equal(url, '/api/v1/auth/login');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(options.body), { username: 'Painter123', password: ' password123 ' });
    return Response.json({ access_token: 'test-token', token_type: 'bearer' });
  });
  assert.deepEqual(result, { access_token: 'test-token', token_type: 'bearer' });
});

for (const [status, message] of [[401, /올바르지/], [422, /입력 형식/], [500, /오류/]]) {
  test(`handles HTTP ${status}`, async () => {
    await assert.rejects(login('painter', 'password123', async () => new Response('', { status })), message);
  });
}

test('handles a network failure', async () => {
  await assert.rejects(login('painter', 'password123', async () => { throw new TypeError('fetch failed'); }), /연결할 수 없습니다/);
});

test('rejects malformed success responses', async () => {
  for (const body of [{}, { access_token: '', token_type: 'bearer' }, { access_token: 'token', token_type: 'other' }]) {
    await assert.rejects(login('painter', 'password123', async () => Response.json(body)), /서버 응답/);
  }
  await assert.rejects(login('painter', 'password123', async () => new Response('<html></html>')), /서버 응답/);
});

test('rejects credentials outside the backend constraints before sending', async () => {
  for (const [username, password] of [['ab', 'password123'], ['bad-name', 'password123'], ['a'.repeat(21), 'password123'], ['painter', 'short'], ['painter', 'a'.repeat(33)]]) {
    await assert.rejects(login(username, password, async () => { assert.fail('must not send invalid credentials'); }), /3~20자/);
  }
});
