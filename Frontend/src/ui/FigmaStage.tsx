import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import './figma.css';

// Scale the supplied 657 × 396 composition as a whole, keeping its layers aligned.
export default function FigmaStage({ children, className = '', label }: { children: ReactNode; className?: string; label: string }) {
  const container = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(() => Math.min(window.innerWidth / 657, window.innerHeight / 396));

  useLayoutEffect(() => {
    const element = container.current;
    if (!element) return;
    const resize = () => setScale(Math.min(element.clientWidth / 657, element.clientHeight / 396));
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return <div ref={container} className={`figma-stage-shell ${className}`} style={{ '--scene-scale': scale } as CSSProperties}>
    <main className="figma-stage" aria-label={label}>{children}</main>
  </div>;
}
