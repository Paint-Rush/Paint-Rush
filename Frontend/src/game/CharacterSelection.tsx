import FigmaStage from '../ui/FigmaStage';
import characterStoryboard from '../assets/figma/character-selection-storyboard.png';
import './storyboard-menu.css';

interface Props {
  character: string;
  characters: readonly { id: string; name: string; role: string }[];
  onChange(character: string): void;
  onHome(): void;
  onPlay(): void;
  onConfigure(): void;
}

export default function CharacterSelection({ character, characters, onChange, onHome, onPlay, onConfigure }: Props) {
  const current = characters.find(option => option.id === character);

  return <FigmaStage label="캐릭터 선택" className="storyboard-screen storyboard-character-selection">
    <img className="figma-title-art" src={characterStoryboard} alt="Figma 캐릭터 선택 콘티 원본" />
    <button className="storyboard-button character-home-arrow" aria-label="HOME · 로비로 돌아가기" title="HOME" onClick={onHome} />
    <button className="storyboard-button character-home-card" aria-label="Home 카드 · 로비로 돌아가기" title="Home" onClick={onHome} />
    <button className="storyboard-button character-settings" aria-label="설정" title="설정" onClick={onConfigure} />
    <button className="storyboard-button character-play" aria-label="Play" title="플레이 준비" onClick={onPlay} />
    <details className="storyboard-character-picker" onKeyDown={event => {
      if (event.key !== 'Escape') return;
      const details = event.currentTarget;
      details.open = false;
      details.querySelector('summary')?.focus();
    }}>
      <summary aria-label={`캐릭터 선택 · 현재 ${current?.name ?? character}`} title={`캐릭터 선택 · 현재 ${current?.name ?? character}`} />
      <fieldset className="storyboard-character-options">
        <legend>캐릭터 선택</legend>
        {characters.map(option => <label key={option.id} title={option.role}>
          <input type="radio" name="selected-character" value={option.id} checked={character === option.id} onChange={() => onChange(option.id)} />
          <span>{option.name}</span>
        </label>)}
        <p className="character-selection-description" role="status">{current?.role}</p>
      </fieldset>
    </details>
  </FigmaStage>;
}
