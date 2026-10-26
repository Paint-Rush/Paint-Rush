import { useState, type FormEvent } from 'react';

export default function App() {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!username.trim()) {
      setMessage('아이디를 입력해 주세요.');
      event.currentTarget.querySelector<HTMLInputElement>('#username')?.focus();
      return;
    }

    // Replace this notice with the authentication request once the API is ready.
    setMessage('입력이 완료되었습니다. 실제 로그인은 서버 연결 후 이용할 수 있습니다.');
  }

  return (
    <main className="login-page">
      <section className="login" aria-labelledby="login-title">
        <h1 id="login-title">로그인</h1>
        <form onSubmit={handleSubmit} onChange={() => setMessage('')}>
          <div className="field">
            <label htmlFor="username">아이디</label>
            <input
              id="username"
              name="username"
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              required
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
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </div>
          <button type="submit">확인</button>
          <p className="message" role="status">{message}</p>
        </form>
      </section>
    </main>
  );
}
