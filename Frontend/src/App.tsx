import { useState } from 'react';
import type { AuthToken } from './services/auth';
import StartPage from './pages/StartPage';
import AuthPage from './pages/AuthPage';

export default function App() {
  const [token, setToken] = useState<AuthToken | null>(null);
  const [isStarted, setIsStarted] = useState(false);

  if (!isStarted) return <StartPage onStart={() => setIsStarted(true)} />;

  if (!token) return <AuthPage onLogin={setToken} onBack={() => setIsStarted(false)} />;

  return (
    <main className="login-page">
      <section className="login" aria-labelledby="login-title">
        <h1 id="login-title">로그인</h1>
        <div className="login-result">
          <p role="status">로그인되었습니다.</p>
          <button type="button" onClick={() => setToken(null)}>로그아웃</button>
        </div>
      </section>
    </main>
  );
}
