import { useState } from 'react';
import type { AuthToken } from './services/auth';
import StartPage from './pages/StartPage';
import AuthPage from './pages/AuthPage';
import LobbyPage from './pages/LobbyPage';

export default function App() {
  const [token, setToken] = useState<AuthToken | null>(null);
  const [isStarted, setIsStarted] = useState(false);

  if (!isStarted) return <StartPage onStart={() => setIsStarted(true)} />;

  if (!token) return <AuthPage onLogin={setToken} onBack={() => setIsStarted(false)} />;

  return <LobbyPage token={token} onLogout={() => setToken(null)} />;
}
