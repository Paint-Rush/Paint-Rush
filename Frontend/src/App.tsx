import { useEffect, useState, useSyncExternalStore } from 'react';
import type { AuthToken } from './services/auth';
import { GameSocket, gameSocketUrl, type SocketLike } from './services/gameSocket';
import StartPage from './pages/StartPage';
import AuthPage from './pages/AuthPage';
import LobbyPage from './pages/LobbyPage';
import CharacterSelectPage from './pages/CharacterSelectPage';
import MatchmakingPage from './pages/MatchmakingPage';
import GamePage from './pages/GamePage';

export default function App() {
  const [token, setToken] = useState<AuthToken | null>(null);
  const [isStarted, setIsStarted] = useState(false);
  const [isSelectingCharacter, setIsSelectingCharacter] = useState(false);
  const [matchCharacterId, setMatchCharacterId] = useState<string | null>(null);
  const [gameSocket] = useState(() => new GameSocket((url) => new WebSocket(url) as unknown as SocketLike, gameSocketUrl(window.location)));
  const match = useSyncExternalStore(gameSocket.subscribe, gameSocket.getState);

  function goLobby() {
    setIsSelectingCharacter(false);
    setMatchCharacterId(null);
  }

  function startMatch(characterId: string) {
    if (!token) return;
    setMatchCharacterId(characterId);
    gameSocket.start(token.access_token, characterId);
  }

  function login(next: AuthToken) {
    setToken(next);
    gameSocket.resume(next.access_token);
  }

  function leaveGame() {
    gameSocket.release();
    setMatchCharacterId(null);
  }

  function backToCharacterSelect() {
    gameSocket.release();
    setMatchCharacterId(null);
  }

  useEffect(() => {
    if (matchCharacterId !== null && (match.phase === 'cancelled' || match.phase === 'idle')) backToCharacterSelect();
  }, [match.phase, matchCharacterId]);

  if (!isStarted) return <StartPage onStart={() => setIsStarted(true)} />;

  if (!token) return <AuthPage onLogin={login} onBack={() => setIsStarted(false)} />;

  const inRoom = match.roomId !== null && match.phase !== 'idle';
  const inGame = inRoom && match.room !== null
    && (match.phase === 'started' || match.phase === 'finished' || (match.phase === 'error' && match.room.status !== 'READY'));

  if (inGame) return <GamePage session={match} socket={gameSocket} onExit={leaveGame} />;

  if ((isSelectingCharacter && matchCharacterId !== null) || inRoom) {
    return (
      <MatchmakingPage
        session={match}
        onCancel={() => gameSocket.cancel()}
        onRetry={() => gameSocket.retry()}
        onBack={backToCharacterSelect}
        onLeaveRoom={() => gameSocket.leaveRoom()}
      />
    );
  }

  if (isSelectingCharacter) {
    return <CharacterSelectPage token={token} confirmedCharacterId={matchCharacterId} onHome={goLobby} onPlay={startMatch} />;
  }

  return <LobbyPage token={token} onGame={() => setIsSelectingCharacter(true)} onLogout={() => { gameSocket.close(); goLobby(); setToken(null); }} />;
}
