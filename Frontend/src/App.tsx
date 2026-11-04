import { useState } from 'react';
import type { AuthToken } from './services/auth';
import StartPage from './pages/StartPage';
import AuthPage from './pages/AuthPage';
import GameShell from './game/GameShell';

export default function App() {
  const [token, setToken] = useState<AuthToken | null>(null);
  const [isStarted, setIsStarted] = useState(false);
  const [preview, setPreview] = useState(false);
  const [username, setUsername] = useState('');

  if (!isStarted) return <StartPage onStart={() => setIsStarted(true)} />;

  if (!token && !preview) return <AuthPage onLogin={(next, name) => { setToken(next); setUsername(name); }} onPreview={() => setPreview(true)} onBack={() => setIsStarted(false)} />;

  return <GameShell token={token} username={username} onExit={() => { setToken(null); setPreview(false); }} />;
}
