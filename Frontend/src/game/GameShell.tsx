import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type { AuthToken } from '../auth';
import defaultMap from './maps/crossroads.json';
import { CARDS, COLORS, DEFAULT_DECK, canUseCard, formatTime, parseMap, rankPlayers, type GameState, type Player, type Point } from './model';
import { createDemo, DemoClock, demoUseCard, paint } from './demo';
import { GameSocket } from './transport';
import { CityScene, Portrait } from './Art';
import { GameCanvas, type CanvasControls } from './GameCanvas';
import './game.css';

const CHARACTERS = [
  { id: 'painter', name: '페인터', role: '균형 잡힌 잉크 아티스트' },
  { id: 'guard', name: '가디언', role: '내 색을 지키는 수호자' },
  { id: 'runner', name: '러너', role: '영역을 넓히는 스프린터' },
  { id: 'blaster', name: '블래스터', role: '과감한 한 방의 승부사' },
];
function inkStyle(player: Player): CSSProperties { return { '--ink': COLORS[player.color] } as CSSProperties; }

export default function GameShell({ token, username, onExit }: { token: AuthToken | null; username: string; onExit(): void }) {
  const [state, setState] = useState<GameState | null>(null);
  const [map, setMap] = useState(() => parseMap(defaultMap));
  const [character, setCharacter] = useState('painter');
  const [deck, setDeck] = useState([...DEFAULT_DECK]);
  const [demo, setDemo] = useState(true);
  const [endpoint, setEndpoint] = useState('/api/v1/game/ws');
  const [message, setMessage] = useState('');
  const [connectionLost, setConnectionLost] = useState(false);
  const [queuedAt, setQueuedAt] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());
  const [selected, setSelected] = useState<Point | null>(null);
  const [follow, setFollow] = useState(true);
  const [zoom, setZoom] = useState(1);
  const [pendingCard, setPendingCard] = useState(false);
  const camera = useRef<CanvasControls>(null);
  const clock = useRef(new DemoClock());
  const socket = useRef<GameSocket | null>(null);
  const cardTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const phase = state?.phase ?? 'lobby';
  const self = state?.players.find(p => p.id === state.selfId);

  function clearPendingCard() {
    if (cardTimeout.current) clearTimeout(cardTimeout.current);
    cardTimeout.current = null; setPendingCard(false);
  }
  useEffect(() => {
    let previous = Date.now();
    const timer = setInterval(() => {
      const time = Date.now(), delta = Math.max(0, time - previous); previous = time; setNow(time);
      if (demo) setState(current => current ? clock.current.advance(current, delta) : null);
    }, 100);
    return () => clearInterval(timer);
  }, [demo]);
  useEffect(() => () => { socket.current?.close(); if (cardTimeout.current) clearTimeout(cardTimeout.current); }, []);

  function toLobby() {
    try { if (!demo && state?.phase === 'queue' && !connectionLost) socket.current?.send({ type: 'queue.cancel' }); } catch { /* Socket may already be closed. */ }
    socket.current?.close(); clearPendingCard(); setState(null); setQueuedAt(null); setSelected(null); setMessage(''); setConnectionLost(false);
  }
  function startQueue() {
    setMessage(''); setConnectionLost(false); setQueuedAt(null); setSelected(null); setFollow(true); setZoom(1);
    clock.current = new DemoClock();
    const initial = createDemo(map, username, character, deck);
    if (demo) { setState(initial); return; }
    if (!token) { setMessage('서버 매칭을 이용하려면 먼저 로그인해 주세요.'); return; }
    if (window.location.protocol === 'file:') { setMessage('파일 미리보기에서는 화면 체험을 이용해 주세요. 서버 매칭은 서비스 주소에서 가능합니다.'); return; }
    const transport = new GameSocket(event => {
      if (event.type === 'snapshot') {
        setState(event.state); setMessage(''); setConnectionLost(false); clearPendingCard();
      } else if (event.type === 'queued') setQueuedAt(event.queued_at);
      else { setMessage(event.message); if (event.fatal) setConnectionLost(true); clearPendingCard(); }
    });
    socket.current = transport;
    try {
      const url = new URL(endpoint, window.location.href);
      url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      transport.connect(url.href, token.access_token, { character_id: character, deck });
      setState(initial);
    } catch (error) { transport.close(); setMessage(error instanceof Error ? error.message : '연결하지 못했습니다.'); }
  }
  async function loadMap(file: File | undefined) {
    if (!file) return;
    try {
      if (file.size > 1_000_000) throw new Error('맵 파일은 1MB 이하여야 합니다.');
      const nextMap = parseMap(JSON.parse(await file.text())); setMap(nextMap); setMessage(`${nextMap.name} 맵을 불러왔습니다.`);
    } catch (error) { setMessage(error instanceof Error ? error.message : '맵을 불러오지 못했습니다.'); }
  }
  function useCard(cardId: string) {
    if (!state || !self || pendingCard || connectionLost || !canUseCard(state, self, cardId)) return;
    try {
      if (demo) setState(demoUseCard(state, cardId, selected ?? undefined));
      else {
        socket.current?.send({ type: 'card.use', room_id: state.roomId, card_id: cardId, target: selected ? [selected.x, selected.y] : null });
        setPendingCard(true);
        cardTimeout.current = setTimeout(() => { setPendingCard(false); setMessage('카드 처리 응답이 없습니다. 서버 상태를 확인해 주세요.'); }, 5000);
      }
      setMessage(''); setSelected(null);
      const card = CARDS.find(c => c.id === cardId);
      if (card?.type === '공격') camera.current?.effect('impact');
    } catch (error) { setMessage(error instanceof Error ? error.message : '카드를 사용할 수 없습니다.'); }
  }
  function preview(action: 'ready' | 'playing' | 'deathmatch' | 'finished' | 'death') {
    if (!demo) return;
    setState(current => {
      if (!current) return current;
      const next = structuredClone(current);
      if (action === 'death') { const me = next.players.find(p => p.id === next.selfId)!; me.alive = false; me.respawnMs = 10000; }
      else {
        next.phase = action;
        if (action === 'ready') next.readyMs = 10000;
        if (action === 'playing') {
          next.remainingMs = 300000;
          if (next.players.every(p => p.tiles === 0)) for (const p of next.players) paint(next, p, [p.spawn]);
        }
        if (action === 'deathmatch') next.remainingMs = 60000;
        if (action === 'finished') { next.remainingMs = 0; next.winnerId = rankPlayers(next.players)[0].id; }
      }
      return next;
    });
  }

  const liveElapsed = !demo && state && !connectionLost ? Math.max(0, now - state.receivedAt) : 0;
  const remaining = Math.max(0, (state?.remainingMs ?? 0) - liveElapsed);
  const playable = phase === 'playing' || phase === 'deathmatch';

  return <div className={`game-app phase-${phase}`}>
    <header className="game-header">
      <button className="brand" onClick={() => { if (phase === 'lobby' || phase === 'finished') toLobby(); }} disabled={phase !== 'lobby' && phase !== 'finished'} aria-label="Paint Rush 로비">PAINT<span>RUSH</span><i>잉크로 차지하는 나만의 영역</i></button>
      <div className="header-right"><span className={`mode-badge ${demo ? 'demo' : ''}`}>{demo ? '화면 체험' : '서버 플레이'}</span><span>{username || '게스트 페인터'}</span>
        {phase === 'lobby' && <button className="quiet-button" onClick={onExit}>{token ? '로그아웃' : '로그인으로'}</button>}</div>
    </header>

    {phase === 'lobby' && <main className="lobby-layout">
      <section className="lobby-hero">
        <p className="eyebrow">YOUR COLOR. YOUR CITY.</p><h1>도시를 물들일<br /><em>준비됐어?</em></h1>
        <p className="hero-description">카드를 고르고, 잉크를 터뜨리고.<br />5분 뒤, 가장 넓은 색의 주인이 되어 봐.</p>
        <div className="hero-art"><Portrait character={character} color="RED" /><span className="hero-paint-stamp">MAKE<br />YOUR MARK.</span></div>
        <p className="art-caption">캐릭터·도시 이미지는 콘티 기반 임시 벡터 아트입니다.</p>
      </section>
      <section className="loadout-panel" aria-labelledby="loadout-title">
        <div className="section-heading"><span className="eyebrow">01 / LOADOUT</span><h2 id="loadout-title">나의 페인터</h2></div>
        <div className="character-options">{CHARACTERS.map(c => <button key={c.id} className={`character-option ${character === c.id ? 'selected' : ''}`} onClick={() => setCharacter(c.id)} aria-pressed={character === c.id}>
          <Portrait character={c.id} color="RED" /><span>{c.name}</span></button>)}</div>
        <p className="character-role">{CHARACTERS.find(c => c.id === character)?.role}</p>
        <div className="section-heading small"><h3>내 카드 덱</h3><span>6장 · 순서를 바꿀 수 있어요</span></div>
        <div className="deck-grid">{deck.map((id, index) => { const card = CARDS.find(c => c.id === id)!; return <button key={id} className={`mini-card card-${card.id}`} title={`${card.description}. 클릭하면 덱 맨 앞으로 이동합니다.`} onClick={() => setDeck([id, ...deck.filter(c => c !== id)])}><span className="card-number">0{index + 1}</span><b>{card.icon}</b><span>{card.name}</span><small>{card.type} · {card.cost}</small></button>; })}</div>
        <div className="match-config"><label>플레이 방식<select value={demo ? 'demo' : 'server'} onChange={e => { setDemo(e.target.value === 'demo'); setMessage(''); }}><option value="demo">화면 체험 · 서버 없이</option><option value="server" disabled={!token}>서버 매칭{!token ? ' · 로그인 필요' : ''}</option></select></label><div><small>선택한 맵</small><strong>{map.name}</strong><span>{map.width} × {map.height}</span></div></div>
        <button className="primary-button queue-start" onClick={startQueue}>{demo ? '매칭 화면 체험' : '매칭 시작'} <span>↗</span></button>
        <p className="subtle-note">{demo ? '3초 매칭 → 10초 준비 → 게임 → 결과를 체험합니다. 실제 전적에는 반영되지 않습니다.' : '매칭 API는 서버 개발 후 연결됩니다. 자동으로 체험 모드로 전환하지 않습니다.'}</p>
        <details className="connection-settings"><summary>맵 불러오기 · 연결 설정</summary><label>JSON 맵 파일<input type="file" accept="application/json,.json" onChange={e => { void loadMap(e.target.files?.[0]); e.currentTarget.value = ''; }} /></label><button className="quiet-button" onClick={() => setMap(parseMap(defaultMap))}>기본 맵으로</button><label>게임 WebSocket 경로<input value={endpoint} onChange={e => setEndpoint(e.target.value)} /></label><p>서버 담당자와 합의할 연결 경로입니다. 현재 저장소에는 게임 WebSocket API가 없습니다.</p></details>
      </section>
    </main>}

    {phase === 'queue' && state && self && <main className="matchmaking-screen">
      <CityScene />
      <div className="match-top"><span className="eyebrow">FIND YOUR RIVALS</span><h1>다음 색을 찾는 중</h1><p>{demo ? '곧 새로운 페인터들을 만납니다.' : queuedAt ? '매칭 대기열에 참가했습니다.' : '매칭 서버 응답을 기다립니다.'}</p></div>
      <aside className="queue-loadout"><Portrait character={self.character} color={self.color} /><div><small>MY PAINTER</small><strong>{CHARACTERS.find(c => c.id === character)?.name}</strong><span>{self.name}</span></div><div className="queue-deck">{deck.map(id => <span key={id} title={CARDS.find(c => c.id === id)?.name}>{CARDS.find(c => c.id === id)?.icon}</span>)}</div></aside>
      <section className="matching-status" aria-label="매칭 대기 상태"><span className="matching-dots"><i /><i /><i /></span><h2>{connectionLost ? '연결을 확인해 주세요' : '매칭 중'}</h2><p className="queue-time">{formatTime(demo ? state.queueMs : queuedAt ? now - queuedAt : 0)}</p><p className="waiting-caption">대기 시간</p><button className="secondary-button" onClick={toLobby}>매칭 취소</button></section>
    </main>}

    {phase === 'ready' && state && <main className="ready-screen" aria-labelledby="ready-title">
      <h1 id="ready-title" className="sr-only">플레이어 준비 화면</h1>
      <div className={`versus-grid players-${state.players.length}`}>{state.players.map((p, index) => <section key={p.id} className={`versus-player player-${index}`} style={inkStyle(p)}>
        <div className="versus-name"><small>{CHARACTERS.find(c => c.id === p.character)?.name ?? p.character}</small><h2>{p.name}</h2><span>Lv. {p.level} {p.id === state.selfId && '· 나'}</span></div>
        <Portrait character={p.character} color={p.color} />
        <div className="player-info"><strong>{p.connected ? '준비 완료' : '연결 대기'}</strong><span>덱 {p.deck.length}장</span><div>{p.deck.map((id, i) => <b key={`${id}-${i}`} title={id}>{CARDS.find(c => c.id === id)?.icon ?? '?'}</b>)}</div></div>
      </section>)}</div>
      <div className="versus-center" aria-hidden="true">VS</div>
      <div className="ready-countdown"><span>{state.map.name}</span><strong>{Math.ceil(Math.max(0, state.readyMs - liveElapsed) / 1000)}</strong><span>{connectionLost ? '서버 연결 대기' : '잠시 후 시작합니다'}</span></div>
    </main>}

    {playable && state && self && <main className="arena-screen">
      <div className="arena-topbar"><div><span className="eyebrow">{state.map.name}</span><strong>{phase === 'deathmatch' ? 'DEATHMATCH' : 'PAINT THE CITY'}</strong></div><div className={`game-timer ${phase === 'deathmatch' ? 'urgent' : ''}`}><strong>{formatTime(remaining)}</strong><span>{phase === 'deathmatch' ? '코스트 0.45초 / 1' : '코스트 0.86초 / 1'}</span></div><div className="my-score"><small>나의 영역</small><b>{self.tiles}<span> 타일</span></b></div></div>
      <div className="arena-body"><aside className="scoreboard"><h2>현재 영역</h2>{rankPlayers(state.players).map((p, index) => <div key={p.id} style={inkStyle(p)} className={`score-row ${p.id === state.selfId ? 'is-me' : ''}`}><span>{index + 1}</span><i /><div><strong>{p.name}</strong><small>{p.alive ? p.connected ? '플레이 중' : '연결 끊김' : '리스폰 대기'}</small></div><b>{p.tiles}</b></div>)}<p>직접 이동은 없습니다.<br />타일을 선택하고 이동 카드를 사용하세요.</p></aside>
        <div className="arena-stage"><GameCanvas ref={camera} state={state} selected={selected} onSelect={setSelected} onFollow={setFollow} />
          <div className="map-label">{state.map.width} × {state.map.height} <span>{follow ? '내 아바타 추적 중' : '자유 카메라'}</span></div>
          {!self.alive && <div className="death-overlay" role="status"><small>잠깐, 잉크를 채우는 중</small><h2>다시 칠할 기회</h2><strong>{Math.ceil(Math.max(0, self.respawnMs - liveElapsed) / 1000)}</strong><p>최초 스폰 지점에서 돌아옵니다.</p></div>}
          {connectionLost && <div className="connection-overlay"><h2>서버 연결이 끊겼습니다</h2><p>확인된 마지막 화면입니다. 카드 사용이 중지되었습니다.</p><button className="secondary-button" onClick={toLobby}>로비로</button></div>}
        </div>
      </div>
      <div className="camera-toolbar"><button className={follow ? 'active' : ''} onClick={() => camera.current?.follow()}>◎ 내 아바타 추적</button><div className="camera-pan"><span>카메라</span>{[[0, -80, '↑'], [-80, 0, '←'], [80, 0, '→'], [0, 80, '↓']].map(([x, y, label]) => <button key={String(label)} aria-label={`카메라 ${label}`} onClick={() => camera.current?.pan(Number(x), Number(y))}>{label}</button>)}</div><label>확대<input aria-label="카메라 확대" type="range" min="0.45" max="2" step="0.05" value={zoom} onChange={e => { const value = Number(e.target.value); setZoom(value); camera.current?.zoom(value); }} /></label><span className="target-coordinate">대상 {selected ? `${selected.x}, ${selected.y}` : '미선택'}</span>{selected && <button onClick={() => setSelected(null)}>대상 해제</button>}</div>
      <section className="hand-panel" aria-label="카드와 코스트"><div className="cost-panel"><strong>{Math.floor(self.cost)}<small>/10</small></strong><div className="cost-pips">{Array.from({ length: 10 }, (_, i) => <i key={i} className={i < self.cost ? 'filled' : ''} />)}</div><span>사용한 코스트 {self.costUsed}</span></div><div className="hand-cards">{self.hand.map((id, index) => { const card = CARDS.find(c => c.id === id); return <button key={`${id}-${index}`} className={`hand-card card-${id}`} disabled={!card || !canUseCard(state, self, id) || connectionLost || pendingCard || remaining === 0} onClick={() => useCard(id)} title={card?.description ?? '아직 정의되지 않은 카드'}><small>{card?.type ?? '미정'}<b>{card?.cost ?? '?'}</b></small><span className="hand-icon">{card?.icon ?? '?'}</span><strong>{card?.name ?? id}</strong><span>{card?.description ?? '카드 정의를 기다립니다.'}</span></button>; })}</div></section>
    </main>}

    {phase === 'finished' && state && <main className="results-screen">
      <p className="eyebrow">THE CITY HAS A NEW COLOR</p><h1>{state.winnerId === state.selfId ? 'VICTORY!' : '다음 색을 준비해요'}</h1><p className="result-subtitle">{state.winnerId === state.selfId ? '이번 도시의 주인은 바로 당신.' : '좋은 승부였어요. 다음 광장에서 다시 만나요.'}</p>
      <div className="result-content"><section className="winner-card" style={inkStyle(state.players.find(p => p.id === state.winnerId)!)}><span className="winner-label">WINNER · 1위</span><Portrait character={state.players.find(p => p.id === state.winnerId)!.character} color={state.players.find(p => p.id === state.winnerId)!.color} /><h2>{state.players.find(p => p.id === state.winnerId)!.name}</h2><span>{state.players.find(p => p.id === state.winnerId)!.tiles} 타일 차지</span></section>
      <section className="result-table-panel"><div className="result-table-scroll"><table><caption>최종 경기 결과 · {state.map.name}</caption><thead><tr><th>순위 / 플레이어</th><th>영역</th><th>누적 도색</th><th>처치</th><th>사용 카드</th><th>사용 코스트</th><th>결과</th></tr></thead><tbody>{rankPlayers(state.players).map((p, i) => <tr key={p.id} className={p.id === state.selfId ? 'my-result' : ''}><th><span style={{ color: COLORS[p.color] }}>●</span> {i + 1}. {p.name}{p.id === state.selfId && ' (나)'}</th><td>{p.tiles}</td><td>{p.painted}</td><td>{p.kills}</td><td>{p.cardsUsed}</td><td>{p.costUsed}</td><td>{p.id === state.winnerId ? '승리' : '패배'}</td></tr>)}</tbody></table></div><p className="ranking-explanation">동률 시 누적 도색 → 처치 → 적은 사용 카드 → 적은 사용 코스트 → 빠른 매칭 순으로 결정합니다.</p><div className="xp-summary">획득 경험치 <strong>{demo ? '체험 경기 · 전적 미반영' : self?.xpGained !== undefined ? `+${self.xpGained} XP` : '서버 집계 대기'}</strong></div></section></div>
      <button className="primary-button" onClick={toLobby}>로비로 돌아가기 ↗</button>
    </main>}

    {message && <div className="game-notice" role="status"><span>{message}</span>{connectionLost && <button onClick={toLobby}>로비로 돌아가기</button>}<button aria-label="안내 닫기" onClick={() => setMessage('')}>×</button></div>}
    {demo && phase !== 'lobby' && <details className="preview-controls"><summary>화면 체험 도구</summary><p>체험 데이터입니다. 실제 경기에는 적용되지 않습니다.</p><div>{(['ready', 'playing', 'deathmatch', 'finished'] as const).map((action, i) => <button key={action} onClick={() => preview(action)}>{['준비 10초', '게임 5분', '데스매치', '결과'][i]}</button>)}{playable && <><button onClick={() => preview('death')}>사망·리스폰 10초</button><button onClick={() => camera.current?.effect('shake')}>흔들림</button><button onClick={() => camera.current?.effect('warp')}>왜곡</button><button onClick={() => camera.current?.effect('impact')}>충격</button></>}<button onClick={toLobby}>로비</button></div></details>}
  </div>;
}
