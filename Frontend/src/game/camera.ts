import type { Point } from './model';

/** Camera coordinates are world pixels; this class never changes a player's position. */
export class Camera {
  x = 0; y = 0; zoom = 1; following = true;
  effect = 0;
  effectKind: 'shake' | 'warp' | 'impact' = 'shake';
  follow(target: Point, deltaSeconds: number) {
    if (!this.following) return;
    const blend = 1 - Math.exp(-10 * Math.max(0, deltaSeconds));
    this.x += (target.x - this.x) * blend;
    this.y += (target.y - this.y) * blend;
  }
  pan(dx: number, dy: number) { this.following = false; this.x += dx / this.zoom; this.y += dy / this.zoom; }
  setZoom(zoom: number) { this.zoom = Math.max(0.45, Math.min(2, zoom)); }
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
