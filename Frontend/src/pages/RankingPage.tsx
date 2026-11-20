import { useEffect, useRef, useState } from 'react';
import type { AuthToken } from '../services/auth';
import {
  CHALLENGER_LIMIT,
  RANKING_PAGE_SIZE,
  loadChallengers,
  loadMyRank,
  loadRankHistory,
  loadRanking,
  type MyRank,
  type RankChange,
  type RankingList,
} from '../services/rank';
import { RankHistoryList, RankingTable, RankSummary } from '../components/RankWidgets';
import '../styles/rank.css';

export type RankingTab = 'all' | 'challengers' | 'history';

const TABS: { id: RankingTab; label: string }[] = [
  { id: 'all', label: '전체 랭킹' },
  { id: 'challengers', label: `챌린저 TOP ${CHALLENGER_LIMIT}` },
  { id: 'history', label: '내 RP 내역' },
];

type Loaded<T> = { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; data: T };

export interface RankingApi {
  myRank: (token: AuthToken) => Promise<MyRank>;
  ranking: (token: AuthToken, page: number) => Promise<RankingList>;
  challengers: (token: AuthToken) => Promise<RankingList>;
  history: (token: AuthToken) => Promise<RankChange[]>;
}

const DEFAULT_API: RankingApi = {
  myRank: (token) => loadMyRank(token),
  ranking: (token, page) => loadRanking(token, page),
  challengers: (token) => loadChallengers(token),
  history: (token) => loadRankHistory(token),
};

function message(reason: unknown): string {
  return reason instanceof Error ? reason.message : '랭크 정보를 불러오지 못했습니다.';
}

function useLoaded<T>(load: () => Promise<T>, keys: unknown[]): [Loaded<T>, () => void] {
  const [reload, setReload] = useState(0);
  const key = JSON.stringify([...keys, reload]);
  const [state, setState] = useState<{ key: string; value: Loaded<T> }>({ key: '', value: { status: 'loading' } });
  useEffect(() => {
    let isCurrent = true;
    setState({ key, value: { status: 'loading' } });
    load()
      .then((data) => { if (isCurrent) setState({ key, value: { status: 'ready', data } }); })
      .catch((reason) => { if (isCurrent) setState({ key, value: { status: 'error', message: message(reason) } }); });
    return () => { isCurrent = false; };
  }, [key]);
  return [state.key === key ? state.value : { status: 'loading' }, () => setReload((value) => value + 1)];
}

export function RankingView({
  tab,
  page,
  myRank,
  list,
  history,
  onTab,
  onPage,
  onRetry,
  onBack,
}: {
  tab: RankingTab;
  page: number;
  myRank: Loaded<MyRank>;
  list: Loaded<RankingList> | null;
  history: Loaded<RankChange[]> | null;
  onTab: (tab: RankingTab) => void;
  onPage: (page: number) => void;
  onRetry: () => void;
  onBack: () => void;
}) {
  const myUserId = myRank.status === 'ready' ? myRank.data.userId : null;
  const content = tab === 'history' ? history : list;
  const pages = list?.status === 'ready' ? Math.max(1, Math.ceil(list.data.total / RANKING_PAGE_SIZE)) : 1;

  return (
    <main className="ranking-page">
      <header className="ranking-head">
        <button type="button" className="rank-button is-back" onClick={onBack}><span aria-hidden="true">◀</span> 로비</button>
        <h1>RANKING</h1>
        <p>Master 이상 · RP 순위</p>
      </header>

      <section className="ranking-me" aria-label="내 랭크">
        {myRank.status === 'ready' ? (
          <RankSummary rank={myRank.data} />
        ) : myRank.status === 'error' ? (
          <div className="rank-error" role="alert">
            <span>{myRank.message}</span>
            <button type="button" className="rank-button" onClick={onRetry}>다시 시도</button>
          </div>
        ) : (
          <p className="rank-loading">내 랭크 불러오는 중…</p>
        )}
      </section>

      <nav className="ranking-tabs" aria-label="랭킹 보기">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={`rank-tab${item.id === tab ? ' is-active' : ''}${item.id === 'challengers' ? ' is-challenger' : ''}`}
            aria-pressed={item.id === tab}
            onClick={() => onTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <section className={`ranking-body is-${tab}`} aria-live="polite">
        {!content || content.status === 'loading' ? (
          <p className="rank-loading">불러오는 중…</p>
        ) : content.status === 'error' ? (
          <div className="rank-error" role="alert">
            <span>{content.message}</span>
            <button type="button" className="rank-button" onClick={onRetry}>다시 시도</button>
          </div>
        ) : tab === 'history' ? (
          <RankHistoryList history={content.data as RankChange[]} />
        ) : (
          <>
            {tab === 'challengers' ? <p className="ranking-caption">1800 RP 이상 · 전체 랭킹 상위 {CHALLENGER_LIMIT}위</p> : null}
            <RankingTable
              entries={(content.data as RankingList).rankings}
              myUserId={myUserId}
              emptyText={tab === 'challengers' ? '아직 챌린저가 없습니다.' : '아직 Master 이상 플레이어가 없습니다.'}
            />
          </>
        )}
      </section>

      {tab === 'all' && list?.status === 'ready' && list.data.total > RANKING_PAGE_SIZE ? (
        <nav className="ranking-pages" aria-label="페이지">
          <button type="button" className="rank-button" disabled={page === 0} onClick={() => onPage(page - 1)}>이전</button>
          <span>{page + 1} / {pages}</span>
          <button type="button" className="rank-button" disabled={page + 1 >= pages} onClick={() => onPage(page + 1)}>다음</button>
        </nav>
      ) : null}
    </main>
  );
}

export default function RankingPage({ token, onBack, api = DEFAULT_API }: { token: AuthToken; onBack: () => void; api?: RankingApi }) {
  const [tab, setTab] = useState<RankingTab>('all');
  const [page, setPage] = useState(0);
  const cache = useRef(new Map<string, Promise<unknown>>());

  function cached<T>(key: string, load: () => Promise<T>): Promise<T> {
    const id = `${token.access_token}:${key}`;
    if (!cache.current.has(id)) {
      const request = load();
      cache.current.set(id, request);
      request.catch(() => cache.current.delete(id));
    }
    return cache.current.get(id) as Promise<T>;
  }

  const [myRank, reloadMyRank] = useLoaded(() => api.myRank(token), [token]);
  const [list, reloadList] = useLoaded(
    () => (tab === 'challengers'
      ? cached('challengers', () => api.challengers(token))
      : tab === 'all' ? cached(`all:${page}`, () => api.ranking(token, page)) : Promise.resolve(null)),
    [token, tab === 'history' ? 'history' : tab, tab === 'all' ? page : 0],
  );
  const [history, reloadHistory] = useLoaded(
    () => (tab === 'history' ? cached('history', () => api.history(token)) : Promise.resolve(null)),
    [token, tab === 'history'],
  );

  return (
    <RankingView
      tab={tab}
      page={page}
      myRank={myRank}
      list={tab === 'history' ? null : list as Loaded<RankingList>}
      history={tab === 'history' ? history as Loaded<RankChange[]> : null}
      onTab={(next) => { setTab(next); if (next !== 'all') setPage(0); }}
      onPage={setPage}
      onRetry={() => {
        if (myRank.status === 'error') reloadMyRank();
        if (tab === 'history') reloadHistory();
        else reloadList();
      }}
      onBack={onBack}
    />
  );
}
