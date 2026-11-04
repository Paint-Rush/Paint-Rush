import { useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { GameCanvas, type CanvasControls, type CanvasView } from './GameCanvas';
import { COLORS, canUseCard, formatTime, getCard, getCardCost, type GameState, type Point } from './model';
import { cardTargetTiles, needsCardTarget, resolveCardAction } from './cardActions';
import combatArtwork from '../assets/figma/combat-storyboard.png';
import crossImage from './assets/ingame/criss-cross.png';
import windImage from './assets/ingame/swastika.png';
import lineImage from './assets/ingame/the-line.png';
import './battle.css';

interface Props {
  state: GameState; pending: boolean; disconnected: boolean; demo: boolean; remainingMs: number;
  onUseCard(index: number, target?: Point): void; onReconnect(): void;
  cameraRef?: RefObject<CanvasControls | null>;
}
const ART = { criss_cross: { image: crossImage, viewBox: '98 124 214 284', width: 956, height: 540 }, swastika: { image: windImage, viewBox: '105 129 214 284', width: 961, height: 540 }, the_line: { image: lineImage, viewBox: '102 130 214 284', width: 961, height: 546 } };

export default function BattleScene({ state, pending, disconnected, demo, remainingMs, onUseCard, onReconnect, cameraRef }: Props) {
  const self = state.players.find(player => player.id === state.selfId)!;
  const [selected, setSelected] = useState<Point | null>(null);
  const [viewport, setViewport] = useState<CanvasView>({ zoom: 1, scale: 1, centerX: state.map.width / 2, centerY: state.map.height / 2,
    x: 0, y: 0, width: state.map.width, height: state.map.height });
  const [active, setActive] = useState<{ index: number; id: string } | null>(null);
  const [aim, setAim] = useState<Point | null>(null);
  const [targetError, setTargetError] = useState('');
  const localCamera = useRef<CanvasControls>(null), camera = cameraRef ?? localCamera;
  const disabled = pending || disconnected || state.inputReady === false || remainingMs <= 0 || !self.alive;
  const total = state.map.width * state.map.height;
  const occupied = state.players.reduce((sum, player) => sum + player.tiles, 0);
  const next = state.nextCard ? getCard(state.nextCard) : undefined;
  const activeId = active && self.hand[active.index] === active.id ? active.id : undefined;
  const targets = useMemo(() => Object.fromEntries(self.hand.map(id => [id, needsCardTarget(id)
    ? cardTargetTiles(id, self.position, state.map)
    : resolveCardAction(id, self.position, state.map).tiles])), [self.hand, self.position.x, self.position.y, state.map]);
  const usable = (id: string) => !disabled && canUseCard(state, self, id) && targets[id]?.length > 0;
  const preview = activeId && !disabled ? targets[activeId] ?? [] : [];
  const path = useMemo(() => {
    if (!activeId || disabled || !needsCardTarget(activeId) || !aim) return [];
    try { return resolveCardAction(activeId, self.position, state.map, aim).tiles; }
    catch { return []; }
  }, [activeId, disabled, aim, self.position.x, self.position.y, state.map]);
  const clearCard = () => { setActive(null); setAim(null); setTargetError(''); };
  useEffect(() => {
    if (disabled || !activeId) { setActive(null); setAim(null); setTargetError(''); }
  }, [disabled, activeId]);
  useEffect(() => {
    const cancel = (event: KeyboardEvent) => { if (event.key === 'Escape') clearCard(); };
    window.addEventListener('keydown', cancel);
    return () => window.removeEventListener('keydown', cancel);
  }, []);
  function previewCard(index: number, id: string) {
    if (!usable(id)) return;
    setActive({ index, id }); setAim(null); setTargetError('');
  }
  function useAt(point: Point) {
    setSelected(point);
    if (!activeId || !active || !usable(activeId)) return;
    if (!needsCardTarget(activeId) && !preview.some(tile => tile.x === point.x && tile.y === point.y)) return;
    try { resolveCardAction(activeId, self.position, state.map, point); }
    catch (error) { setTargetError(error instanceof Error ? error.message : '표시된 타일을 선택하세요.'); return; }
    onUseCard(active.index, needsCardTarget(activeId) ? point : undefined);
    clearCard();
  }

  return <main className="ingame-board battle-scene" aria-label="인게임">
    <img className="battle-artwork" src={combatArtwork} alt="" />
    <header className="battle-timer"><span>{demo ? '화면 체험' : state.phase === 'deathmatch' ? '데스매치' : '서버 플레이'}</span><strong className={state.phase === 'deathmatch' ? 'deathmatch-time' : ''} aria-label={`남은 시간 ${formatTime(remainingMs)}`}>{formatTime(remainingMs)}</strong></header>
    <section className="ingame-map-panel battle-map" aria-label="게임 맵">
      <GameCanvas ref={camera} blueprint state={state} selected={selected} onSelect={useAt}
        onHover={point => setAim(previous => previous?.x === point?.x && previous?.y === point?.y ? previous : point)}
        onViewChange={setViewport} previewTiles={preview} previewPath={path} />
      <div className="battle-player-info"><span>{self.name} · <b data-testid="player-position">{self.position.x}, {self.position.y}</b></span><span>{selected ? `목적지 ${selected.x}, ${selected.y}` : '카드 호버 → 범위 확인 → 클릭 실행'}</span></div>
      {activeId && !disabled && <div className="battle-card-hint" role="status">
        <strong>{getCard(activeId)?.name}</strong>
        <span>{targetError || (needsCardTarget(activeId) ? '맵의 빨간 표시를 클릭해 실행 · Esc 취소' : '카드 또는 빨간 범위를 클릭해 실행 · Esc 취소')}</span>
        <small>점선·빗금: 카드 범위 · 단색: 칠한 영역</small>
      </div>}
      <div className="battle-camera-help"><output aria-label="확대 배율">{viewport.zoom.toFixed(1)}×</output><span>휠 확대·축소</span><span>우클릭 드래그</span><span><kbd>Space</kbd> 내 위치</span></div>
      {viewport.zoom > 1.001 && <aside className="battle-minimap" aria-label="전체 맵">
        <div className="minimap-heading"><span>전체 맵 · 클릭해 이동</span><button aria-label="전체 맵 크기로 돌아가기" title="전체 맵 크기로 돌아가기" onClick={event => { camera.current?.overview(); event.currentTarget.blur(); }}>↙↗</button></div>
        <svg viewBox={`0 0 ${state.map.width} ${state.map.height}`} role="img" aria-label="미니맵. 클릭해서 해당 위치 보기" onContextMenu={event => event.preventDefault()} onPointerDown={event => {
          if (event.button !== 0) return;
          const bounds = event.currentTarget.getBoundingClientRect();
          camera.current?.lookAt({ x: (event.clientX - bounds.left) / bounds.width * state.map.width, y: (event.clientY - bounds.top) / bounds.height * state.map.height });
        }}>
          {state.map.tiles.flatMap((row, y) => row.map((tile, x) => {
            const owner = state.players.find(player => player.id === state.owners[y][x]);
            return <rect key={`${x}-${y}`} x={x} y={y} width={1} height={1} fill={owner ? COLORS[owner.color] : state.map.legend[String(tile)].color} />;
          }))}
          {state.players.filter(player => player.alive).map(player => <circle key={player.id} cx={player.position.x + 0.5} cy={player.position.y + 0.5} r={player.id === self.id ? 0.4 : 0.3} fill={COLORS[player.color]} stroke="#fff" strokeWidth={0.2} />)}
          <rect className="minimap-viewport" x={viewport.x} y={viewport.y} width={viewport.width} height={viewport.height} fill="#ffffff25" stroke="#fff" strokeWidth={4} vectorEffect="non-scaling-stroke" />
          <rect x={viewport.x} y={viewport.y} width={viewport.width} height={viewport.height} fill="none" stroke="#333" strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
        </svg>
      </aside>}
      {!self.alive && <div className="death-overlay" role="status"><h2>리스폰 대기</h2>{self.respawnMs > 0 ? <strong>{Math.ceil(self.respawnMs / 1000)}</strong> : <p>서버의 복귀 상태를 기다립니다.</p>}</div>}
      {disconnected && <div className="connection-overlay"><h2>서버 연결 확인</h2><p>카드 사용이 중지됐습니다.</p><button onClick={onReconnect}>재접속</button></div>}
      {remainingMs === 0 && <div className="ingame-end-marker" role="status">경기 종료 · 카드 사용 중지</div>}
    </section>
    <section className="battle-cost" aria-label="현재 코스트">
      <div className="hand-heading"><span>NOW COST</span><strong><b>{self.cost}</b> / {state.maxCost ?? 10}</strong></div>
      <div className="ingame-cost-pips" role="meter" aria-label="코스트" aria-valuemin={0} aria-valuemax={state.maxCost ?? 10} aria-valuenow={self.cost}>{Array.from({ length: state.maxCost ?? 10 }, (_, i) => <i key={i} className={i < self.cost ? 'filled' : ''} />)}</div>
    </section>
    <aside className="next-card"><span>NEXT</span><div><b>{next?.icon ?? '?'}</b><strong>{next?.name ?? '확인 중'}</strong><small>{next ? getCardCost(state, next.id) ?? '—' : '—'}</small></div></aside>
    <section className="server-hand battle-hand" aria-label="현재 손패">
      <div className="server-hand-cards">{self.hand.map((id, index) => {
        const card = getCard(id), cost = getCardCost(state, id);
        const art = Object.hasOwn(ART, id) ? ART[id as keyof typeof ART] : undefined;
        const clip = art?.viewBox.split(' ').map(Number);
        const available = usable(id);
        const isActive = activeId === id && active?.index === index;
        return <button key={`${index}-${id}`} className={`server-hand-card${available ? ' usable' : ''}${isActive ? ' active-card' : ''}`} aria-label={`${index + 1}번 손패 ${card?.name ?? id} 사용`} aria-pressed={isActive} disabled={!available} title={`${card?.description ?? id}${cost === null ? ' · 코스트 확인 필요' : ` · 코스트 ${cost}`}`} onPointerEnter={() => previewCard(index, id)} onFocus={() => previewCard(index, id)} onClick={() => {
          if (needsCardTarget(id)) previewCard(index, id);
          else { onUseCard(index); clearCard(); }
        }}>
          {isActive && <span className="card-selected-label" aria-hidden="true">선택됨</span>}
          <small>{card?.type}<span>{cost ?? '—'}</span></small>
          {art && clip ? <><strong className="card-art-name">{card?.name}</strong><svg className="hand-card-art" viewBox={art.viewBox} aria-hidden="true"><defs><clipPath id={`hand-art-${index}`}><rect x={clip[0]} y={clip[1]} width={clip[2]} height={clip[3]} /></clipPath></defs><image href={art.image} width={art.width} height={art.height} clipPath={`url(#hand-art-${index})`} /></svg></> : <><strong>{card?.name ?? id}</strong><b>{card?.icon ?? '?'}</b></>}
          <em>{index + 1}</em>
        </button>;
      })}</div>
      {pending && <p role="status" className="card-pending">사용 결과 확인 중…</p>}
    </section>
    <section className="battle-coverage" aria-label="타일 채색 비율">
      <div className="coverage-labels">{state.players.map(player => <span key={player.id} style={{ color: COLORS[player.color] }} title={`${player.name}: ${player.tiles} / ${total}칸`}>{(player.tiles / total * 100).toFixed(1)}%</span>)}</div>
      <div className="coverage-bar" aria-label={`전체 ${total}칸 중 ${occupied}칸 채색`}>{state.players.map(player => <i key={player.id} style={{ width: `${player.tiles / total * 100}%`, backgroundColor: COLORS[player.color] }} />)}<i className="unpainted" style={{ width: `${(total - occupied) / total * 100}%` }} /></div>
      <p>내 영역 {self.tiles}칸 · {(self.tiles / total * 100).toFixed(1)}%</p>
    </section>
  </main>;
}
