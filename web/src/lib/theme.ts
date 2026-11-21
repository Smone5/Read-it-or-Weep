import type { Branding } from './types'

function luminance(hex: string): number {
  const value = hex.replace('#', '')
  const channel = (index: number) => {
    const c = parseInt(value.slice(index, index + 2), 16) / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(0) + 0.7152 * channel(2) + 0.0722 * channel(4)
}

/** Pick dark or white text on a dynamic fill color near the WCAG AA crossover (~0.20). */
export function contrastInk(hex: string, darkInk = '#14112b'): string {
  return luminance(hex) > 0.2 ? darkInk : '#ffffff'
}

/** Apply the company's colours, theme and title to the page. Safe to call repeatedly. */
export function applyBranding(branding: Branding | null | undefined) {
  const root = document.documentElement
  if (!branding) {
    root.dataset.theme = root.dataset.theme ?? 'dark'
    return
  }
  root.dataset.theme = branding.theme
  root.style.setProperty('--brand', branding.primaryColor)
  root.style.setProperty('--accent', branding.accentColor)
  root.style.setProperty('--brand-ink', contrastInk(branding.primaryColor, '#14112b'))
  root.style.setProperty('--accent-ink', contrastInk(branding.accentColor, '#1c1300'))
  document.title = branding.gameTitle
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', branding.theme === 'dark' ? '#0d0b1f' : '#f4f2fb')
}

/** Tint the backdrop with a decade's colours (or clear it). */
export function applyDecade(decade: string | null | undefined) {
  const root = document.documentElement
  if (decade) root.dataset.decade = decade
  else delete root.dataset.decade
}

export const DECADE_LABEL: Record<string, string> = {
  '50s': '1950s',
  '60s': '1960s',
  '70s': '1970s',
  '80s': '1980s',
  '90s': '1990s',
  '00s': '2000s',
  '10s': '2010s',
  '20s': '2020s',
}

export const DECADE_FLAVOUR: Record<string, string> = {
  '50s': 'Jukeboxes & milkshakes',
  '60s': 'Flower power',
  '70s': 'Disco & vinyl',
  '80s': 'Neon & synths',
  '90s': 'Mixtapes & grunge',
  '00s': 'Ringtones & iPods',
  '10s': 'Streaming era',
  '20s': 'Right about now',
}

export const DECADE_SWATCH: Record<string, [string, string]> = {
  '50s': ['#e63946', '#7fd6c2'],
  '60s': ['#ff6b35', '#9d4edd'],
  '70s': ['#e9843c', '#c9a227'],
  '80s': ['#ff2e93', '#00e5ff'],
  '90s': ['#00b4d8', '#ffd60a'],
  '00s': ['#4cc9f0', '#c77dff'],
  '10s': ['#2ec4b6', '#ff9f1c'],
  '20s': ['#a3e635', '#c084fc'],
}
