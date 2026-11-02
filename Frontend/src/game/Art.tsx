import matchmakingStoryboard from "./assets/matchmaking-storyboard.png";
import { COLORS, type Color } from './model';

/** Vector stand-ins: replace with character art without changing the screen layout. */
export function Portrait({ character, color, className = '' }: { character: string; color: Color; className?: string }) {
  const ink = COLORS[color];
  return <svg viewBox="0 0 360 340" className={`portrait ${className}`} aria-hidden="true">
    <circle cx="180" cy="170" r="126" fill={ink} opacity=".16" />
    <path d="M25 258L333 77M36 285L320 104M70 308L342 148" stroke={ink} strokeWidth="12" opacity=".15" />
    <ellipse cx="182" cy="317" rx="106" ry="13" fill="#20333d" opacity=".15" />
    <g stroke="#243a43" strokeWidth="5" strokeLinejoin="round">
      <path d="M116 254L109 319H151L179 264L200 319H243L225 253" fill="#2e414b" />
      <path d="M129 156L88 200L118 231L137 205L130 270L236 270L225 201L247 222L273 194L226 154Z" fill={ink} />
      <path d="M157 159L143 263L211 263L195 158" fill="#f4f0dc" />
      <path d="M155 231H212L215 251H150Z" fill="#263e4a" />
      <path d="M157 130V165Q180 185 200 163V128" fill="#dfb899" />
      <path d="M128 76Q124 27 183 30Q243 30 239 83L228 131Q182 175 138 127Z" fill="#f2d3ae" />
      <path d="M125 82L126 53L147 31L168 40L194 19L212 37L239 45L246 81L215 70L197 57L173 82L152 70Z" fill={ink} />
      <path d="M145 101L169 105M199 104L220 98" strokeWidth="7" />
      <path d="M173 132Q189 140 205 129" fill="none" />
      {character === 'guard' ? <><path d="M232 164L307 185L294 272L250 300L213 263Z" fill="#f6f2dc" /><path d="M248 195L282 205L275 251L254 268L239 249Z" fill={ink} /></>
        : character === 'runner' ? <><path d="M95 204L46 274L96 288L142 218" fill="#f3efd9" /><path d="M36 279Q82 251 117 290" stroke={ink} strokeWidth="16" fill="none" /></>
        : character === 'blaster' ? <><path d="M219 182L320 143L337 189L232 222Z" fill="#f3efd9" /><path d="M301 150L319 195" stroke={ink} strokeWidth="12" /></>
        : <><path d="M104 211L74 241L50 220L80 191Z" fill="#f2d3ae" /><path d="M53 201Q17 172 40 143Q74 151 66 180" stroke={ink} strokeWidth="13" fill="none" /></>}
      <circle cx="178" cy="200" r="9" fill={ink} />
    </g>
    <g fill={ink}><circle cx="55" cy="99" r="8" /><circle cx="290" cy="86" r="12" /><circle cx="313" cy="115" r="5" /></g>
  </svg>;
}

export function CityScene() {
  return <div className="city-scene"><img className="storyboard-image" src={matchmakingStoryboard} alt="제공된 매치메이킹 콘티 원본" /></div>;
}
