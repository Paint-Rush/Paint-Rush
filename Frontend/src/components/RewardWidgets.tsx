import type { MatchRewardState } from '../services/reward';
import '../styles/rank.css';

function format(value: number): string {
  return value.toLocaleString('ko-KR');
}

export function MatchRewardPanel({ state, celebrate = false }: { state: MatchRewardState; celebrate?: boolean }) {
  const reward = state.status === 'applied' ? state.reward : null;
  return (
    <aside
      className={`match-reward is-${state.status}${reward?.leveledUp ? ' is-level-up' : ''}${celebrate && reward?.leveledUp ? ' is-celebrating' : ''}`}
      aria-label="경기 보상"
      aria-live="polite"
    >
      <h2>REWARD</h2>
      {state.status === 'pending' ? (
        <p className="match-rank-note">보상 정산 확인 중…</p>
      ) : state.status === 'unrewarded' ? (
        <p className="match-rank-note">이번 경기는 보상 미적용입니다.</p>
      ) : state.status === 'error' ? (
        <p className="match-rank-note is-error">{state.message}</p>
      ) : reward ? (
        <>
          <p className="match-rank-place">경기 {reward.placement}위</p>
          <div className="match-reward-line is-gold">
            <strong>GOLD +{format(reward.goldEarned)}</strong>
            <small>{format(reward.goldBefore)} → {format(reward.goldAfter)} GOLD</small>
          </div>
          <div className="match-reward-line is-exp">
            <strong>EXP +{format(reward.experienceEarned)}</strong>
            <small>{format(reward.experienceBefore)} → {format(reward.experienceAfter)} EXP</small>
          </div>
          {reward.leveledUp ? (
            <div className="match-level-up" role="status">
              <strong>LEVEL UP!</strong>
              <span>LEVEL <b>{reward.levelBefore}</b> → LEVEL <b className="match-level-new">{reward.levelAfter}</b></span>
            </div>
          ) : (
            <p className="match-reward-level">LEVEL {reward.levelAfter} 유지</p>
          )}
        </>
      ) : null}
    </aside>
  );
}
