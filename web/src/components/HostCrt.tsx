// Animated retro CRT oscilloscope monitor for the talking game-show host.
// Brand-agnostic so it works with any custom host name or character, with distinct phosphor
// tints by vocal register and live states for idle, speaking, listening, happy, and wince.
//
// Animated eyes, vocal bars, and radar pulse are rendered as plain HTML elements inside the
// CRT tube overlay (rather than SVG <rect>s inside a filtered <g>) so iOS WebKit composites
// transform animations smoothly on the GPU instead of freezing on static frames.

export type HostCrtState = 'idle' | 'speaking' | 'listening' | 'happy' | 'wince' | 'thinking'

type Props = {
  state?: HostCrtState
  /** Emotional mood kept on the phosphor even while the host is speaking. */
  mood?: 'idle' | 'happy' | 'wince'
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
  if (register === 'hype') return 'brand'
  return 'accent'
}

export function HostCrt({ state = 'idle', mood = 'idle', tone = 'accent', size = 'md' }: Props) {
  const effectiveMood = state === 'happy' || state === 'wince' ? state : mood
  const phosphor =
    state === 'listening'
      ? 'var(--bad)'
      : effectiveMood === 'happy'
        ? 'var(--good)'
        : effectiveMood === 'wince'
          ? 'var(--bad)'
          : TONE_COLOR[tone]
  const dim = size === 'sm' ? 'h-10 w-11' : 'h-11 w-12 sm:h-13 sm:w-14'
  const showHtmlFace = state === 'idle' || state === 'speaking' || state === 'thinking' || state === 'listening'

  return (
    <div
      className={`host-crt relative flex-none ${dim}`}
      data-state={state}
      style={{ ['--crt-color' as string]: phosphor }}
      aria-hidden="true"
    >
      {/* Static CRT monitor casing & non-animated expression faces */}
      <svg viewBox="0 0 48 44" className="crt-casing h-full w-full overflow-visible">
        <rect x="1" y="1" width="46" height="40" rx="5" fill="#15122b" stroke="currentColor" strokeOpacity="0.35" strokeWidth="2" />
        <rect x="5" y="5" width="38" height="28" rx="3" fill="#06050f" stroke="#000" strokeWidth="1.5" />
        <rect x="6" y="6" width="36" height="26" rx="2" fill="var(--crt-color)" fillOpacity="0.12" />

        {state === 'happy' && (
          <g stroke="var(--crt-color)" strokeWidth="2.4" strokeLinecap="square" fill="none" className="crt-glow">
            <path d="M13 16 L16.5 12.5 L20 16" />
            <path d="M28 16 L31.5 12.5 L35 16" />
            <path d="M14 22 Q24 29 34 22" />
          </g>
        )}
        {state === 'wince' && (
          <g stroke="var(--crt-color)" strokeWidth="2.3" strokeLinecap="square" fill="none" className="crt-glow">
            <path d="M14 12 L19 17 M19 12 L14 17" />
            <path d="M29 12 L34 17 M34 12 L29 17" />
            <path d="M12 24 L16 21 L20 25 L24 21 L28 25 L32 21 L36 24" />
          </g>
        )}

        {/* Bottom bezel status strip & power LED */}
        <rect x="9" y="35.5" width="14" height="2" rx="1" fill="#fff" fillOpacity="0.16" />
        <circle cx="38" cy="36.5" r="1.8" fill={state === 'speaking' || state === 'listening' ? 'var(--crt-color)' : '#2fbf71'} />
      </svg>

      {/* GPU-composited HTML face + glass scanlines inside the CRT recess (x=6..42, y=6..32 of 48×44) */}
      <div className="crt-screen pointer-events-none absolute left-[12.5%] top-[13.6%] h-[59.1%] w-[75%] overflow-hidden rounded-[2px]">
        {state === 'listening' ? (
          <div className="relative flex h-full w-full items-center justify-center">
            <span className="crt-visor-wing left-[8%]" />
            <span className="crt-pulse" />
            <span className="crt-visor-core" />
            <span className="crt-visor-wing right-[8%]" />
          </div>
        ) : showHtmlFace ? (
          <div className="flex h-full w-full flex-col justify-between px-[11%] pb-[13%] pt-[16%]">
            <div className="flex items-center justify-between px-[10%]">
              <span className="crt-eye" />
              <span className="crt-eye" />
            </div>
            <div className="crt-bars">
              <span className="crt-bar crt-b1" />
              <span className="crt-bar crt-b2" />
              <span className="crt-bar crt-b3" />
              <span className="crt-bar crt-b4" />
              <span className="crt-bar crt-b5" />
              <span className="crt-bar crt-b6" />
            </div>
          </div>
        ) : null}
        <div className="crt-glass" />
      </div>
    </div>
  )
}
