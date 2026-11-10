import { useEffect, useRef, useState } from 'react';
import type { GameSocket } from '../services/gameSocket';
import { ResultExit, type ResultExitState } from '../services/resultExit';
import type { GameResultRow, MatchPlayer, MatchSession } from '../types/game';
import '../styles/result.css';

const COLOR_NAMES: Record<string, string> = { RED: '빨강', BLUE: '파랑', GREEN: '초록', YELLOW: '노랑' };
const STATS: { key: keyof GameResultRow; label: string }[] = [
  { key: 'totalPainted', label: '누적 채색' },
  { key: 'cardsUsed', label: '카드' },
  { key: 'costUsed', label: '코스트' },
  { key: 'kills', label: '처치' },
  { key: 'deaths', label: '사망' },
];

interface ResultPageProps {
  session: MatchSession;
  socket: Pick<GameSocket, 'onEvent' | 'leaveRoom' | 'retry'>;
  onLeft: () => void;
}

function playerName(player: MatchPlayer | undefined, userId: number) {
  return player ? player.characterName ?? player.characterId : `플레이어 ${userId}`;
}

function Silhouette() {
  return (
    <svg viewBox="0 0 120 150" aria-hidden="true">
      <circle cx="60" cy="44" r="26" />
      <path d="M14 150 C16 106 34 86 60 86 C86 86 104 106 106 150" />
    </svg>
  );
}

