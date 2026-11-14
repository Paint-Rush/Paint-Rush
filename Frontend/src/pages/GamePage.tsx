import { useEffect, useMemo, useRef, useState } from 'react';
import BattleScene from '../game/BattleScene';
import type { CanvasControls } from '../game/GameCanvas';
import type { Point } from '../game/model';
import { toGameState } from '../game/roomView';
import { loadCards, type CardCatalog } from '../services/cards';
import type { GameSocket } from '../services/gameSocket';
import type { MatchSession } from '../types/game';

const CARD_TIMEOUT = 5000;

interface GamePageProps {
  session: MatchSession;
  socket: Pick<GameSocket, 'onEvent' | 'playCard' | 'castUltimate' | 'sync' | 'retry' | 'leaveRoom'>;
  onExit: () => void;
  initialCards?: CardCatalog | null;
}

export default function GamePage({ session, socket, onExit, initialCards = null }: GamePageProps) {
  const [cards, setCards] = useState<CardCatalog | null>(initialCards);
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const camera = useRef<CanvasControls>(null);
  const pendingTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const userId = useRef(session.userId);
  userId.current = session.userId;

  const state = useMemo(() => {
    if (!session.room || session.userId === null) return null;
    try {
      return toGameState(session.room, session.userId);
    } catch {
      return null;
    }
  }, [session.room, session.userId]);

  function settle() {
    if (pendingTimer.current) clearTimeout(pendingTimer.current);
    pendingTimer.current = null;
    setPending(false);
  }

  useEffect(() => {
    if (cards) return;
    let current = true;
    loadCards()
      .then((catalog) => { if (current) setCards(catalog); })
      .catch((reason: unknown) => { if (current) setMessage(reason instanceof Error ? reason.message : '카드 목록을 불러오지 못했습니다.'); });
    return () => { current = false; };
  }, []);

  useEffect(() => {
    socket.sync();
    const unsubscribe = socket.onEvent((event) => {
      if (event.type === 'hand_updated') {
        settle();
        socket.sync();
      } else if (event.type === 'ultimate_activated' && event.user_id === userId.current) {
        settle();
        socket.sync();
      } else if (event.type === 'error' && pendingTimer.current) {
        settle();
        setMessage(typeof event.message === 'string' ? event.message : '카드를 사용할 수 없습니다.');
        socket.sync();
      }
    });
    return () => {
      unsubscribe();
      if (pendingTimer.current) clearTimeout(pendingTimer.current);
    };
  }, [socket]);

  function submitCard(index: number, target?: Point) {
    if (pending) return;
    if (!socket.playCard(index, target)) {
      setMessage('지금은 카드를 사용할 수 없습니다.');
      return;
    }
    wait('카드 처리 응답이 지연되고 있습니다. 서버 상태를 확인합니다.');
  }

  function submitUltimate(target?: Point) {
    if (pending || pendingTimer.current) return;
    if (!socket.castUltimate(target)) {
      setMessage('지금은 궁극기를 사용할 수 없습니다.');
      return;
    }
    wait('궁극기 처리 응답이 지연되고 있습니다. 서버 상태를 확인합니다.');
  }

  function wait(delayed: string) {
    setMessage('');
    setPending(true);
    pendingTimer.current = setTimeout(() => {
      pendingTimer.current = null;
      setPending(false);
      setMessage(delayed);
      socket.sync();
    }, CARD_TIMEOUT);
  }

  function reconnect() {
    if (!socket.retry()) onExit();
  }

  const finished = session.phase === 'finished';
  const disconnected = !session.connected || session.phase === 'error';
  const remainingMs = finished ? 0 : (session.room?.remainingSeconds ?? 0) * 1000;

  return (
    <div className="game-app phase-playing">
      {state ? (
        <BattleScene
          state={state}
          cards={cards}
          pending={pending}
          disconnected={disconnected}
          remainingMs={remainingMs}
          onUseCard={submitCard}
          onUseUltimate={submitUltimate}
          onReconnect={reconnect}
          cameraRef={camera}
        />
      ) : (
        <p className="game-notice" role="alert">게임 상태를 불러오는 중입니다.</p>
      )}
      {session.error ? (
        <div className="game-notice" role="alert">
          <span>{session.error.message}</span>
          <button type="button" onClick={onExit}>로비로</button>
        </div>
      ) : message ? (
        <div className="game-notice" role="status">
          <span>{message}</span>
          <button type="button" aria-label="안내 닫기" onClick={() => setMessage('')}>×</button>
        </div>
      ) : null}
    </div>
  );
}
