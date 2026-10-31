import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Application, Container, Graphics, Point as PixiPoint } from 'pixi.js';
import { Camera } from './camera';
import { COLORS, type GameState, type Point } from './model';

export interface CanvasControls {
  follow(): void; pan(x: number, y: number): void; zoom(value: number): void;
  effect(kind: 'shake' | 'warp' | 'impact'): void;
}
interface Props { state: GameState; selected: Point | null; onSelect(point: Point): void; onFollow(value: boolean): void }
const TILE = 48;

export const GameCanvas = forwardRef<CanvasControls, Props>(function GameCanvas({ state, selected, onSelect, onFollow }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef({ state, selected, onSelect, onFollow });
  latest.current = { state, selected, onSelect, onFollow };
  const camera = useRef(new Camera());
  const [error, setError] = useState('');
  useImperativeHandle(ref, () => ({
    follow() { camera.current.following = true; latest.current.onFollow(true); },
    pan(x, y) { camera.current.pan(x, y); latest.current.onFollow(false); },
    zoom(value) { camera.current.setZoom(value); },
    effect(kind) { camera.current.trigger(kind); },
  }), []);

  useEffect(() => {
    let cancelled = false;
    let cleanup = () => {};
    const app = new Application();
    async function start() {
      try {
        await app.init({ width: 800, height: 540, background: '#202d35', antialias: true,
          resolution: Math.min(window.devicePixelRatio || 1, 2), autoDensity: true, preference: 'webgl' });
        if (cancelled) { app.destroy(true, { children: true }); return; }
        const element = host.current!;
        element.appendChild(app.canvas);
        app.canvas.setAttribute('aria-label', '타일 맵. 드래그는 카메라 이동, 클릭은 카드 대상 선택입니다.');
        app.canvas.setAttribute('role', 'img');
        const world = new Container();
        const tiles = new Graphics(), avatars = new Graphics(), highlight = new Graphics(), shock = new Graphics();
        world.addChild(tiles, avatars, highlight);
        app.stage.addChild(world, shock);
        const me = latest.current.state.players.find(p => p.id === latest.current.state.selfId)!;
        camera.current.x = (me.position.x + 0.5) * TILE;
        camera.current.y = (me.position.y + 0.5) * TILE;
        const resize = new ResizeObserver(() => {
          const bounds = element.getBoundingClientRect();
          if (bounds.width > 0 && bounds.height > 0) app.renderer.resize(bounds.width, bounds.height);
        });
        resize.observe(element);
        const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
        let previous: GameState | null = null;
        let lastSelection: Point | null | undefined;
        app.ticker.add(ticker => {
          const { state: current, selected: selection } = latest.current;
          const self = current.players.find(p => p.id === current.selfId)!;
          const delta = Math.min(ticker.deltaMS / 1000, 0.1);
          camera.current.follow({ x: (self.position.x + 0.5) * TILE, y: (self.position.y + 0.5) * TILE }, delta);
          const effect = camera.current.step(delta, motion.matches);
          const zoom = camera.current.zoom * effect.scale;
          world.scale.set(zoom);
          world.skew.set(effect.skew, 0);
          const bounded = (position: number, screenSize: number, worldSize: number) => worldSize * zoom <= screenSize
            ? (screenSize - worldSize * zoom) / 2
            : Math.min(24, Math.max(screenSize - worldSize * zoom - 24, position));
          let worldX = app.screen.width / 2 - camera.current.x * zoom;
          let worldY = app.screen.height / 2 - camera.current.y * zoom;
          if (camera.current.following) {
            worldX = bounded(worldX, app.screen.width, current.map.width * TILE);
            worldY = bounded(worldY, app.screen.height, current.map.height * TILE);
          }
          world.position.set(worldX + effect.dx, worldY + effect.dy);
          if (current !== previous) {
            tiles.clear(); avatars.clear();
            for (let y = 0; y < current.map.height; y++) for (let x = 0; x < current.map.width; x++) {
              const definition = current.map.legend[String(current.map.tiles[y][x])];
              const owner = current.players.find(p => p.id === current.owners[y][x]);
              tiles.roundRect(x * TILE + 1, y * TILE + 1, TILE - 2, TILE - 2, 3)
                .fill(owner ? COLORS[owner.color] : definition.color);
              if (!definition.walkable) {
                tiles.rect(x * TILE + 8, y * TILE + 8, TILE - 16, TILE - 16).fill('#34454c');
                tiles.moveTo(x * TILE + 8, y * TILE + 8).lineTo(x * TILE + 40, y * TILE + 40).stroke({ color: '#8b9a9e', width: 2 });
              }
            }
            for (const p of current.players) {
              if (!p.alive) continue;
              const x = (p.position.x + 0.5) * TILE, y = (p.position.y + 0.5) * TILE;
              avatars.ellipse(x, y + 14, 16, 6).fill({ color: '#17232b', alpha: 0.35 });
              if (p.id === current.selfId) avatars.circle(x, y, 21).stroke({ color: '#fff', width: 3 });
              avatars.roundRect(x - 11, y - 1, 22, 23, 4).fill(COLORS[p.color]);
              avatars.circle(x, y - 11, 12).fill('#f6e4c9');
              avatars.roundRect(x - 13, y - 22, 26, 10, 3).fill(COLORS[p.color]);
              avatars.rect(x - 6, y - 13, 3, 4).rect(x + 3, y - 13, 3, 4).fill('#24313b');
              avatars.roundRect(x + 10, y, 6, 19, 2).fill('#24313b');
              if (!p.connected) avatars.circle(x, y - 28, 4).fill('#aaa');
            }
            previous = current;
          }
          if (selection !== lastSelection) {
            highlight.clear();
            if (selection) highlight.rect(selection.x * TILE + 2, selection.y * TILE + 2, TILE - 4, TILE - 4).stroke({ color: '#fff', width: 3 });
            lastSelection = selection;
          }
          shock.clear();
          if (camera.current.effectKind === 'impact' && camera.current.effect > 0 && !motion.matches) {
            const progress = 1 - camera.current.effect / 0.6;
            shock.circle(app.screen.width / 2, app.screen.height / 2, 20 + progress * 200)
              .stroke({ color: '#fff', width: 5 * (1 - progress), alpha: 1 - progress });
          }
        });
        let drag: { startX: number; startY: number; x: number; y: number; moved: boolean; id: number } | null = null;
        const down = (event: PointerEvent) => {
          if (event.button !== 0) return;
          drag = { startX: event.clientX, startY: event.clientY, x: event.clientX, y: event.clientY, moved: false, id: event.pointerId };
          app.canvas.setPointerCapture(event.pointerId);
        };
        const move = (event: PointerEvent) => {
          if (!drag || drag.id !== event.pointerId) return;
          if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > 5) drag.moved = true;
          if (drag.moved) {
            camera.current.pan(drag.x - event.clientX, drag.y - event.clientY);
            latest.current.onFollow(false);
          }
          drag.x = event.clientX; drag.y = event.clientY;
        };
        const up = (event: PointerEvent) => {
          if (!drag || drag.id !== event.pointerId) return;
          if (!drag.moved) {
            const bounds = app.canvas.getBoundingClientRect();
            const local = world.toLocal(new PixiPoint((event.clientX - bounds.left) * app.screen.width / bounds.width,
              (event.clientY - bounds.top) * app.screen.height / bounds.height));
            const x = Math.floor(local.x / TILE), y = Math.floor(local.y / TILE);
            const map = latest.current.state.map;
            if (x >= 0 && x < map.width && y >= 0 && y < map.height) latest.current.onSelect({ x, y });
          }
          drag = null;
        };
        const cancel = () => { drag = null; };
        app.canvas.addEventListener('pointerdown', down);
        app.canvas.addEventListener('pointermove', move);
        app.canvas.addEventListener('pointerup', up);
        app.canvas.addEventListener('pointercancel', cancel);
        cleanup = () => {
          resize.disconnect();
          app.canvas.removeEventListener('pointerdown', down); app.canvas.removeEventListener('pointermove', move);
          app.canvas.removeEventListener('pointerup', up); app.canvas.removeEventListener('pointercancel', cancel);
          app.destroy(true, { children: true });
        };
      } catch {
        if (app.renderer) app.destroy(true, { children: true });
        if (!cancelled) setError('게임 화면을 그릴 수 없습니다. WebGL을 지원하는 브라우저에서 다시 열어 주세요.');
      }
    }
    void start();
    return () => { cancelled = true; cleanup(); };
  }, []);

  return <div className="arena-canvas" ref={host}>{error && <p role="alert" className="canvas-error">{error}</p>}</div>;
});
