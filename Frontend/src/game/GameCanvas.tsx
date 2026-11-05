import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Application, Container, Graphics, Point as PixiPoint } from 'pixi.js';
import { Camera } from './camera';
import { COLORS, type GameState, type Point } from './model';

export interface CanvasControls {
  overview(): void;
  follow(): void; pan(x: number, y: number): void; zoom(value: number): void;
  lookAt(point: Point): void;
  effect(kind: 'shake' | 'warp' | 'impact'): void;
}
export interface CanvasView { zoom: number; scale: number; centerX: number; centerY: number; x: number; y: number; width: number; height: number }
interface Props {
  state: GameState; selected: Point | null; onSelect(point: Point): void; onFollow?(value: boolean): void;
  onViewChange?(view: CanvasView): void;
  onHover?(point: Point | null): void; blueprint?: boolean; previewTiles?: readonly Point[]; previewPath?: readonly Point[];
}
const TILE = 48;

export const GameCanvas = forwardRef<CanvasControls, Props>(function GameCanvas({ state, selected, onSelect, onFollow, onViewChange, onHover, blueprint = false, previewTiles = [], previewPath = [] }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const latest = useRef({ state, selected, onSelect, onFollow, onViewChange, onHover, blueprint, previewTiles, previewPath });
  latest.current = { state, selected, onSelect, onFollow, onViewChange, onHover, blueprint, previewTiles, previewPath };
  const camera = useRef(new Camera());
  const [error, setError] = useState('');
  useImperativeHandle(ref, () => ({
    overview() { camera.current.overview(); latest.current.onHover?.(null); },
    follow() {
      const self = latest.current.state.players.find(player => player.id === latest.current.state.selfId)!;
      camera.current.center({ x: (self.position.x + 0.5) * TILE, y: (self.position.y + 0.5) * TILE });
      latest.current.onFollow?.(true); latest.current.onHover?.(null);
    },
    pan(x, y) { camera.current.pan(x, y); latest.current.onFollow?.(false); },
    zoom(value) { camera.current.setZoom(value); },
    lookAt(point) { camera.current.center({ x: point.x * TILE, y: point.y * TILE }); latest.current.onHover?.(null); },
    effect(kind) { camera.current.trigger(kind); },
  }), []);

  useEffect(() => {
    let cancelled = false;
    let cleanup = () => {};
    const app = new Application();
    async function start() {
      try {
        await app.init({ width: 800, height: 540, background: latest.current.blueprint ? '#ffffff' : '#202d35', antialias: true,
          resolution: Math.min(window.devicePixelRatio || 1, 2), autoDensity: true, preference: 'webgl' });
        if (cancelled) { app.destroy(true, { children: true }); return; }
        const element = host.current!;
        element.appendChild(app.canvas);
        app.canvas.setAttribute('aria-label', '타일 맵. 휠 확대·축소, 우클릭 드래그, Space 내 위치. 카드 호버 후 타일을 좌클릭해 실행합니다.');
        app.canvas.setAttribute('role', 'img');
        const world = new Container();
        const tiles = new Graphics(), avatars = new Graphics(), highlight = new Graphics(), range = new Graphics(), actionFlash = new Graphics(), shock = new Graphics();
        world.addChild(tiles, range, actionFlash, avatars, highlight);
        app.stage.addChild(world, shock);
        const resize = new ResizeObserver(() => {
          const bounds = element.getBoundingClientRect();
          if (bounds.width > 0 && bounds.height > 0) app.renderer.resize(bounds.width, bounds.height);
        });
        resize.observe(element);
        const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
        let previous: GameState | null = null;
        let lastSelection: Point | null | undefined;
        let lastRange = '';
        let lastView = '';
        let actionId: number | undefined, actionMs = 0;
        app.ticker.add(ticker => {
          const { state: current, selected: selection, blueprint: isBlueprint, previewTiles: preview, previewPath: path } = latest.current;
          const self = current.players.find(p => p.id === current.selfId)!;
          const delta = Math.min(ticker.deltaMS / 1000, 0.1);
          if (current.previewAction?.id !== actionId) { actionId = current.previewAction?.id; actionMs = 0.65; }
          else actionMs = Math.max(0, actionMs - delta);
          const effect = camera.current.step(delta, motion.matches);
          const padding = Math.min(16, Math.min(app.screen.width, app.screen.height) * 0.04);
          const mapWidth = current.map.width * TILE, mapHeight = current.map.height * TILE;
          camera.current.configure(app.screen.width, app.screen.height, mapWidth, mapHeight, padding);
          const zoom = camera.current.scale * effect.scale;
          world.scale.set(zoom);
          world.skew.set(effect.skew, 0);
          const worldX = app.screen.width / 2 - camera.current.x * zoom;
          const worldY = app.screen.height / 2 - camera.current.y * zoom;
          world.position.set(worldX + effect.dx, worldY + effect.dy);
          const viewport = camera.current.viewport();
          const view: CanvasView = { zoom: camera.current.zoom, scale: camera.current.scale,
            centerX: camera.current.x / TILE, centerY: camera.current.y / TILE,
            x: viewport.x / TILE, y: viewport.y / TILE, width: viewport.width / TILE, height: viewport.height / TILE };
          const viewKey = JSON.stringify(view);
          if (viewKey !== lastView) {
            lastView = viewKey;
            app.canvas.dataset.zoom = String(view.zoom); app.canvas.dataset.scale = String(view.scale);
            app.canvas.dataset.centerX = String(view.centerX); app.canvas.dataset.centerY = String(view.centerY);
            latest.current.onViewChange?.(view);
          }
          if (current !== previous) {
            tiles.clear(); avatars.clear();
            for (let y = 0; y < current.map.height; y++) for (let x = 0; x < current.map.width; x++) {
              const definition = current.map.legend[String(current.map.tiles[y][x])];
              const owner = current.players.find(p => p.id === current.owners[y][x]);
              if (isBlueprint) tiles.rect(x * TILE, y * TILE, TILE, TILE)
                .fill(owner ? COLORS[owner.color] : definition.color)
                .stroke({ color: '#c4c4c4', width: 1.2 });
              else tiles.roundRect(x * TILE + 1, y * TILE + 1, TILE - 2, TILE - 2, 3).fill(owner ? COLORS[owner.color] : definition.color);
              if (!definition.walkable) {
                tiles.rect(x * TILE + 8, y * TILE + 8, TILE - 16, TILE - 16).fill('#34454c');
                tiles.moveTo(x * TILE + 8, y * TILE + 8).lineTo(x * TILE + 40, y * TILE + 40).stroke({ color: '#8b9a9e', width: 2 });
              }
            }
            for (const p of current.players) {
              if (!p.alive) continue;
              const x = (p.position.x + 0.5) * TILE, y = (p.position.y + 0.5) * TILE;
              if (isBlueprint) {
                avatars.circle(x, y - 14, 10).stroke({ color: '#171717', width: 2.4 });
                avatars.moveTo(x, y - 4).lineTo(x, y + 19).moveTo(x, y).lineTo(x - 14, y + 10)
                  .moveTo(x, y).lineTo(x + 14, y + 10).moveTo(x, y + 19).lineTo(x - 12, y + 36)
                  .moveTo(x, y + 19).lineTo(x + 12, y + 36).stroke({ color: '#171717', width: 2.4 });
                continue;
              }
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
          const rangeKey = JSON.stringify([preview, path, current.map.width, current.map.height]);
          if (rangeKey !== lastRange) {
            range.clear();
            const pathKeys = new Set(path.map(point => `${point.x},${point.y}`));
            const all = new Map([...preview, ...path].map(point => [`${point.x},${point.y}`, point]));
            for (const [key, point] of all) {
              if (!Number.isInteger(point.x) || !Number.isInteger(point.y) || point.x < 0 || point.y < 0 || point.x >= current.map.width || point.y >= current.map.height) continue;
              const x = point.x * TILE + 4, y = point.y * TILE + 4, size = TILE - 8;
              const aimed = pathKeys.has(key);
              // A white backing, red hatch and dashed edge stay distinct from solid red ownership.
              range.rect(x, y, size, size).fill({ color: '#ffffff', alpha: aimed ? 0.85 : 0.62 })
                .stroke({ color: '#ffffff', width: 5 });
              for (let offset = 8; offset < size * 2; offset += 10) {
                range.moveTo(x + Math.max(0, offset - size), y + Math.min(size, offset))
                  .lineTo(x + Math.min(size, offset), y + Math.max(0, offset - size));
              }
              range.stroke({ color: '#c91c3d', width: aimed ? 2.4 : 1.4, alpha: aimed ? 0.9 : 0.55 });
              for (let offset = 0; offset < size; offset += 12) {
                const end = Math.min(size, offset + 7);
                range.moveTo(x + offset, y).lineTo(x + end, y).moveTo(x + offset, y + size).lineTo(x + end, y + size)
                  .moveTo(x, y + offset).lineTo(x, y + end).moveTo(x + size, y + offset).lineTo(x + size, y + end);
              }
              range.stroke({ color: '#c91c3d', width: aimed ? 3 : 2 });
            }
            lastRange = rangeKey;
          }
          actionFlash.clear();
          if (actionMs > 0 && current.previewAction) for (const point of current.previewAction.tiles) {
            actionFlash.rect(point.x * TILE + 2, point.y * TILE + 2, TILE - 4, TILE - 4)
              .stroke({ color: COLORS[self.color], width: 3, alpha: motion.matches ? 1 : actionMs / 0.65 });
          }
          if (selection !== lastSelection) {
            highlight.clear();
            if (selection) highlight.rect(selection.x * TILE + 2, selection.y * TILE + 2, TILE - 4, TILE - 4).stroke({ color: isBlueprint ? '#f48b17' : '#fff', width: 3 });
            lastSelection = selection;
          }
          shock.clear();
          if (camera.current.effectKind === 'impact' && camera.current.effect > 0 && !motion.matches) {
            const progress = 1 - camera.current.effect / 0.6;
            shock.circle(app.screen.width / 2, app.screen.height / 2, 20 + progress * 200)
              .stroke({ color: '#fff', width: 5 * (1 - progress), alpha: 1 - progress });
          }
        });
        let drag: { startX: number; startY: number; x: number; y: number; moved: boolean; id: number; pans: boolean } | null = null;
        const pointAt = (event: PointerEvent): Point | null => {
          const bounds = app.canvas.getBoundingClientRect();
          const local = world.toLocal(new PixiPoint((event.clientX - bounds.left) * app.screen.width / bounds.width,
            (event.clientY - bounds.top) * app.screen.height / bounds.height));
          const x = Math.floor(local.x / TILE), y = Math.floor(local.y / TILE), map = latest.current.state.map;
          return x >= 0 && x < map.width && y >= 0 && y < map.height ? { x, y } : null;
        };
        const down = (event: PointerEvent) => {
          if (event.button !== 0 && event.button !== 2) return;
          drag = { startX: event.clientX, startY: event.clientY, x: event.clientX, y: event.clientY, moved: false, id: event.pointerId,
            pans: event.button === 2 || event.pointerType === 'touch' };
          app.canvas.setPointerCapture(event.pointerId);
        };
        const move = (event: PointerEvent) => {
          if (!drag) { latest.current.onHover?.(pointAt(event)); return; }
          if (drag.id !== event.pointerId) return;
          if (Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > 5) drag.moved = true;
          if (drag.moved && drag.pans) {
            camera.current.pan(drag.x - event.clientX, drag.y - event.clientY);
            latest.current.onFollow?.(false);
            latest.current.onHover?.(null);
            app.canvas.classList.add('camera-dragging');
          }
          drag.x = event.clientX; drag.y = event.clientY;
        };
        const up = (event: PointerEvent) => {
          if (!drag || drag.id !== event.pointerId) return;
          if (!drag.moved && event.button === 0) {
            const point = pointAt(event);
            if (point) latest.current.onSelect(point);
          }
          if (app.canvas.hasPointerCapture(event.pointerId)) app.canvas.releasePointerCapture(event.pointerId);
          drag = null;
          app.canvas.classList.remove('camera-dragging');
        };
        const leave = () => latest.current.onHover?.(null);
        const cancel = () => { drag = null; app.canvas.classList.remove('camera-dragging'); leave(); };
        const wheel = (event: WheelEvent) => {
          event.preventDefault();
          const bounds = app.canvas.getBoundingClientRect();
          camera.current.zoomAt(camera.current.zoom * Math.exp(-Math.max(-150, Math.min(150, event.deltaY)) * 0.0025),
            { x: (event.clientX - bounds.left) * app.screen.width / bounds.width, y: (event.clientY - bounds.top) * app.screen.height / bounds.height });
          leave();
        };
        const contextMenu = (event: MouseEvent) => event.preventDefault();
        const recenter = (event: KeyboardEvent) => {
          const target = event.target instanceof Element ? event.target : null;
          if (event.code !== 'Space' || event.repeat || target?.closest('input, textarea, select, [contenteditable="true"]')) return;
          if (target?.closest('button') && !target.closest('.server-hand-card')) return;
          event.preventDefault();
          const current = latest.current.state, self = current.players.find(player => player.id === current.selfId)!;
          camera.current.center({ x: (self.position.x + 0.5) * TILE, y: (self.position.y + 0.5) * TILE });
          leave();
        };
        app.canvas.addEventListener('pointerdown', down);
        app.canvas.addEventListener('pointermove', move);
        app.canvas.addEventListener('pointerup', up);
        app.canvas.addEventListener('pointercancel', cancel);
        app.canvas.addEventListener('pointerleave', leave);
        app.canvas.addEventListener('wheel', wheel, { passive: false });
        app.canvas.addEventListener('contextmenu', contextMenu);
        window.addEventListener('keydown', recenter);
        cleanup = () => {
          resize.disconnect();
          app.canvas.removeEventListener('pointerdown', down); app.canvas.removeEventListener('pointermove', move);
          app.canvas.removeEventListener('pointerup', up); app.canvas.removeEventListener('pointercancel', cancel);
          app.canvas.removeEventListener('pointerleave', leave);
          app.canvas.removeEventListener('wheel', wheel); app.canvas.removeEventListener('contextmenu', contextMenu);
          window.removeEventListener('keydown', recenter);
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
