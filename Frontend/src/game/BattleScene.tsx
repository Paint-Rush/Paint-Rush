import { useEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { GameCanvas, type CanvasControls, type CanvasView } from './GameCanvas';
import { COLORS, formatTime, type GameState, type Point } from './model';
import { attackTargets, bombArea, bombHits, gameShortcut, moveTargets, selectCard, slashBlocked } from './roomView';
import { cardName, type CardCatalog, type CardType } from '../services/cards';
import combatArtwork from '../assets/figma/combat-storyboard.png';
import './game.css';
import './battle.css';

interface Props {
  state: GameState; cards: CardCatalog | null; pending: boolean; disconnected: boolean; remainingMs: number;
  onUseCard(index: number, target?: Point): void; onReconnect(): void;
  cameraRef?: RefObject<CanvasControls | null>;
}
const ICONS: Record<CardType, string> = { MOVE: '→', ATTACK: '✸' };

export default function BattleScene({ state, cards, pending, disconnected, remainingMs, onUseCard, onReconnect, cameraRef }: Props) {
  const self = state.players.find(player => player.id === state.selfId)!;
  const [selected, setSelected] = useState<Point | null>(null);
  const [viewport, setViewport] = useState<CanvasView>({ zoom: 1, scale: 1, centerX: state.map.width / 2, centerY: state.map.height / 2,
    x: 0, y: 0, width: state.map.width, height: state.map.height });
  const [active, setActive] = useState<{ index: number; id: string } | null>(null);
  const [aim, setAim] = useState<Point | null>(null);
  const [targetError, setTargetError] = useState('');
  const localCamera = useRef<CanvasControls>(null), camera = cameraRef ?? localCamera;
  const disabled = pending || disconnected || state.inputReady === false || remainingMs <= 0 || !self.alive
    || (state.phase !== 'playing' && state.phase !== 'deathmatch');
  const total = state.map.width * state.map.height;
  const occupied = state.players.reduce((sum, player) => sum + player.tiles, 0);
  const getCard = (id: string | null | undefined) => id ? cards?.[id] : undefined;
  const isMove = (id: string | undefined) => getCard(id)?.type === 'MOVE';
  const isAttack = (id: string | undefined) => getCard(id)?.type === 'ATTACK';
  const next = getCard(state.nextCard);
  const activeId = active && self.hand[active.index] === active.id ? active.id : undefined;
  const targets = useMemo(() => moveTargets(state.map, self.position), [self.position.x, self.position.y, state.map]);
  const strikes = useMemo(() => activeId && isAttack(activeId) ? attackTargets(state.map, self.position, activeId) : [],
    [activeId, self.position.x, self.position.y, state.map, cards]);
  const usable = (id: string) => {
    const card = getCard(id);
    return !disabled && !!card && self.cost >= card.cost
      && (card.type === 'MOVE' ? targets.length > 0 : attackTargets(state.map, self.position, id).length > 0);
  };
  const preview = !activeId || disabled ? [] : isMove(activeId) ? targets : strikes;
  const aimValid = !!aim && preview.some(tile => tile.x === aim.x && tile.y === aim.y);
  const path = useMemo(() => {
    if (!activeId || disabled || !aim || !aimValid) return [];
    return activeId === 'attack_bomb' ? bombArea(state.map, aim) : [aim];
  }, [activeId, disabled, aim, aimValid, state.map]);
  const invalid = useMemo(() => {
    if (!activeId || disabled || !isAttack(activeId)) return [];
    const blocked = activeId === 'attack_slash' ? slashBlocked(state.map, self.position) : [];
    return aim && !aimValid && !blocked.some(tile => tile.x === aim.x && tile.y === aim.y) ? [...blocked, aim] : blocked;
  }, [activeId, disabled, aim, aimValid, state.map, self.position.x, self.position.y, cards]);
  const impact = activeId === 'attack_bomb' && !disabled ? bombHits(state.map, state.owners, path, self.id) : [];
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
  const keys = useRef({ toggleCard, useAt, hand: self.hand, hasCard: !!activeId });
  keys.current = { toggleCard, useAt, hand: self.hand, hasCard: !!activeId };
  useEffect(() => {
    const editable = (event: KeyboardEvent) => event.target instanceof Element && !!event.target.closest('input, textarea, select, [contenteditable="true"]');
    const press = (event: KeyboardEvent) => {
      if (event.code === 'Space' && !editable(event)) event.preventDefault();
      const shortcut = gameShortcut(event, editable(event));
      if (!shortcut || shortcut.type === 'recenter') return;
      const { toggleCard: toggle, useAt: use, hand, hasCard } = keys.current;
      if (shortcut.type === 'select') {
        if (hand[shortcut.index] !== undefined) toggle(shortcut.index, hand[shortcut.index]);
        return;
      }
      const point = hasCard ? camera.current?.pointer() : null;
      if (point) use(point);
    };
    const release = (event: KeyboardEvent) => { if (event.code === 'Space' && !editable(event)) event.preventDefault(); };
    window.addEventListener('keydown', press);
    window.addEventListener('keyup', release);
    return () => { window.removeEventListener('keydown', press); window.removeEventListener('keyup', release); };
  }, []);
  function toggleCard(index: number, id: string) {
    const next = selectCard(activeId && active ? active : null, index, id);
    if (next) previewCard(next.index, next.id);
    else clearCard();
  }
  function useAt(point: Point) {
    setSelected(point);
    if (!activeId || !active || !usable(activeId)) return;
    if (isAttack(activeId)) {
      if (!strikes.some(tile => tile.x === point.x && tile.y === point.y)) { setTargetError('공격할 수 없는 타일입니다.'); return; }
    } else if (!isMove(activeId) || !preview.some(tile => tile.x === point.x && tile.y === point.y)) { setTargetError('표시된 타일을 선택하세요.'); return; }
    onUseCard(active.index, point);
    clearCard();
  }

  return <main className="ingame-board battle-scene" aria-label="인게임">
    <img className="battle-artwork" src={combatArtwork} alt="" />
    <header className="battle-timer"><span>{state.phase === 'deathmatch' ? '데스매치' : '서버 플레이'}</span><strong className={state.phase === 'deathmatch' ? 'deathmatch-time' : ''} aria-label={`남은 시간 ${formatTime(remainingMs)}`}>{formatTime(remainingMs)}</strong></header>
    <section className="ingame-map-panel battle-map" aria-label="게임 맵">
      <GameCanvas ref={camera} blueprint state={state} selected={selected} onSelect={useAt} onCancel={clearCard}
        onHover={point => setAim(previous => previous?.x === point?.x && previous?.y === point?.y ? previous : point)}
        onViewChange={setViewport} previewTiles={preview} previewPath={path} invalidTiles={invalid} impactTiles={impact} />
      <div className="battle-player-info"><span>{self.name} · <b data-testid="player-position">{self.position.x}, {self.position.y}</b></span><span>{selected ? `목적지 ${selected.x}, ${selected.y}` : '카드 호버 → 범위 확인 → 클릭 실행'}</span></div>
      {activeId && !disabled && <div className="battle-card-hint" role="status">
        <strong>{cardName(activeId)}</strong>
        <span>{targetError || (isMove(activeId) ? '맵의 빨간 표시를 클릭해 실행 · Esc·우클릭·카드 재클릭 취소' : activeId === 'attack_bomb' ? `내 위치에서 3칸 이내의 중심 타일을 클릭 · 상대 타일 ${impact.length}칸 중립화 · Esc·우클릭 취소` : '상하좌우 인접 NORMAL 타일을 클릭 · Esc·우클릭 취소')}</span>
        <small>점선·빗금: 카드 범위 · 단색: 칠한 영역</small>
      </div>}
      <div className="battle-camera-help"><output aria-label="확대 배율">{viewport.zoom.toFixed(1)}×</output><span>휠 확대·축소</span><span>우클릭 드래그</span><span><kbd>R</kbd> 내 위치</span><span><kbd>1</kbd><kbd>2</kbd><kbd>3</kbd> 카드 · <kbd>Space</kbd> 사용</span></div>
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
    <aside className="next-card"><span>NEXT</span><div><b>{next ? ICONS[next.type] : '?'}</b><strong>{state.nextCard ? cardName(state.nextCard) : '없음'}</strong><small>{next?.cost ?? '—'}</small></div></aside>
    <section className="server-hand battle-hand" aria-label="현재 손패">
      <div className="server-hand-cards">{self.hand.map((id, index) => {
        const card = getCard(id), cost = card?.cost ?? null;
        const available = usable(id);
        const isActive = activeId === id && active?.index === index;
        return <button key={`${index}-${id}`} className={`server-hand-card${available ? ' usable' : ''}${isActive ? ' active-card' : ''}`} aria-label={`${index + 1}번 손패 ${cardName(id)} 사용`} aria-pressed={isActive} disabled={!available} title={`${card?.type ?? id}${cost === null ? ' · 코스트 확인 필요' : ` · 코스트 ${cost}`}`} onClick={() => toggleCard(index, id)}>
          {isActive && <span className="card-selected-label" aria-hidden="true">선택됨</span>}
          <small>{card?.type ?? '—'}<span>{cost ?? '—'}</span></small>
          <strong>{cardName(id)}</strong><b>{card ? ICONS[card.type] : '?'}</b>
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
