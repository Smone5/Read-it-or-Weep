// Crisp pixel-grid SVG icons for the arcade UI.
// Every icon uses currentColor and aria-hidden="true" so icons never trigger OS emoji substitution,
// always match button/text contrast, and survive Forced Colors Mode.

import type { SVGProps } from 'react'

type IconProps = SVGProps<SVGSVGElement> & { size?: number | string }

function Svg({ size = '1em', className = '', children, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
      className={`inline-block shrink-0 align-[-0.125em] ${className}`}
      {...rest}
    >
      {children}
    </svg>
  )
}

export function IconPlay(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 2h2v2h2v2h2v1h2v2h-2v1H8v2H6v2H4V2Z" />
    </Svg>
  )
}

export function IconPause(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3 2h4v12H3V2Zm6 0h4v12H9V2Z" />
    </Svg>
  )
}

export function IconStop(props: IconProps) {
  return (
    <Svg {...props}>
      <rect x="3" y="3" width="10" height="10" />
    </Svg>
  )
}

export function IconSkip(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M1 3h2v2h2v2h2v2H5v2H3v2H1V3Zm7 0h2v2h2v2h2v2h-2v2h-2v2H8V3Z" />
    </Svg>
  )
}

export function IconSoundOn(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M2 6h3l3-3h1v10H8l-3-3H2V6Zm9 0h1v4h-1V6Zm2-2h1v8h-1V4Z" />
    </Svg>
  )
}

export function IconSoundOff(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M2 6h3l3-3h1v10H8l-3-3H2V6Zm9 0h1v1h1V6h1v1h-1v2h1v1h-1V9h-1v1h-1V9h1V7h-1V6Z" />
    </Svg>
  )
}

export function IconMusic(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M6 2h8v2H8v7a2.5 2.5 0 1 1-2-2.45V2Zm6 2h2v6a2.5 2.5 0 1 1-2-2.45V4Z" />
    </Svg>
  )
}

export function IconMusicOff(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M6 2h8v2H8v3.5L6 5.5V2Zm6 2h2v5.5l-2-2V4ZM2 2.5 3.4 1.1 14.9 12.6 13.5 14 10 10.5V11a2.5 2.5 0 1 1-2-2.45L2 2.5Z" />
    </Svg>
  )
}

export function IconClose(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3 3h2v2h2v2h2V5h2V3h2v2h-2v2H9v2h2v2h2v2h-2v-2H9V9H7v2H5v2H3v-2h2V9h2V7H5V5H3V3Z" />
    </Svg>
  )
}

export function IconCheck(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M13 3h2v2h-2v2h-2v2H9v2H7v2H5v-2H3V9H1V7h2v2h2v2h2V9h2V7h2V5h2V3Z" />
    </Svg>
  )
}

export function IconFlag(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3 2h2v12H3V2Zm2 0h8v2h-1v2h1v2H5V2Z" />
    </Svg>
  )
}

export function IconStar(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M7 1h2v4h5v2h-2v2h-1v2h1v3h-2v-1H8v-1H6v1H4v-3h1V9H4V7H2V5h5V1Z" />
    </Svg>
  )
}

export function IconHeart(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3 2h3v1h1v1h2V3h1V2h3v1h1v1h1v4h-1v2h-2v2h-2v2H9v1H7v-1H5v-2H3v-2H2V8H1V4h1V3h1V2Z" />
    </Svg>
  )
}

export function IconCrown(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M1 4h2v2h2V4h2V2h2v2h2v2h2V4h2v9H1V4Zm2 7h10v-1H3v1Z" />
    </Svg>
  )
}

export function IconTrophy(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M4 2h8v1h3v4h-1v2h-2v1h-2v2h2v2H4v-2h2v-2H4V9H2V7H1V3h3V2Zm-1 3v2h1V5H3Zm9 0v2h1V5h-1Z" />
    </Svg>
  )
}

export function IconBolt(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M9 1h2L8 7h5l-6 8H5l2-6H3l6-8Z" />
    </Svg>
  )
}

