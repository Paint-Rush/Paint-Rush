import { useEffect, useState } from 'react';
import startArtwork from '../../public/storyboard/start.png';
import '../styles/start.css';

export default function StartPage({ onStart }: { onStart: () => void }) {
  const [hasImage, setHasImage] = useState(true);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (!ready) return;
    const timer = window.setTimeout(onStart, 3000);
    return () => window.clearTimeout(timer);
  }, [ready, onStart]);

  return (
    <main className={hasImage ? 'start-page' : 'start-page start-page-empty'}>
      <div className="start-art">
        {hasImage ? (
          <img src={startArtwork} alt="Paint Rush 시작메뉴" width={1289} height={724} onLoad={() => setReady(true)} onError={() => { setHasImage(false); setReady(true); }} />
        ) : (
          <h1>Paint Rush</h1>
        )}
      </div>
    </main>
  );
}
