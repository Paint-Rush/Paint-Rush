import { useRef, useState } from 'react';
import type { CSSProperties, RefObject } from 'react';
import { GameCanvas, type CanvasControls } from './GameCanvas';
import { COLORS, canUseCard, formatTime, getCard, type GameState, type Point } from './model';
import { PATTERNS, patternTiles, type Direction, type Pattern } from './patterns';
import crossImage from './assets/ingame/criss-cross.png';
import swastikaImage from './assets/ingame/swastika.png';
import lineImage from './assets/ingame/the-line.png';
import './ingame.css';

const ART = {
  criss_cross: { image: crossImage, width: 956, height: 540, viewBox: '98 124 214 284' },
  swastika: { image: swastikaImage, width: 961, height: 540, viewBox: '105 129 214 284' },
  the_line: { image: lineImage, width: 961, height: 546, viewBox: '102 130 214 284' },
};
interface Props {
  state: GameState; pending: boolean; disconnected: boolean; demo: boolean; remainingMs: number;
  onUseCard(index: number): void; onReconnect(): void;
  cameraRef?: RefObject<CanvasControls | null>;
}

export default function InGame({ state, pending, disconnected, demo, remainingMs, onUseCard, onReconnect, cameraRef }: Props) {
  const self = state.players.find(player => player.id === state.selfId)!;
  const [pattern, setPattern] = useState<Pattern>('criss_cross');
  const [direction, setDirection] = useState<Direction>('left');
  const [selected, setSelected] = useState<Point | null>(null);
  const [follow, setFollow] = useState(true);
  const [zoom, setZoom] = useState(1);
  const localCamera = useRef<CanvasControls>(null);
  const camera = cameraRef ?? localCamera;
  const art = ART[pattern], definition = PATTERNS.find(item => item.id === pattern)!;
  const inputDisabled = pending || disconnected || state.inputReady === false || remainingMs <= 0 || !self.alive;
  const preview = patternTiles({ pattern, origin: self.position, map: state.map, direction });

  return <main className="ingame-board" aria-label="인게임">
    <aside className="ingame-card-panel">
      <div className="ingame-status"><span>{demo ? '콘티 체험' : '서버 플레이'}</span><strong className={state.phase === 'deathmatch' ? 'deathmatch-time' : ''}>{formatTime(remainingMs)}</strong></div>
      <h1>Attack Card</h1>
      <svg className="story-card" viewBox={art.viewBox} role="img" aria-label={`${definition.name} 콘티 원본 카드`}>
        <image href={art.image} width={art.width} height={art.height} />
      </svg>
      <p className="pattern-preview-label">콘티 카드 미리보기</p>
      <div className="pattern-tabs" aria-label="콘티 카드 선택">{PATTERNS.map(item => <button key={item.id} aria-pressed={pattern === item.id} onClick={() => { setPattern(item.id); setSelected(null); }}>{item.name}</button>)}</div>
      <p className="pattern-description">{definition.description}</p>
      {pattern === 'the_line' && <fieldset className="line-directions"><legend>방향 선택</legend>{(['up', 'left', 'right', 'down'] as Direction[]).map((value, i) => <button key={value} aria-pressed={direction === value} onClick={() => setDirection(value)} aria-label={`직선 ${['위', '왼쪽', '오른쪽', '아래'][i]}`}>{['↑', '←', '→', '↓'][i]}</button>)}</fieldset>}
      <section className="server-hand" aria-label="현재 손패">
        <div className="hand-heading"><span>현재 손패</span><strong>코스트 <b>{self.cost}</b> / {state.maxCost ?? 10}</strong></div>
        <div className="ingame-cost-pips" aria-label={`코스트 ${self.cost}`}>{Array.from({ length: state.maxCost ?? 10 }, (_, i) => <i key={i} className={i < self.cost ? 'filled' : ''} />)}</div>
        <div className="server-hand-cards">{self.hand.map((id, index) => {
          const card = getCard(id);
          return <button key={`${index}-${id}`} className="server-hand-card" aria-label={`${index + 1}번 손패 ${card?.name ?? id} 사용`} disabled={inputDisabled || !canUseCard(state, self, id)} onClick={() => onUseCard(index)}>
            <small>{index + 1}<span>{card?.cost ?? '?'}</span></small><b>{card?.icon ?? '?'}</b><strong>{card?.name ?? id}</strong>
          </button>;
        })}</div>
        <p className="next-card">다음 카드 <strong>{state.nextCard ? getCard(state.nextCard)?.name ?? state.nextCard : '서버 확인 대기'}</strong></p>
        {pending && <p role="status" className="card-pending">사용 결과 확인 중…</p>}
      </section>
    </aside>
    <section className="ingame-map-panel" aria-label="게임 맵">
      <GameCanvas ref={camera} blueprint state={state} selected={selected} onSelect={point => {
        setSelected(point);
        if (pattern !== 'the_line') return;
        if (point.x === self.position.x && point.y !== self.position.y) setDirection(point.y < self.position.y ? 'up' : 'down');
        else if (point.y === self.position.y && point.x !== self.position.x) setDirection(point.x < self.position.x ? 'left' : 'right');
      }} onFollow={setFollow} previewTiles={preview} />
      <div className="ingame-map-info"><span>{state.map.width} × {state.map.height} · {self.name} · <b data-testid="player-position">{self.position.x}, {self.position.y}</b></span><span style={{ '--ink': COLORS[self.color] } as CSSProperties}>내 영역 {self.tiles}</span></div>
      <div className="ingame-camera-controls"><button aria-pressed={follow} onClick={() => camera.current?.follow()}>내 캐릭터 추적</button><label>확대<input aria-label="카메라 확대" type="range" min="0.45" max="2" step="0.05" value={zoom} onChange={e => { const value = Number(e.target.value); setZoom(value); camera.current?.zoom(value); }} /></label><span>{selected ? `선택 ${selected.x}, ${selected.y}` : '타일 선택'}</span></div>
      {!self.alive && <div className="death-overlay" role="status"><h2>리스폰 대기</h2>{self.respawnMs > 0 ? <strong>{Math.ceil(self.respawnMs / 1000)}</strong> : <p>서버의 복귀 상태를 기다립니다.</p>}</div>}
      {disconnected && <div className="connection-overlay"><h2>서버 상태 확인이 필요합니다</h2><p>마지막으로 확인된 화면입니다. 카드 사용이 중지됐습니다.</p><button className="secondary-button" onClick={onReconnect}>재접속</button></div>}
      {remainingMs === 0 && <div className="ingame-end-marker" role="status">경기 종료 · 카드 사용 중지</div>}
    </section>
  </main>;
}
