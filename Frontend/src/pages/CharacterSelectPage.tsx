import { useEffect, useRef, useState, type CSSProperties } from 'react';
import type { AuthToken } from '../services/auth';
import { arrangeCarousel, findCharacter, loadCharacterSelection, type PlayableCharacter } from '../services/characters';
import { CHARACTER_ILLUSTRATIONS } from '../assets/characters';
import '../styles/character-select.css';

function area(x: number, y: number, width: number, height: number): CSSProperties {
  return {
    left: `${(x / 1672) * 100}%`,
    top: `${(y / 941) * 100}%`,
    width: `${(width / 1672) * 100}%`,
    height: `${(height / 941) * 100}%`,
  };
}

interface CharacterSelectPageProps {
  token: AuthToken;
  confirmedCharacterId: string | null;
  onHome: () => void;
  onPlay: (characterId: string) => void;
}

export default function CharacterSelectPage({ token, confirmedCharacterId, onHome, onPlay }: CharacterSelectPageProps) {
  const [characters, setCharacters] = useState<PlayableCharacter[] | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [reloadCount, setReloadCount] = useState(0);
  const isLeaving = useRef(false);
  const isPlaying = useRef(false);

  useEffect(() => {
    let isCurrent = true;
    setCharacters(null);
    setError('');
    loadCharacterSelection(token)
      .then((result) => {
        if (!isCurrent) return;
        setCharacters(result.characters);
        setSelectedId(findCharacter(result.characters, confirmedCharacterId)?.characterId ?? result.initialCharacterId);
      })
      .catch((reason) => { if (isCurrent) setError(reason instanceof Error ? reason.message : '캐릭터 목록을 불러오지 못했습니다.'); });
    return () => { isCurrent = false; };
  }, [token, reloadCount]);

  const selected = characters ? findCharacter(characters, selectedId) : null;
  const carousel = characters ? arrangeCarousel(characters, selected?.characterId ?? null) : [];
  const isConfirmed = confirmedCharacterId !== null;
  const canPlay = selected !== null && !isConfirmed;
  const illustration = selected ? CHARACTER_ILLUSTRATIONS[selected.characterId] : undefined;
  const pending = error ? '?' : characters ? '-' : '…';

  function choose(characterId: string) {
    if (isConfirmed || !characters || !findCharacter(characters, characterId)) return;
    setSelectedId(characterId);
  }

  function play() {
    if (!selected || isConfirmed || isPlaying.current || isLeaving.current) return;
    isPlaying.current = true;
    onPlay(selected.characterId);
  }

  function goHome() {
    if (isLeaving.current) return;
    isLeaving.current = true;
    onHome();
  }

  const notice = isConfirmed
    ? `${findCharacter(characters ?? [], confirmedCharacterId)?.name ?? confirmedCharacterId} 선택 완료 · 매칭 화면 준비 중입니다.`
    : characters && characters.length === 0 ? '선택할 수 있는 캐릭터가 없습니다.' : '';

  return (
    <main className="character-page">
      <div className="character-art" style={{ '--sheet': 'url(storyboard/character-select.png)' } as CSSProperties}>
        <img src="storyboard/character-select.png" alt="" width={1672} height={941} />

        <div className="character-mask" style={area(642, 62, 326, 640)} />
        <div className="character-mask" style={area(502, 682, 616, 222)} />

        <section className="character-patch character-profile-title" style={area(226, 146, 108, 40)} aria-hidden="true">캐릭터</section>
        <dl className="character-patch character-profile" style={area(110, 256, 216, 136)} aria-label="선택한 캐릭터 정보" aria-live="polite">
          <div>
            <dt>이름</dt>
            <dd title={selected?.name}>{selected ? selected.name : pending}</dd>
          </div>
          <div>
            <dt>ID</dt>
            <dd title={selected?.characterId}>{selected ? selected.characterId : pending}</dd>
          </div>
          <div>
            <dt>상태</dt>
            <dd>{selected ? (isConfirmed ? '확정됨' : '선택 중') : pending}</dd>
          </div>
        </dl>

        <figure className="character-stage" style={area(652, 70, 306, 512)} aria-label="캐릭터 일러스트">
          {selected ? (
            illustration ? (
              <img key={selected.characterId} className="character-illustration" src={illustration} alt={selected.name} />
            ) : (
              <div key={selected.characterId} className="character-placeholder">
                <svg viewBox="0 0 120 150" aria-hidden="true">
                  <circle cx="60" cy="42" r="26" />
                  <path d="M14 146 C16 104 34 84 60 84 C86 84 104 104 106 146" />
                </svg>
                <strong>{selected.name}</strong>
                <span>일러스트 준비 중</span>
              </div>
            )
          ) : null}
        </figure>

        <div className="character-carousel" role="radiogroup" aria-label="캐릭터">
          {carousel.map(({ character, position }) => (
            <button
              key={character.characterId}
              className={`character-card is-${position}`}
              type="button"
              role="radio"
              aria-checked={position === 'center'}
              aria-hidden={position === 'hidden'}
              aria-label={character.name}
              tabIndex={position === 'hidden' ? -1 : undefined}
              style={area(668, 603, 280, 295)}
              disabled={position === 'hidden' || isConfirmed}
              onClick={() => choose(character.characterId)}
            >
              <span className="character-card-name" title={character.name}>{character.name}</span>
            </button>
          ))}
          {carousel.length > 0 ? null : (
            <span className="character-card is-center is-empty" style={area(668, 603, 280, 295)}>
              <span className="character-card-name">{pending}</span>
            </span>
          )}
        </div>

        <section className="character-patch character-ultimate" style={area(1322, 446, 242, 64)} aria-label="궁극기" aria-live="polite">
          {selected ? (
            <>
              <span className="character-ultimate-owner" title={selected.name}>{selected.name}</span>
              <strong className="character-ultimate-name" title={selected.ultimateName}>{selected.ultimateName}</strong>
              <span className="character-ultimate-id">{selected.ultimateId}</span>
            </>
          ) : error ? (
            <div className="character-message" role="alert">
              <span className="character-error" title={error}>{error}</span>
              <button className="character-retry" type="button" onClick={() => setReloadCount(reloadCount + 1)}>다시 시도</button>
            </div>
          ) : (
            <span className="character-message" role="status">{characters ? '선택 가능한 캐릭터 없음' : '캐릭터 목록 불러오는 중…'}</span>
          )}
        </section>

        <p className="character-patch character-confirm-label" style={area(1141, 700, 106, 33)}>{isConfirmed ? '확정됨' : '선택 확정'}</p>
        <button className="character-hit character-confirm" type="button" style={area(1132, 692, 125, 198)} onClick={play} disabled={!canPlay}>선택 확정</button>
        <button className="character-hit" type="button" style={area(56, 813, 229, 80)} onClick={goHome}>HOME</button>
      </div>
      <p className="character-notice" role="status">{notice}</p>
    </main>
  );
}
