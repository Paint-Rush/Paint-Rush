import {
  formatRp,
  rankProgress,
  tierLabel,
  tierMove,
  tierName,
  type MatchRankState,
  type MyRank,
  type RankChange,
  type RankingEntry,
  type TierName,
} from '../services/rank';
import '../styles/rank.css';

const PLACEMENT_LABELS = ['', '1위', '2위', '3위', '4위'];

function changeClass(value: number): string {
  return value > 0 ? 'is-up' : value < 0 ? 'is-down' : 'is-even';
}

export function RankBadge({ tier, division = null, size = 'md' }: { tier: TierName; division?: number | null; size?: 'sm' | 'md' | 'lg' }) {
  return (
    <span className={`rank-badge is-${tier.toLowerCase()} is-${size}`} title={tierLabel(tier, division)}>
      <svg viewBox="0 0 40 44" aria-hidden="true">
        <path className="rank-badge-shield" d="M20 2 L37 9 L35 27 C33 35 27 40 20 42 C13 40 7 35 5 27 L3 9 Z" />
        {tier === 'CHALLENGER' ? <path className="rank-badge-mark" d="M9 18 L14 24 L20 12 L26 24 L31 18 L29 31 L11 31 Z" /> : null}
        {tier === 'MASTER' ? <path className="rank-badge-mark" d="M12 30 L12 15 L20 24 L28 15 L28 30" /> : null}
      </svg>
      {division !== null ? <b>{division}</b> : null}
    </span>
  );
}

export function RankProgressBar({ rank }: { rank: MyRank }) {
  const progress = rankProgress(rank);
  return (
    <div className={`rank-progress${progress.percent === null ? ' is-open' : ''}`}>
      {progress.percent !== null ? (
        <span
          className="rank-progress-track"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(progress.percent)}
          aria-label="RP 진행도"
        >
          <i style={{ width: `${progress.percent}%` }} />
        </span>
      ) : null}
      <small>{progress.label}</small>
    </div>
  );
}

export function RankSummary({ rank, compact = false }: { rank: MyRank; compact?: boolean }) {
  return (
    <div className={`rank-summary is-${rank.tier.toLowerCase()}${compact ? ' is-compact' : ''}`}>
      <RankBadge tier={rank.tier} division={rank.division} size={compact ? 'sm' : 'lg'} />
      <div className="rank-summary-text">
        <strong>{tierLabel(rank.tier, rank.division)}</strong>
        <span className="rank-rp">{rank.totalRp.toLocaleString('ko-KR')} RP</span>
        {rank.rankingPosition !== null ? <span className="rank-position">전체 {rank.rankingPosition.toLocaleString('ko-KR')}위</span> : null}
      </div>
      <RankProgressBar rank={rank} />
    </div>
  );
}

export function RankingTable({ entries, myUserId, emptyText }: { entries: RankingEntry[]; myUserId: number | null; emptyText: string }) {
  if (entries.length === 0) return <p className="rank-empty">{emptyText}</p>;
  return (
    <ol className="rank-table" aria-label="랭킹 목록">
      {entries.map((entry) => {
        const isMe = entry.userId === myUserId;
        return (
          <li
            key={entry.userId}
            className={`rank-row is-${entry.tier.toLowerCase()}${isMe ? ' is-me' : ''}`}
            data-user={entry.userId}
            aria-current={isMe ? 'true' : undefined}
          >
            <span className="rank-row-position">{entry.position}</span>
            <span className="rank-row-name">
              <b title={entry.displayName}>{entry.displayName}</b>
              {isMe ? <small>나</small> : null}
            </span>
            <span className="rank-row-tier">
              <RankBadge tier={entry.tier} size="sm" />
              {tierLabel(entry.tier)}
            </span>
            <span className="rank-row-rp">{entry.totalRp.toLocaleString('ko-KR')} RP</span>
          </li>
        );
      })}
    </ol>
  );
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ko-KR', { dateStyle: 'short', timeStyle: 'short' });
}

export function RankHistoryList({ history }: { history: RankChange[] }) {
  if (history.length === 0) return <p className="rank-empty">아직 RP가 반영된 4인 경기가 없습니다.</p>;
  return (
    <ol className="rank-history" aria-label="RP 변동 내역">
      {history.map((change) => (
        <li key={change.matchId} className="rank-history-row" data-match={change.matchId}>
          <span className="rank-history-place">{PLACEMENT_LABELS[change.placement] ?? `${change.placement}위`}</span>
          <span className={`rank-history-change ${changeClass(change.rpChange)}`}>{formatRp(change.rpChange)}</span>
          <span className="rank-history-flow">{change.rpBefore.toLocaleString('ko-KR')} → {change.rpAfter.toLocaleString('ko-KR')} RP</span>
          {change.rpChange !== change.placementRpChange ? (
            <small className="rank-history-note">순위 보상 {formatRp(change.placementRpChange)} · 최저 0 RP 적용</small>
          ) : null}
          <time className="rank-history-date" dateTime={change.createdAt}>{formatDate(change.createdAt)}</time>
        </li>
      ))}
    </ol>
  );
}

const MOVE_TEXT = { up: '승급!', down: '강등', same: '현재 티어 유지' };

export function MatchRankPanel({ state }: { state: MatchRankState }) {
  const move = state.status === 'applied' ? tierMove(state.settlement) : null;
  return (
    <aside className={`match-rank is-${state.status}${move ? ` is-move-${move}` : ''}`} aria-label="랭크 결과" aria-live="polite">
      <h2>RANK</h2>
      {state.status === 'unranked' ? (
        <p className="match-rank-note">4인 경기만 랭크가 적용됩니다.<br />이번 경기는 랭크 미적용</p>
      ) : state.status === 'pending' ? (
        <p className="match-rank-note">RP 정산 확인 중…</p>
      ) : state.status === 'error' ? (
        <p className="match-rank-note is-error">{state.message}</p>
      ) : (
        <>
          <p className="match-rank-place">경기 {state.settlement.placement}위</p>
          <p className={`match-rank-change ${changeClass(state.settlement.rpChange)}`}>{formatRp(state.settlement.rpChange)}</p>
          {state.settlement.rpChange !== state.settlement.placementRpChange ? (
            <small className="match-rank-floor">순위 보상 {formatRp(state.settlement.placementRpChange)} · 최저 0 RP 적용</small>
          ) : null}
          <dl className="match-rank-rp">
            <dt>이전 RP</dt><dd>{state.settlement.rpBefore.toLocaleString('ko-KR')}</dd>
            <dt>현재 RP</dt><dd>{state.settlement.rpAfter.toLocaleString('ko-KR')}</dd>
          </dl>
          {state.settlement.tierBefore === null || state.settlement.tierAfter === null ? (
            <p className="match-rank-note">이전 기록이라 티어 변화 정보가 없습니다.</p>
          ) : (
            <>
              <p className="match-rank-tier">
                {move === 'same' ? null : (
                  <>
                    <RankBadge tier={state.settlement.tierBefore} division={state.settlement.divisionBefore} size="sm" />
                    <span>{tierName(state.settlement.tierBefore, state.settlement.divisionBefore)}</span>
                    <span aria-hidden="true">→</span>
                  </>
                )}
                <RankBadge tier={state.settlement.tierAfter} division={state.settlement.divisionAfter} size="sm" />
                <span>{tierName(state.settlement.tierAfter, state.settlement.divisionAfter)}</span>
              </p>
              <p className={`match-rank-move is-${move}`}>{move ? MOVE_TEXT[move] : null}</p>
            </>
          )}
        </>
      )}
    </aside>
  );
}
