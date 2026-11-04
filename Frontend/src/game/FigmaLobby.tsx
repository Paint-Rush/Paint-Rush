import FigmaStage from '../ui/FigmaStage';
import lobbyStoryboard from '../assets/figma/lobby-storyboard.png';
import './storyboard-menu.css';

export default function FigmaLobby({ onSelectCharacter, onConfigure }: { onSelectCharacter(): void; onConfigure(): void }) {
  return <FigmaStage label="로비" className="storyboard-screen storyboard-lobby">
    <img className="figma-title-art" src={lobbyStoryboard} alt="Figma 로비 콘티 원본" />
    <button className="storyboard-button lobby-avatar" aria-label="캐릭터 선택" title="캐릭터 선택" onClick={onSelectCharacter} />
    <nav aria-label="로비 메뉴">
      <button className="storyboard-button lobby-game" aria-label="게임 시작" title="GAME" onClick={onSelectCharacter} />
      <button className="storyboard-button lobby-shop" aria-label="상점" title="SHOP" disabled />
      <button className="storyboard-button lobby-profile" aria-label="프로필" title="PROFILE" onClick={onSelectCharacter} />
      <button className="storyboard-button lobby-settings" aria-label="설정" title="SETTING" onClick={onConfigure} />
    </nav>
  </FigmaStage>;
}
