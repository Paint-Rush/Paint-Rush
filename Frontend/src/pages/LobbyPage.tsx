import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type { AuthToken } from '../services/auth';
import { levelProgress, loadLobbyAccount, loadLobbyWallet, type LobbyAccount } from '../services/account';
import { loadMyRank, type MyRank } from '../services/rank';
import { RankSummary } from '../components/RankWidgets';
import '../styles/lobby.css';

function area(x: number, y: number, width: number, height: number): CSSProperties {
  return {
    left: `${(x / 1672) * 100}%`,
    top: `${(y / 941) * 100}%`,
    width: `${(width / 1672) * 100}%`,
    height: `${(height / 941) * 100}%`,
  };
}

const MENU = [
  { label: 'GAME', box: area(150, 292, 245, 92), notice: '캐릭터 선택 화면은 준비 중입니다.' },
  { label: 'SHOP', box: area(95, 405, 180, 82), notice: '상점은 개발 예정입니다.' },
  { label: 'PROFILE', box: area(100, 493, 252, 82), notice: '프로필 화면은 개발 예정입니다.' },
  { label: 'SETTING', box: area(95, 583, 258, 92), notice: '사운드 시스템은 준비 중입니다. 오른쪽 BGM·SFX 조절 값은 저장되지 않습니다.' },
];

interface LobbyPageProps {
  token: AuthToken;
  onGame: () => void;
  onLogout: () => void;
  onRanking?: () => void;
  loadRank?: (token: AuthToken) => Promise<MyRank>;
  loadWallet?: (token: AuthToken) => Promise<number>;
}

