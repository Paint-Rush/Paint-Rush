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
  return <div className="city-scene" aria-hidden="true">
    <svg className="city-svg" viewBox="0 0 1440 800" preserveAspectRatio="xMidYMid slice">
      <defs>
        <pattern id="city-windows" width="46" height="48" patternUnits="userSpaceOnUse"><rect x="10" y="10" width="21" height="27" rx="2" fill="#a8cdd5" opacity=".6" /></pattern>
        <linearGradient id="city-sky" x2="0" y2="1"><stop stopColor="#d8edf1" /><stop offset="1" stopColor="#fff8e4" /></linearGradient>
      </defs>
      <path fill="url(#city-sky)" d="M0 0H1440V800H0Z" />
      <circle cx="1030" cy="160" r="85" fill="#f5da86" opacity=".7" />
      <g fill="#b8d4da"><path d="M0 370H120V270H192V345H260V230H310V365H390V800H0ZM1040 330H1110V210H1180V290H1255V200H1320V340H1440V800H1040Z" /></g>
      <g stroke="#7199ad" strokeWidth="5" fill="#eef4ec">
        <path d="M270 560V152L460 89V570Z" /><path d="M460 89L565 175V570L460 570Z" fill="#c3dce1" />
        <path d="M600 550V240L851 130V550Z" /><path d="M851 130L1040 250V570L851 550Z" fill="#c8dce0" />
        <path d="M1070 580V350L1170 319V570Z" />
      </g>
      <g fill="url(#city-windows)"><path d="M290 190L442 137V532H290Z" /><path d="M628 267L826 180V531H628Z" /><path d="M872 182L1013 272V531H872Z" /></g>
      <g fill="#91bf99"><circle cx="275" cy="528" r="52" /><circle cx="330" cy="545" r="66" /><circle cx="1065" cy="525" r="69" /><circle cx="1127" cy="535" r="47" /></g>
      <path d="M0 610Q720 465 1440 610V800H0Z" fill="#e1e1cd" />
      <g stroke="#b7c2b2" strokeWidth="3"><path d="M720 546L0 800M720 546L370 800M720 546L1070 800M720 546L1440 800M0 681H1440M0 752H1440" /></g>
      <path d="M560 552L560 484H920V556M560 507H920M660 484V555M810 484V553" fill="none" stroke="#446881" strokeWidth="9" />
      <g fill="#ef5964" opacity=".55"><path d="M166 671Q231 583 361 633T582 714Q509 781 336 752T166 671Z" /></g>
      <g fill="#76c969" opacity=".6"><path d="M868 705Q934 612 1083 638T1312 725Q1172 797 1015 768Z" /></g>
    </svg>
    <div className="scene-painter left"><Portrait character="painter" color="RED" /></div>
    <div className="scene-painter right"><Portrait character="runner" color="GREEN" /></div>
    <svg className="ink-curtain ink-left" viewBox="0 0 220 800" preserveAspectRatio="none"><path d="M0 0H134Q218 90 145 194T160 384T140 566T194 800H0Z" fill="#93c8ff" /><path className="ink-flow" d="M123-30Q225 80 136 194T152 384T133 566T180 830" fill="none" stroke="#3497f2" strokeWidth="40" /></svg>
    <svg className="ink-curtain ink-right" viewBox="0 0 220 800" preserveAspectRatio="none"><path d="M0 0H134Q218 90 145 194T160 384T140 566T194 800H0Z" fill="#b5d88e" /><path className="ink-flow" d="M123-30Q225 80 136 194T152 384T133 566T180 830" fill="none" stroke="#79bb79" strokeWidth="40" /></svg>
  </div>;
}
