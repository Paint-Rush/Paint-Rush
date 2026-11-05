import type { Point } from './model';

/** Camera coordinates are world pixels; this class never changes a player's position. */
export class Camera {
  x = 0; y = 0; zoom = 1;
  width = 1; height = 1; worldWidth = 1; worldHeight = 1; fit = 1;
  effect = 0;
  effectKind: 'shake' | 'warp' | 'impact' = 'shake';
  get scale() { return this.fit * this.zoom; }
  configure(width: number, height: number, worldWidth: number, worldHeight: number, padding = 16) {
    const changedMap = worldWidth !== this.worldWidth || worldHeight !== this.worldHeight;
    this.width = width; this.height = height; this.worldWidth = worldWidth; this.worldHeight = worldHeight;
    this.fit = Math.max(0.001, Math.min((width - padding * 2) / worldWidth, (height - padding * 2) / worldHeight));
    if (changedMap) this.overview();
    else this.constrain();
  }
  constrain() {
    const halfWidth = this.width / (2 * this.scale), halfHeight = this.height / (2 * this.scale);
    this.x = halfWidth >= this.worldWidth / 2 ? this.worldWidth / 2 : Math.max(halfWidth, Math.min(this.worldWidth - halfWidth, this.x));
    this.y = halfHeight >= this.worldHeight / 2 ? this.worldHeight / 2 : Math.max(halfHeight, Math.min(this.worldHeight - halfHeight, this.y));
  }
  overview() { this.zoom = 1; this.x = this.worldWidth / 2; this.y = this.worldHeight / 2; }
  center(target: Point) { this.x = target.x; this.y = target.y; this.constrain(); }
  pan(dx: number, dy: number, displayedScale = this.scale) { this.x += dx / displayedScale; this.y += dy / displayedScale; this.constrain(); }
  setZoom(zoom: number) { this.zoom = Math.max(1, Math.min(3, zoom)); this.constrain(); }
  zoomAt(zoom: number, point: Point) {
    const anchor = { x: this.x + (point.x - this.width / 2) / this.scale, y: this.y + (point.y - this.height / 2) / this.scale };
    this.zoom = Math.max(1, Math.min(3, zoom));
    this.x = anchor.x - (point.x - this.width / 2) / this.scale;
    this.y = anchor.y - (point.y - this.height / 2) / this.scale;
    this.constrain();
  }
  viewport() {
    const halfWidth = this.width / (2 * this.scale), halfHeight = this.height / (2 * this.scale);
    const x = Math.max(0, this.x - halfWidth), y = Math.max(0, this.y - halfHeight);
    return { x, y, width: Math.min(this.worldWidth, this.x + halfWidth) - x, height: Math.min(this.worldHeight, this.y + halfHeight) - y };
  }
  trigger(kind: 'shake' | 'warp' | 'impact') { this.effectKind = kind; this.effect = 0.6; }
  step(delta: number, reducedMotion: boolean) {
    this.effect = reducedMotion ? 0 : Math.max(0, this.effect - delta);
    const strength = this.effect / 0.6;
    return {
      dx: this.effectKind === 'shake' ? Math.sin(this.effect * 90) * 12 * strength : 0,
      dy: this.effectKind === 'shake' ? Math.cos(this.effect * 75) * 8 * strength : 0,
      scale: this.effectKind === 'impact' ? 1 + Math.sin(strength * Math.PI) * 0.1 : 1,
      skew: this.effectKind === 'warp' ? Math.sin(strength * Math.PI * 3) * 0.12 * strength : 0,
    };
  }
}
