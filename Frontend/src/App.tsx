import { useState } from 'react';
import type { AuthToken } from './services/auth';
import StartPage from './pages/StartPage';
import AuthPage from './pages/AuthPage';
import LobbyPage from './pages/LobbyPage';
import CharacterSelectPage from './pages/CharacterSelectPage';

export default function App() {
  const [token, setToken] = useState<AuthToken | null>(null);
  const [isStarted, setIsStarted] = useState(false);
  const [isSelectingCharacter, setIsSelectingCharacter] = useState(false);
  const [matchCharacterId, setMatchCharacterId] = useState<string | null>(null);

  function goLobby() {
    setIsSelectingCharacter(false);
    setMatchCharacterId(null);
  }

  if (!isStarted) return <StartPage onStart={() => setIsStarted(true)} />;

  if (!token) return <AuthPage onLogin={setToken} onBack={() => setIsStarted(false)} />;

  if (isSelectingCharacter) {
    return <CharacterSelectPage token={token} confirmedCharacterId={matchCharacterId} onHome={goLobby} onPlay={setMatchCharacterId} />;
  }

  return <LobbyPage token={token} onGame={() => setIsSelectingCharacter(true)} onLogout={() => { goLobby(); setToken(null); }} />;
}