export default function LobbyPage({ token, onGame, onLogout, onRanking = () => {}, loadRank = loadMyRank, loadWallet = loadLobbyWallet }: LobbyPageProps) {
  const [account, setAccount] = useState<LobbyAccount | null>(null);
  const [gold, setGold] = useState<number | null>(null);
  const [goldError, setGoldError] = useState('');
  const [goldReload, setGoldReload] = useState(0);
  const [rank, setRank] = useState<MyRank | null>(null);
  const [rankError, setRankError] = useState('');
  const [rankReload, setRankReload] = useState(0);
  const [error, setError] = useState('');
  const [reloadCount, setReloadCount] = useState(0);
  const [notice, setNotice] = useState('');
  const [isSettingOpen, setIsSettingOpen] = useState(false);
  const [bgm, setBgm] = useState(50);
  const [sfx, setSfx] = useState(50);
  const bgmInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let isCurrent = true;
    setAccount(null);
    setError('');
    loadLobbyAccount(token)
      .then((result) => { if (isCurrent) setAccount(result); })
      .catch((reason) => { if (isCurrent) setError(reason instanceof Error ? reason.message : '계정 정보를 불러오지 못했습니다.'); });
    return () => { isCurrent = false; };
  }, [token, reloadCount]);

  useEffect(() => {
    let isCurrent = true;
    setGold(null);
    setGoldError('');
    loadWallet(token)
      .then((result) => { if (isCurrent) setGold(result); })
      .catch((reason) => { if (isCurrent) setGoldError(reason instanceof Error ? reason.message : '골드 정보를 불러오지 못했습니다.'); });
    return () => { isCurrent = false; };
  }, [token, goldReload]);

  useEffect(() => {
    let isCurrent = true;
    setRank(null);
    setRankError('');
    loadRank(token)
      .then((result) => { if (isCurrent) setRank(result); })
      .catch((reason) => { if (isCurrent) setRankError(reason instanceof Error ? reason.message : '랭크 정보를 불러오지 못했습니다.'); });
    return () => { isCurrent = false; };
  }, [token, rankReload]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(''), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  function openMenu(label: string, message: string) {
    if (label === 'GAME') {
      onGame();
      return;
    }
    setNotice(message);
    if (label === 'SETTING') {
      setIsSettingOpen(true);
      bgmInput.current?.focus();
    }
  }

  return (
    <main className="lobby-page">
      <div className="lobby-art">
        <img src="storyboard/lobby.png" alt="" width={1672} height={941} />

        <p className="lobby-patch lobby-level" style={area(283, 28, 145, 54)}>
          Lv. {account ? account.level.toLocaleString('ko-KR') : error ? '?' : '…'}
        </p>
        <p className="lobby-patch lobby-coin" style={area(322, 96, 124, 48)} aria-label="보유 골드">
          {gold !== null ? (
            <span className="lobby-gold">{gold.toLocaleString('ko-KR')}</span>
          ) : goldError ? (
            <button className="lobby-retry" type="button" title={goldError} onClick={() => setGoldReload(goldReload + 1)}>다시 시도</button>
          ) : (
            <span className="lobby-loading">…</span>
          )}
        </p>
        <div className="lobby-patch lobby-account" style={area(222, 155, 285, 50)} role="status">
          {account ? (
            <>
              <span className="lobby-name" title={account.displayName}>{account.displayName}</span>
              <span className="lobby-exp">EXP {account.totalExperience.toLocaleString('ko-KR')}</span>
            </>
          ) : error ? (
            <>
              <span className="lobby-error" title={error}>{error}</span>
              <button className="lobby-retry" type="button" onClick={() => setReloadCount(reloadCount + 1)}>다시 시도</button>
            </>
          ) : (
            <span className="lobby-loading">계정 정보 불러오는 중…</span>
          )}
        </div>
        {account ? (
          <div className="lobby-patch lobby-level-progress" style={area(222, 207, 285, 40)} aria-label="경험치 진행도">
            {(() => {
              const progress = levelProgress(account.level, account.totalExperience);
              return (
                <>
                  <span className="lobby-level-track" role="progressbar" aria-valuemin={0} aria-valuemax={progress.needed} aria-valuenow={progress.current}>
                    <i style={{ width: `${progress.percent}%` }} />
                  </span>
                  <small>{progress.current.toLocaleString('ko-KR')} / {progress.needed.toLocaleString('ko-KR')} · 다음 레벨까지 {progress.remaining.toLocaleString('ko-KR')} EXP</small>
                </>
              );
            })()}
          </div>
        ) : null}
        <button className="lobby-logout" type="button" style={area(478, 98, 112, 46)} onClick={onLogout}>로그아웃</button>

        <section className="lobby-rank" style={area(612, 34, 236, 170)} aria-label="내 랭크">
          {rank ? (
            <RankSummary rank={rank} compact />
          ) : rankError ? (
            <p className="lobby-rank-state">
              <span className="lobby-error" title={rankError}>{rankError}</span>
              <button className="lobby-retry" type="button" onClick={() => setRankReload(rankReload + 1)}>다시 시도</button>
            </p>
          ) : (
            <p className="lobby-rank-state"><span className="lobby-loading">랭크 불러오는 중…</span></p>
          )}
          <button className="lobby-rank-open" type="button" onClick={onRanking}>랭킹 보기 ▶</button>
        </section>

        <nav className="lobby-menu" aria-label="로비 메뉴">
          {MENU.map((item) => (
            <button key={item.label} className="lobby-menu-button" type="button" style={item.box} onClick={() => openMenu(item.label, item.notice)}>
              {item.label}
            </button>
          ))}
        </nav>

        <p className="lobby-patch lobby-pending" style={area(1285, 335, 260, 52)}>친구 · 채팅 준비 중</p>

        <div className={isSettingOpen ? 'lobby-setting is-open' : 'lobby-setting'} style={area(1255, 435, 343, 355)} />
        <p className="lobby-patch lobby-setting-note" style={area(1370, 466, 215, 52)}>사운드 준비 중 · 값 저장 안 됨</p>
        <span className="lobby-patch" style={area(1474, 556, 26, 12)} />
        <label className="lobby-patch lobby-slider" style={area(1386, 567, 144, 26)}>
          <span className="lobby-hidden">BGM 볼륨 미리보기</span>
          <input ref={bgmInput} type="range" min={0} max={100} value={bgm} onChange={(event) => setBgm(Number(event.target.value))} />
        </label>
        <label className="lobby-patch lobby-slider" style={area(1388, 645, 104, 24)}>
          <span className="lobby-hidden">SFX 볼륨 미리보기</span>
          <input type="range" min={0} max={100} value={sfx} onChange={(event) => setSfx(Number(event.target.value))} />
        </label>
      </div>
      <p className="lobby-notice" role="status">{notice}</p>
    </main>
  );
}
