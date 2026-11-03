import { useState } from 'react';
import '../styles/start.css';

export default function StartPage({ onStart }: { onStart: () => void }) {
  const [hasImage, setHasImage] = useState(true);

  return (
    <main className={hasImage ? 'start-page' : 'start-page start-page-empty'}>
      <div className="start-art">
        {hasImage ? (
          <img src="storyboard/start.png" alt="Paint Rush" width={1289} height={724} onError={() => setHasImage(false)} />
        ) : (
          <h1>Paint Rush</h1>
        )}
        <button className="start-button" type="button" onClick={onStart}>START</button>
      </div>
    </main>
  );
}
