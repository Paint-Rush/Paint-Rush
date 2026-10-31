import { useState, type FormEvent } from 'react';
import { login, register, type AuthToken } from './auth';
import GameShell from './game/GameShell';

export default function App() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [token, setToken] = useState<AuthToken | null>(null);
  const [isRegister, setIsRegister] = useState(false);
  const [preview, setPreview] = useState(false);

  function changePage() {
    setIsRegister(!isRegister);
    setUsername('');
    setPassword('');
    setMessage('');
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isLoading) return;
    if (!username.trim()) {
      setMessage('아이디를 입력해 주세요.');
      event.currentTarget.querySelector<HTMLInputElement>('#username')?.focus();
      return;
    }

    if (window.location.protocol === 'file:') {
      setMessage(`화면 미리보기입니다. 실제 ${isRegister ? '회원가입' : '로그인'}은 서비스 주소에서 이용해 주세요.`);
      return;
    }
    setIsLoading(true);
    setMessage('');
    try {
      if (isRegister) {
        const user = await register(username, password);
        setUsername(user.username);
        setIsRegister(false);
        setMessage('회원가입이 완료되었습니다. 비밀번호를 입력해 로그인해 주세요.');
      } else {
        setToken(await login(username, password));
      }
      setPassword('');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '요청에 실패했습니다.');
    } finally {
      setIsLoading(false);
    }
  }

  if (token || preview) return <GameShell token={token} username={username} onExit={() => { setToken(null); setPreview(false); setMessage(''); }} />;

  return (
    <main className="login-page">
      <section className="login" aria-labelledby="login-title">
        <h1 id="login-title">{isRegister ? '회원가입' : '로그인'}</h1>
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
              autoComplete={isRegister ? 'new-password' : 'current-password'}
              required
              minLength={8}
              maxLength={32}
              disabled={isLoading}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          <button type="submit" disabled={isLoading}>{isLoading ? (isRegister ? '가입 중…' : '로그인 중…') : '확인'}</button>
          <p className="account-switch">
            {isRegister ? '이미 계정이 있다면? ' : '계정이 없다면? '}
            <button className="text-button" type="button" disabled={isLoading} onClick={changePage}>
              {isRegister ? '로그인' : '회원가입'}
            </button>
          </p>
          <p className="message" role="status">{message}</p>
          <button className="text-button" type="button" disabled={isLoading} onClick={() => setPreview(true)}>게임 화면 체험하기 →</button>
        </form>
      </section>
    </main>
  );
}
