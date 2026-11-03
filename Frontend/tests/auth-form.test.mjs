import assert from 'node:assert/strict';
import test from 'node:test';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
import { submitAuthForm } from '../src/services/auth.ts';

const values = { username: 'Painter123', password: 'password123', passwordConfirm: 'password123' };

function recordRequests(response) {
  const calls = [];
  const request = async (url, options) => {
    calls.push({ url, body: JSON.parse(options.body) });
    return typeof response === 'function' ? response() : response.clone();
  };
  return { calls, request };
}

test('login form sends only username and password and returns the token', async () => {
  const { calls, request } = recordRequests(Response.json({ access_token: 'test-token', token_type: 'bearer' }));
  const result = await submitAuthForm('login', { ...values, passwordConfirm: '' }, request);
  assert.deepEqual(result, { status: 'logged-in', token: { access_token: 'test-token', token_type: 'bearer' } });
  assert.deepEqual(calls, [{ url: '/api/v1/auth/login', body: { username: 'Painter123', password: 'password123' } }]);
});

test('register form sends the matching password without the confirmation field', async () => {
  const { calls, request } = recordRequests(Response.json({ id: 7, username: 'painter123' }, { status: 201 }));
  const result = await submitAuthForm('register', values, request);
  assert.deepEqual(result, { status: 'registered', username: 'painter123' });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/v1/auth/register');
  assert.deepEqual(calls[0].body, { username: 'Painter123', password: 'password123' });
  assert.deepEqual(Object.keys(calls[0].body), ['username', 'password']);
});

test('register form blocks the request when the passwords differ', async () => {
  for (const passwordConfirm of ['password124', 'password123 ', '', 'PASSWORD123']) {
    const { calls, request } = recordRequests(() => assert.fail('must not send mismatched passwords'));
    const result = await submitAuthForm('register', { ...values, passwordConfirm }, request);
    assert.deepEqual(result, { status: 'error', message: '비밀번호가 일치하지 않습니다.' });
    assert.equal(calls.length, 0);
  }
});

test('register form keeps the existing validation and 409 handling', async () => {
  const invalid = await submitAuthForm('register', { username: 'ab', password: 'password123', passwordConfirm: 'password123' }, async () => assert.fail('must not send invalid credentials'));
  assert.equal(invalid.status, 'error');
  assert.match(invalid.message, /3~20자/);
  const duplicate = await submitAuthForm('register', values, async () => new Response('', { status: 409 }));
  assert.equal(duplicate.status, 'error');
  assert.match(duplicate.message, /이미 사용 중/);
});

test('login form keeps 401 and connection failure handling', async () => {
  const unauthorized = await submitAuthForm('login', values, async () => new Response('', { status: 401 }));
  assert.equal(unauthorized.status, 'error');
  assert.match(unauthorized.message, /올바르지/);
  const offline = await submitAuthForm('login', values, async () => { throw new TypeError('fetch failed'); });
  assert.equal(offline.status, 'error');
  assert.match(offline.message, /연결할 수 없습니다/);
});

test('auth page renders the login layout first', async () => {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const result = await build({
    stdin: {
      contents: "import { createElement } from 'react'; import { renderToStaticMarkup } from 'react-dom/server'; import AuthPage from './src/pages/AuthPage'; export const html = renderToStaticMarkup(createElement(AuthPage, { onLogin() {}, onBack() {} }));",
      resolveDir: root,
      loader: 'tsx',
    },
    bundle: true,
    write: false,
    format: 'cjs',
    platform: 'node',
    jsx: 'automatic',
    loader: { '.css': 'empty' },
    define: { 'process.env.NODE_ENV': '"production"' },
    logLevel: 'silent',
  });
  const dir = await mkdtemp(join(tmpdir(), 'paint-rush-auth-'));
  try {
    const file = join(dir, 'auth-page.cjs');
    await writeFile(file, result.outputFiles[0].text);
    const { html } = (await import(pathToFileURL(file).href)).default;
    assert.match(html, /aria-label="SIGN IN"/);
    assert.equal(html.match(/<input/g).length, 2);
    assert.match(html, /id="username"/);
    assert.match(html, /id="password"[^>]*type="password"/);
    assert.doesNotMatch(html, /password-confirm/);
    assert.match(html, /class="auth-submit"[^>]*>로그인</);
    assert.match(html, /계정이 없으신가요\?<button[^>]*>회원가입</);
  } finally {
    await rm(dir, { recursive: true, force: true });
  }
});