export function IconDisc(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5 1h6v1h2v1h1v2h1v6h-1v2h-1v1h-2v1H5v-1H3v-1H2v-2H1V5h1V3h1V2h2V1Zm2 5v4h2V9h1V7H9V6H7Z" />
    </Svg>
  )
}

export function IconChart(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M1 2h2v11h12v2H1V2Zm4 7h2v3H5V9Zm3-4h2v7H8V5Zm3 2h2v5h-2V7Z" />
    </Svg>
  )
}

export function IconChip(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5 1h6v1h2v1h1v2h1v6h-1v2h-1v1h-2v1H5v-1H3v-1H2v-2H1V5h1V3h1V2h2V1Zm1 3v2H4v4h2v2h4v-2h2V6h-2V4H6Zm1 3h2v2H7V7Z" />
    </Svg>
  )
}

export function IconBook(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M2 2h5v1h2V2h5v11H9v1H7v-1H2V2Zm2 2v7h3V4H4Zm5 0v7h3V4H9Z" />
    </Svg>
  )
}

export function IconBulb(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5 1h6v1h2v5h-1v2h-1v2H5V9H4V7H3V2h2V1Zm1 11h4v1H6v-1Zm1 2h2v1H7v-1Z" />
    </Svg>
  )
}

export function IconSplit(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M2 3h5v4H2V3Zm7 0h5v4H9V3ZM2 9h5v4H2V9Zm7 0h5v4H9V9Zm1 1v2h3v-2h-3ZM3 10v2h3v-2H3Z" />
    </Svg>
  )
}

export function IconSparkle(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M7 1h2v4h1v1h5v2h-5v1H9v6H7V9H6V8H1V6h5V5h1V1Z" />
    </Svg>
  )
}

export function IconExpand(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M2 2h5v2H4v3H2V2Zm7 0h5v5h-2V4H9V2ZM2 9h2v3h3v2H2V9Zm10 0h2v5H9v-2h3V9Z" />
    </Svg>
  )
}

export function IconRotate(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M8 2h4v1h2v3h-2V4H6v2H4v4h2v2h4v-2h2v2h-1v2H5v-1H3v-2H2V5h1V3h2V2h3Zm3 1h3v3h-2V5h-1V3Z" />
    </Svg>
  )
}

export function IconZoomIn(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M7 3h2v4h4v2H9v4H7V9H3V7h4V3Z" />
    </Svg>
  )
}

export function IconZoomOut(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M3 7h10v2H3V7Z" />
    </Svg>
  )
}

export function IconExternal(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M7 2h7v7h-2V5.4l-5.3 5.3-1.4-1.4L10.6 4H7V2ZM2 5h3v2H4v5h5v-1h2v3H2V5Z" />
    </Svg>
  )
}

export function IconMic(props: IconProps) {
  return (
    <Svg viewBox="0 0 24 24" {...props}>
      <path d="M12 15a3.5 3.5 0 0 0 3.5-3.5v-5a3.5 3.5 0 0 0-7 0v5A3.5 3.5 0 0 0 12 15Zm6-3.5a1 1 0 1 0-2 0 4 4 0 0 1-8 0 1 1 0 1 0-2 0 6 6 0 0 0 5 5.9V20H9a1 1 0 1 0 0 2h6a1 1 0 1 0 0-2h-2v-2.6a6 6 0 0 0 5-5.9Z" />
    </Svg>
  )
}

export function IconArrowLeft(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M7 3h2v2H7v2h7v2H7v2h2v2H7v-1H5v-1H4v-1H3V9H2V7h1V6h1V5h1V4h2V3Z" />
    </Svg>
  )
}

export function IconSearch(props: IconProps) {
  return (
    <Svg {...props}>
      <path d="M5 2h4v1h2v2h1v4h-1v1l3.5 3.5-1.4 1.4L9.6 11.4H9v1H5v-1H3V9H2V5h1V3h2V2Zm0 2v1H4v4h1v1h4V9h1V5H9V4H5Z" />
    </Svg>
  )
}
