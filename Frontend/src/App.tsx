import { useState, type FormEvent } from 'react';
import { login, type AuthToken } from './auth';

export default function App() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [token, setToken] = useState<AuthToken | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isLoading) return;
    if (!username.trim()) {
      setMessage('아이디를 입력해 주세요.');
      event.currentTarget.querySelector<HTMLInputElement>('#username')?.focus();
      return;
    }

    if (window.location.protocol === 'file:') {
      setMessage('화면 미리보기입니다. 실제 로그인은 서비스 주소에서 이용해 주세요.');
      return;
    }
    setIsLoading(true);
    setMessage('');
    try {
      setToken(await login(username, password));
      setPassword('');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '로그인에 실패했습니다.');
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login" aria-labelledby="login-title">
        <h1 id="login-title">로그인</h1>
        {token ? (
          <div className="login-result">
            <p role="status">로그인되었습니다.</p>
            <button type="button" onClick={() => { setToken(null); setMessage(''); }}>로그아웃</button>
          </div>
        ) : (
        <form onSubmit={handleSubmit} onChange={() => setMessage('')} aria-busy={isLoading}>
          <div className="field">
            <label htmlFor="username">아이디</label>
            <input
              id="username"
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              required
              pattern="[A-Za-z0-9_]{3,20}"
              minLength={3}
              maxLength={20}
              title="영문·숫자·밑줄 3~20자"
              disabled={isLoading}
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="password">비밀번호</label>
            <input
              id="password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
              minLength={8}
              maxLength={32}
              disabled={isLoading}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          <button type="submit" disabled={isLoading}>{isLoading ? '로그인 중…' : '확인'}</button>
          <p className="message" role="status">{message}</p>
        </form>
        )}
      </section>
    </main>
  );
}
