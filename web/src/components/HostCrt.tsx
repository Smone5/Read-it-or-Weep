// Animated retro CRT oscilloscope monitor for the talking game-show host.
// Brand-agnostic so it works with any custom host name or character, with distinct phosphor
// tints by vocal register and live states for idle, speaking, listening, happy, and wince.

export type HostCrtState = 'idle' | 'speaking' | 'listening' | 'happy' | 'wince' | 'thinking'

type Props = {
  state?: HostCrtState
  /** Optional subtle phosphor tint derived from the host's register or choice. */
  tone?: 'accent' | 'brand' | 'cyan' | 'rose'
  size?: 'sm' | 'md'
}

const TONE_COLOR: Record<NonNullable<Props['tone']>, string> = {
  accent: 'var(--accent)',
  brand: 'var(--brand)',
  cyan: '#38e8ff',
  rose: '#ff5c93',
}

export function hostToneFor(register?: string | null): NonNullable<Props['tone']> {
  if (register === 'brighter') return 'cyan'
  if (register === 'deeper') return 'accent'
  if (register === 'middle') return 'rose'
  return 'accent'
}

export function HostCrt({ state = 'idle', tone = 'accent', size = 'md' }: Props) {
  const phosphor = state === 'listening' ? 'var(--bad)' : state === 'happy' ? 'var(--good)' : state === 'wince' ? 'var(--bad)' : TONE_COLOR[tone]
  const dim = size === 'sm' ? 'h-10 w-11' : 'h-11 w-12 sm:h-13 sm:w-14'

  return (
    <div
      className={`host-crt relative flex-none ${dim}`}
      data-state={state}
      style={{ ['--crt-color' as string]: phosphor }}
      aria-hidden="true"
    >
      <svg viewBox="0 0 48 44" className="h-full w-full overflow-visible">
        {/* Outer arcade CRT casing */}
        <rect x="1" y="1" width="46" height="40" rx="5" fill="#15122b" stroke="currentColor" strokeOpacity="0.35" strokeWidth="2" />
        {/* Inner screen recess */}
        <rect x="5" y="5" width="38" height="28" rx="3" fill="#06050f" stroke="#000" strokeWidth="1.5" />

        {/* Subtle phosphor tube glow */}
        <rect x="6" y="6" width="36" height="26" rx="2" fill="var(--crt-color)" fillOpacity="0.12" />

        {/* Screen face / oscilloscope content */}
        {state === 'happy' ? (
          <g stroke="var(--crt-color)" strokeWidth="2.4" strokeLinecap="square" fill="none" className="crt-glow">
            {/* Happy chevron eyes */}
            <path d="M13 16 L16.5 12.5 L20 16" />
            <path d="M28 16 L31.5 12.5 L35 16" />
            {/* Grinning arc */}
            <path d="M14 22 Q24 29 34 22" />
          </g>
        ) : state === 'wince' ? (
          <g stroke="var(--crt-color)" strokeWidth="2.3" strokeLinecap="square" fill="none" className="crt-glow">
            {/* X / wince eyes */}
            <path d="M14 12 L19 17 M19 12 L14 17" />
            <path d="M29 12 L34 17 M34 12 L29 17" />
            {/* Jagged static mouth */}
            <path d="M12 24 L16 21 L20 25 L24 21 L28 25 L32 21 L36 24" />
          </g>
        ) : state === 'listening' ? (
          <g className="crt-glow">
            {/* Mic-visor / radar pulse */}
            <circle cx="24" cy="19" r="8" fill="none" stroke="var(--crt-color)" strokeWidth="1.6" strokeDasharray="3 2" className="crt-pulse" />
            <circle cx="24" cy="19" r="3.2" fill="var(--crt-color)" />
            <rect x="10" y="17.5" width="4" height="3" rx="1" fill="var(--crt-color)" fillOpacity="0.7" />
            <rect x="34" y="17.5" width="4" height="3" rx="1" fill="var(--crt-color)" fillOpacity="0.7" />
          </g>
        ) : (
          <g className="crt-glow">
            {/* Twin CRT eyes */}
            <rect x="14" y="11" width="4" height="4" rx="0.5" fill="var(--crt-color)" className={state === 'idle' ? 'crt-eye' : undefined} />
            <rect x="30" y="11" width="4" height="4" rx="0.5" fill="var(--crt-color)" className={state === 'idle' ? 'crt-eye' : undefined} />
            {/* Oscilloscope / vocal bars */}
            <g className="crt-bars" fill="var(--crt-color)">
              <rect x="11" y="19" width="3" height="9" rx="0.5" className="crt-b1" />
              <rect x="16" y="19" width="3" height="9" rx="0.5" className="crt-b2" />
              <rect x="21" y="19" width="3" height="9" rx="0.5" className="crt-b3" />
              <rect x="26" y="19" width="3" height="9" rx="0.5" className="crt-b4" />
              <rect x="31" y="19" width="3" height="9" rx="0.5" className="crt-b5" />
              <rect x="36" y="19" width="2.5" height="9" rx="0.5" className="crt-b6" />
            </g>
          </g>
        )}

        {/* CRT horizontal scanlines over the tube */}
        <g stroke="#000" strokeOpacity="0.34" strokeWidth="1">
          <line x1="6" y1="9" x2="42" y2="9" />
          <line x1="6" y1="13" x2="42" y2="13" />
          <line x1="6" y1="17" x2="42" y2="17" />
          <line x1="6" y1="21" x2="42" y2="21" />
          <line x1="6" y1="25" x2="42" y2="25" />
          <line x1="6" y1="29" x2="42" y2="29" />
        </g>

        {/* Subtle glass sheen */}
        <path d="M7 7 L20 7 L11 16 L7 16 Z" fill="#fff" fillOpacity="0.09" />

        {/* Bottom bezel status strip & power LED */}
        <rect x="9" y="35.5" width="14" height="2" rx="1" fill="#fff" fillOpacity="0.16" />
        <circle cx="38" cy="36.5" r="1.8" fill={state === 'speaking' || state === 'listening' ? 'var(--crt-color)' : '#2fbf71'} />
      </svg>
    </div>
  )
}
