import assert from 'node:assert/strict';
import test from 'node:test';
import { login, register } from '../src/auth.ts';

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

test('registration sends JSON to the register endpoint and returns the normalized username', async () => {
  const user = await register('Painter123', ' password123 ', async (url, options) => {
    assert.equal(url, '/api/v1/auth/register');
    assert.equal(options.method, 'POST');
    assert.equal(options.headers['Content-Type'], 'application/json');
    assert.deepEqual(JSON.parse(options.body), { username: 'Painter123', password: ' password123 ' });
    return Response.json({ id: 1, username: 'painter123' }, { status: 201 });
  });
  assert.deepEqual(user, { id: 1, username: 'painter123' });
});

test('registration explains duplicate usernames', async () => {
  await assert.rejects(register('painter', 'password123', async () => new Response('', { status: 409 })), /이미 사용 중/);
});

test('registration handles invalid, failed, and malformed responses', async () => {
  await assert.rejects(register('painter', 'short'), /3~20자/);
  await assert.rejects(register('painter', 'password123', async () => new Response('', { status: 422 })), /입력 형식/);
  await assert.rejects(register('painter', 'password123', async () => { throw new TypeError('offline'); }), /연결할 수 없습니다/);
  for (const body of [{ access_token: 'token' }, { id: -1, username: 'painter' }, { id: 1, username: '' }]) {
    await assert.rejects(register('painter', 'password123', async () => Response.json(body)), /서버 응답/);
  }
});
