import { useEffect, useState, type CSSProperties } from 'react';
import type { MatchPhase, MatchPlayer, MatchSession } from '../types/game';
import { readySecondsLeft } from '../services/gameSocket';
import '../styles/matchmaking.css';

function area(width: number, height: number) {
  return (x: number, y: number, w: number, h: number): CSSProperties => ({
    left: `${(x / width) * 100}%`,
    top: `${(y / height) * 100}%`,
    width: `${(w / width) * 100}%`,
    height: `${(h / height) * 100}%`,
  });
}

const waitingArea = area(1808, 1018);
const foundArea = area(1805, 1013);

const QUEUE_LABELS: Partial<Record<MatchPhase, string>> = {
  idle: '매칭 준비 중',
  connecting: '게임 서버에 연결 중',
  authenticating: '로그인 확인 중',
  joining: '매칭 신청 중',
  waiting: '상대를 찾는 중',
  cancelling: '매칭 취소 확인 중',
  cancelled: '매칭이 취소되었습니다',
  error: '매칭을 진행할 수 없습니다',
};

const PLAYER_SLOTS = [
  { label: foundArea(578, 62, 262, 84) },
  { label: foundArea(916, 52, 250, 92) },
  { label: foundArea(470, 585, 240, 70), veil: [foundArea(0, 525, 712, 488), foundArea(712, 700, 190, 313)] },
  { label: foundArea(1100, 560, 230, 70), veil: [foundArea(1095, 525, 710, 488), foundArea(902, 700, 193, 313)] },
];

const COLOR_NAMES: Record<string, string> = { RED: '빨강', BLUE: '파랑', GREEN: '초록', YELLOW: '노랑' };

const ROOM_PHASES: MatchPhase[] = ['matched', 'ready', 'started', 'finished'];

interface MatchmakingPageProps {
  session: MatchSession;
  now?: number;
  onCancel: () => void;
  onRetry: () => void;
  onBack: () => void;
  onLeaveRoom: () => void;
}

function PlayerLabel({ player, isMe }: { player: MatchPlayer; isMe: boolean }) {
  return (
    <>
      <strong className="match-player-name" title={player.characterName ?? player.characterId}>{player.characterName ?? player.characterId}</strong>
      <span className="match-player-meta">
        <i className={`match-color is-${player.color.toLowerCase()}`} aria-hidden="true" />
        {COLOR_NAMES[player.color] ?? player.color} · 플레이어 #{player.userId}{isMe ? ' (나)' : ''}
      </span>
    </>
  );
}

export default function MatchmakingPage({ session, now, onCancel, onRetry, onBack, onLeaveRoom }: MatchmakingPageProps) {
  const [clock, setClock] = useState(() => now ?? Date.now());
  const { phase, error } = session;
  const inRoom = ROOM_PHASES.includes(phase) || (phase === 'error' && session.roomId !== null);

  useEffect(() => {
    if (now !== undefined || phase !== 'ready') return;
    const timer = window.setInterval(() => setClock(Date.now()), 250);
    return () => window.clearInterval(timer);
  }, [phase, now]);

  const readyLeft = readySecondsLeft(session, now ?? clock);
  const canRetry = phase === 'error' && error?.code !== 'auth_failed';

  if (!inRoom) {
    return (
      <main className="match-page is-waiting" style={{ '--art-ratio': 1808 / 1018, '--art-base': 1808 } as CSSProperties}>
        <div className="match-art">
          <img src="storyboard/matching.png" alt="" width={1808} height={1018} />
          <span className="match-dots" style={waitingArea(984, 806, 66, 38)} aria-hidden="true">
            {phase === 'waiting' || phase === 'joining' ? <><i /><i /><i /></> : null}
          </span>
          <section className="match-card match-queue" style={waitingArea(1086, 748, 520, 168)} aria-live="polite">
            <p className="match-title" role="status">{QUEUE_LABELS[phase] ?? '매칭 준비 중'}</p>
            <p className={error ? 'match-detail is-error' : 'match-detail'}>
              {error
                ? error.message
                : phase === 'waiting' && session.queue
                  ? `대기열 ${session.queue.waiting}명 · ${session.queue.matchSize}명이 모이면 시작`
                  : session.connected ? '서버 연결됨' : '서버 연결 대기'}
            </p>
            <div className="match-actions">
              {phase === 'waiting' || phase === 'cancelling' || phase === 'joining' ? (
                <button type="button" onClick={onCancel} disabled={phase !== 'waiting'}>{phase === 'cancelling' ? '취소 중…' : '매칭 취소'}</button>
              ) : null}
              {canRetry ? <button type="button" onClick={onRetry}>다시 시도</button> : null}
              {phase === 'error' || phase === 'cancelled' || phase === 'idle' ? <button type="button" onClick={onBack}>캐릭터 선택으로</button> : null}
            </div>
          </section>
        </div>
      </main>
    );
  }

  const status = phase === 'error'
    ? { title: '연결 끊김', detail: error?.message ?? '' }
    : phase === 'finished'
      ? { title: '경기 종료', detail: '이전 경기가 끝났습니다. 정리한 뒤 다시 매칭할 수 있습니다.' }
      : phase === 'started'
        ? { title: '경기 시작!', detail: '인게임 화면 준비 중입니다. 서버 연결은 유지됩니다.' }
        : { title: '매칭 완료', detail: '서버가 경기 준비 시간을 보내고 있습니다.' };

  return (
    <main className="match-page is-found" style={{ '--art-ratio': 1805 / 1013, '--art-base': 1805 } as CSSProperties}>
      <div className="match-art">
        <img src="storyboard/match-found.png" alt="" width={1805} height={1013} />
        {PLAYER_SLOTS.map((slot, index) => {
          const player = session.players[index];
          if (!player) return slot.veil?.map((box, part) => <div key={`${index}-${part}`} className="match-veil" style={box} />) ?? null;
          return (
            <p key={player.userId} className={player.userId === session.userId ? 'match-player is-me' : 'match-player'} style={slot.label}>
              <PlayerLabel player={player} isMe={player.userId === session.userId} />
            </p>
          );
        })}
        <section className="match-ready" style={foundArea(566, 344, 180, 154)} aria-live="polite">
          <span className="match-ready-label">{phase === 'started' ? 'START' : phase === 'finished' ? 'END' : 'READY'}</span>
          <strong className="match-ready-count" role="timer">
            {phase === 'started' ? 'GO' : phase === 'ready' || phase === 'matched' ? (readyLeft === null ? '…' : `${readyLeft}`) : '-'}
          </strong>
          <span className="match-ready-unit">{phase === 'ready' && readyLeft !== null ? '초 후 시작' : ''}</span>
        </section>
        <section className="match-card match-status" style={foundArea(600, 812, 606, 150)}>
          <p className="match-title" role="status">{status.title}</p>
          <p className={phase === 'error' ? 'match-detail is-error' : 'match-detail'}>{status.detail}</p>
          <p className="match-room" title={session.roomId ?? ''}>방 {session.roomId ? session.roomId.slice(0, 8) : '-'} · {session.connected ? '연결 유지 중' : '연결 끊김'}</p>
          <div className="match-actions">
            {canRetry ? <button type="button" onClick={onRetry}>다시 연결</button> : null}
            {phase === 'finished' ? <button type="button" onClick={onLeaveRoom}>경기 정리</button> : null}
            {phase === 'error' ? <button type="button" onClick={onBack}>캐릭터 선택으로</button> : null}
          </div>
        </section>
      </div>
    </main>
  );
}