export default function ResultPage({ session, socket, onLeft }: ResultPageProps) {
  const [exit, setExit] = useState<ResultExitState>({ leaving: false, message: '' });
  const [notice, setNotice] = useState('');
  const flow = useRef<ResultExit | null>(null);
  const left = useRef(onLeft);
  left.current = onLeft;

  useEffect(() => {
    const current = new ResultExit(socket, setExit, () => left.current());
    flow.current = current;
    return () => {
      current.dispose();
      if (flow.current === current) flow.current = null;
    };
  }, [socket]);

  useEffect(() => {
    if (!session.connected) flow.current?.disconnected();
  }, [session.connected]);

  function leave() {
    setNotice('');
    flow.current?.leave();
  }

  function reconnect() {
    flow.current?.clearMessage();
    setNotice(socket.retry() ? '' : '지금은 다시 연결할 수 없습니다.');
  }

  const result = session.result && session.result.roomId === session.roomId ? session.result : null;
  const players = new Map<number, MatchPlayer>((session.room?.players ?? session.players).map((player) => [player.userId, player]));
  const rows = result ? [...result.results].sort((a, b) => a.rank - b.rank) : null;
  const mine = rows?.find((row) => row.userId === session.userId) ?? null;
  const winner = result ? players.get(result.winnerId) : undefined;
  const winnerRow = rows?.find((row) => row.userId === result?.winnerId) ?? null;
  const tied = rows !== null && rows.length > 1 && rows[0].paintedTiles !== null && rows[0].paintedTiles === rows[1].paintedTiles;
  const outcome = !rows ? '결과 집계 중' : mine ? (mine.isWinner ? 'WIN' : 'LOSE') : '경기 종료';
  const lost = !session.connected || session.phase === 'error';
  const reconnecting = session.phase === 'connecting' || session.phase === 'authenticating';
  const entries = rows ?? [...players.values()].map((player) => ({ userId: player.userId }));
  const message = exit.message || notice || (session.error?.message ?? (lost && !reconnecting ? '게임 서버 연결이 끊겼습니다. 다시 연결하면 결과를 복원합니다.' : ''));

  return (
    <main className={`result-page players-${entries.length}`}>
      <div className="result-stage">
        <svg className="result-scribble is-left" viewBox="0 0 300 200" aria-hidden="true">
          <path d="M0 150 C40 120 50 175 80 140 C110 100 90 60 130 70 C170 80 150 20 190 30 C230 40 220 0 250 5" />
        </svg>
        <svg className="result-scribble is-right" viewBox="0 0 300 200" aria-hidden="true">
          <path d="M20 10 C60 0 70 60 110 40 C150 20 160 90 200 70 C240 50 230 130 270 120 C290 115 300 160 280 190" />
        </svg>

        <header className="result-head" aria-live="polite">
          <p className="result-kicker">경기 종료</p>
          <h1 className={`result-outcome is-${outcome === 'WIN' ? 'win' : outcome === 'LOSE' ? 'lose' : 'pending'}`} role="status">{outcome}</h1>
          <p className="result-sub">
            {rows
              ? `${mine ? `내 순위 ${mine.rank}위 · ` : ''}승자 ${playerName(winner, result!.winnerId)}${tied ? ' · 점령 타일 동점, 서버 판정 우선순위 적용' : ''}`
              : '서버에서 경기 결과를 집계하고 있습니다.'}
          </p>
        </header>

        <section className="result-board" aria-label="경기 결과">
          <i className="result-drip is-top" aria-hidden="true" />
          <i className="result-drip is-bottom" aria-hidden="true" />

          <ol className="result-ranks" aria-label="참가자 순위">
            {entries.map((entry) => {
              const row = rows ? entry as GameResultRow : null;
              const player = players.get(entry.userId);
              const isMe = entry.userId === session.userId;
              const color = player?.color.toLowerCase() ?? 'none';
              return (
                <li key={entry.userId} className={`result-row${isMe ? ' is-me' : ''}${row?.isWinner ? ' is-winner' : ''}`} data-user={entry.userId}>
                  <span className={`result-avatar is-${color}`}><Silhouette /></span>
                  <div className="result-bar">
                    <strong className="result-rank">{row ? `${row.rank}위` : '…'}</strong>
                    <span className="result-who">
                      <b title={playerName(player, entry.userId)}>{playerName(player, entry.userId)}</b>
                      <small>
                        {player ? `${COLOR_NAMES[player.color] ?? player.color} · ` : ''}플레이어 #{entry.userId}{isMe ? ' (나)' : ''}
                      </small>
                    </span>
                    {row ? (
                      <span className="result-stats">
                        {STATS.filter(({ key }) => row[key] !== null).map(({ key, label }) => (
                          <span key={key}>{label} <b>{row[key] as number}</b></span>
                        ))}
                      </span>
                    ) : <span className="result-stats is-pending">집계 중</span>}
                    <span className="result-tiles">
                      <span>{row && row.paintedTiles !== null ? <><b>{row.paintedTiles}</b>칸</> : '-'}</span>
                      <small>최종 점령</small>
                    </span>
                  </div>
                </li>
              );
            })}
          </ol>

          <aside className="result-winner" aria-label="우승자">
            <svg className="result-crown" viewBox="0 0 80 60" aria-hidden="true">
              <path d="M8 56 L4 24 L24 40 L40 18 L56 40 L76 24 L72 56 Z" />
              <path d="M28 12 L24 4 M52 12 L56 4 M40 10 L40 1" />
            </svg>
            <h2>
              <svg viewBox="0 0 40 60" aria-hidden="true"><path d="M34 58 C10 50 4 26 16 2 M14 46 l-10 -2 M10 34 l-9 -4 M10 22 l-8 -6 M14 11 l-6 -7" /></svg>
              우승자
              <svg viewBox="0 0 40 60" aria-hidden="true"><path d="M6 58 C30 50 36 26 24 2 M26 46 l10 -2 M30 34 l9 -4 M30 22 l8 -6 M26 11 l6 -7" /></svg>
            </h2>
            <figure className={`result-portrait is-${winner?.color.toLowerCase() ?? 'none'}`}>
              {result ? <Silhouette /> : <span className="result-wait">?</span>}
            </figure>
            <p className="result-winner-name">
              {result ? (
                <>
                  <b title={playerName(winner, result.winnerId)}>{playerName(winner, result.winnerId)}</b>
                  <small>플레이어 #{result.winnerId}{result.winnerId === session.userId ? ' (나)' : ''}{winnerRow?.paintedTiles != null ? ` · ${winnerRow.paintedTiles}칸` : ''}</small>
                </>
              ) : <small>결과 집계 중…</small>}
            </p>
          </aside>
        </section>

        <footer className="result-foot">
          <div className="result-actions">
            {lost && session.error?.code !== 'session_replaced' ? (
              <button type="button" className="result-button" onClick={reconnect} disabled={reconnecting}>{reconnecting ? '다시 연결 중…' : '다시 연결'}</button>
            ) : null}
            <button type="button" className="result-button is-back" onClick={leave} disabled={exit.leaving || !result || lost}>
              <span aria-hidden="true">◀</span> {exit.leaving ? '경기 정리 중…' : '로비로 돌아가기'}
            </button>
          </div>
          <p className={message ? 'result-message is-error' : 'result-message'} role={message ? 'alert' : undefined}>
            {message || (result ? '경험치 보상 시스템 준비 중' : '결과가 확정되면 로비로 돌아갈 수 있습니다.')}
          </p>
        </footer>
      </div>
    </main>
  );
}
