// Spoiler-free score sharing, "Beat my score" challenge links,
// client-side retro-arcade Trophy Card PNG, and Readiness Certificate PNG.
// Zero report content ever leaves the app — safe for confidential reports.

import type { BadgeInfo, Branding, ModeId, Summary } from './types'

export type Challenge = {
  score: number
  by: string
  badge: string | null
  mode: ModeId | null
}

const VALID_MODES = new Set<ModeId>(['show', 'sudden_death', 'speed', 'straight', 'jukebox'])

export function parseChallenge(params: URLSearchParams): Challenge | null {
  const rawScore = (params.get('beat') || '').trim()
  if (!/^\d{1,7}$/.test(rawScore)) return null
  const score = Number.parseInt(rawScore, 10)
  if (!Number.isFinite(score) || score <= 0) return null
  const by = (params.get('by') || 'A challenger').trim().slice(0, 32) || 'A challenger'
  const badge = (params.get('badge') || '').trim().slice(0, 40) || null
  const rawMode = (params.get('mode') || '').trim() as ModeId
  const mode = VALID_MODES.has(rawMode) ? rawMode : null
  return { score, by, badge, mode }
}

function firstName(fullName?: string | null): string {
  const clean = (fullName || '').trim()
  if (!clean) return 'Player'
  return clean.split(/\s+/)[0].slice(0, 24)
}

export function buildChallengeUrl(
  slug: string,
  opts: { score: number; playerName?: string | null; badge?: string | null; mode: ModeId },
): string {
  const url = new URL(`/q/${slug}`, window.location.origin)
  if (opts.score > 0) url.searchParams.set('beat', String(opts.score))
  const by = firstName(opts.playerName)
  if (by) url.searchParams.set('by', by)
  if (opts.badge) url.searchParams.set('badge', opts.badge)
  url.searchParams.set('mode', opts.mode)
  return url.toString()
}

function emojiGrid(results?: boolean[]): string {
  if (!results || results.length === 0) return ''
  const capped = results.slice(0, 20)
  const cells = capped.map((ok) => (ok ? '🟩' : '🟥'))
  if (cells.length <= 10) return cells.join('')
  return `${cells.slice(0, 10).join('')}\n${cells.slice(10).join('')}${results.length > 20 ? '…' : ''}`
}

function tierEmoji(tone?: BadgeInfo['tone']): string {
  switch (tone) {
    case 'legend': return '👑'
    case 'gold': return '🏆'
    case 'good': return '⭐'
    case 'brand': return '📘'
    case 'warn': return '⚡'
    case 'bad': return '💡'
    default: return '🏁'
  }
}

export function buildShareText(opts: {
  slug: string
  quizTitle: string
  gameTitle: string
  playerName?: string | null
  summary: Summary
  includeLink?: boolean
}): string {
  const { slug, quizTitle, gameTitle, playerName, summary, includeLink = true } = opts
  const badge = summary.badgeInfo?.label ?? summary.badge
  const icon = summary.mode === 'straight'
    ? (summary.passed ? '✅' : '📘')
    : tierEmoji(summary.badgeInfo?.tone)
  const rankStr = summary.rank ? ` (#${summary.rank} of ${summary.players})` : ''
  const headline = summary.mode === 'straight'
    ? `${icon} ${summary.passed ? 'Passed' : 'In progress'} · ${summary.accuracy}%${rankStr}`
    : `${icon} ${badge ?? summary.modeName} · ${summary.score.toLocaleString()} pts${rankStr}`
  const grid = emojiGrid(summary.resultsGrid)
  const statsLine = `${summary.accuracy}% accuracy${summary.streakMax > 1 ? ` · x${summary.streakMax} streak` : ''}`
  const link = includeLink
    ? buildChallengeUrl(slug, {
        score: summary.mode === 'straight' ? summary.accuracy : summary.score,
        playerName,
        badge,
        mode: summary.mode,
      })
    : null
  const lines = [
    includeLink ? `${gameTitle} — ${quizTitle}` : gameTitle,
    headline,
    ...(grid ? [`${grid} (${statsLine})`] : [statsLine]),
    ...(link ? [`Think you can beat me? ${link}`] : []),
  ]
  return lines.join('\n')
}

// ---- Canvas drawing helpers --------------------------------------------------

function roundRect(ctx: CanvasRenderingContext2D, x: number, y: number, w: number, h: number, r: number) {
  ctx.beginPath()
  ctx.moveTo(x + r, y)
  ctx.arcTo(x + w, y, x + w, y + h, r)
  ctx.arcTo(x + w, y + h, x, y + h, r)
  ctx.arcTo(x, y + h, x, y, r)
  ctx.arcTo(x, y, x + w, y, r)
  ctx.closePath()
}

function wrapLines(ctx: CanvasRenderingContext2D, text: string, maxWidth: number, maxLines = 2): string[] {
  const words = text.trim().split(/\s+/)
  const lines: string[] = []
  let current = ''
  for (const word of words) {
    const candidate = current ? `${current} ${word}` : word
    if (ctx.measureText(candidate).width <= maxWidth) {
      current = candidate
    } else {
      if (current) lines.push(current)
      current = word
      if (lines.length === maxLines - 1) break
    }
  }
  if (current) lines.push(current)
  if (lines.length > maxLines) lines.length = maxLines
  const last = lines[lines.length - 1]
  if (last && ctx.measureText(last).width > maxWidth) {
    let trimmed = last
    while (trimmed.length > 3 && ctx.measureText(`${trimmed}…`).width > maxWidth) {
      trimmed = trimmed.slice(0, -1)
    }
    lines[lines.length - 1] = `${trimmed}…`
  }
  return lines
}

function toneHex(tone: BadgeInfo['tone'] | undefined, brand: string, accent: string): string {
  switch (tone) {
    case 'legend': return '#38E8FF'
    case 'gold': return accent || '#FFB020'
    case 'good': return '#2FBF71'
    case 'brand': return brand || '#6D5EF8'
    case 'warn': return '#F59E0B'
    case 'bad': return '#F0506E'
    default: return '#94A3B8'
  }
}

function canvasToBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) resolve(blob)
      else reject(new Error('Could not export image'))
    }, 'image/png')
  })
}

export async function renderTrophyCardPng(opts: {
  branding: Branding
  quizTitle: string
  playerName?: string | null
  team?: string | null
  summary: Summary
}): Promise<Blob> {
  const { branding, quizTitle, playerName, team, summary } = opts
  const W = 1200
  const H = 630
  const canvas = document.createElement('canvas')
  canvas.width = W
  canvas.height = H
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Canvas unavailable')

  const brand = branding.primaryColor || '#6D5EF8'
  const accent = branding.accentColor || '#FFB020'
  const medalColor = summary.mode === 'straight'
    ? (summary.passed ? '#2FBF71' : '#F0506E')
    : toneHex(summary.badgeInfo?.tone, brand, accent)

  // Deep retro-arcade background
  ctx.fillStyle = '#0D0B1F'
  ctx.fillRect(0, 0, W, H)

  const glow1 = ctx.createRadialGradient(220, 120, 20, 220, 120, 520)
  glow1.addColorStop(0, `${brand}44`)
  glow1.addColorStop(1, 'transparent')
  ctx.fillStyle = glow1
  ctx.fillRect(0, 0, W, H)

  const glow2 = ctx.createRadialGradient(980, 520, 20, 980, 520, 480)
  glow2.addColorStop(0, `${accent}33`)
  glow2.addColorStop(1, 'transparent')
  ctx.fillStyle = glow2
  ctx.fillRect(0, 0, W, H)

  // Subtle scanlines
  ctx.fillStyle = 'rgba(255,255,255,0.025)'
  for (let y = 0; y < H; y += 6) ctx.fillRect(0, y, W, 2)

  // Outer arcade cabinet frame
  roundRect(ctx, 28, 28, W - 56, H - 56, 16)
  ctx.fillStyle = 'rgba(25, 22, 52, 0.88)'
  ctx.fill()
  ctx.lineWidth = 3
  ctx.strokeStyle = medalColor
  ctx.stroke()

  // Mode pill on top right (measured first so header text never collides with it)
  const modeLabel = summary.modeName.toUpperCase()
  ctx.font = '700 16px ui-monospace, SFMono-Regular, Menlo, monospace'
  const pillW = Math.min(340, ctx.measureText(modeLabel).width + 32)
  const pillX = W - 64 - pillW
  roundRect(ctx, pillX, 56, pillW, 34, 6)
  ctx.fillStyle = 'rgba(255,255,255,0.08)'
  ctx.fill()
  ctx.lineWidth = 1.5
  ctx.strokeStyle = 'rgba(255,255,255,0.2)'
  ctx.stroke()
  ctx.fillStyle = '#F6F4FF'
  ctx.fillText(modeLabel, pillX + 16, 79, pillW - 32)

  // Top header bar (bounded to stop cleanly before the mode pill)
  const headerMaxW = Math.max(200, pillX - 64 - 24)
  const headerTitle = branding.gameTitle.toUpperCase()
  ctx.fillStyle = '#A9A4CC'
  ctx.font = '700 18px ui-monospace, SFMono-Regular, Menlo, monospace'
  const titleW = Math.min(headerMaxW, ctx.measureText(headerTitle).width)
  ctx.fillText(headerTitle, 64, 82, headerMaxW)
  if (branding.companyName && headerMaxW - titleW > 80) {
    const company = `·  ${branding.companyName.toUpperCase()}`
    ctx.fillStyle = 'rgba(255,255,255,0.55)'
    ctx.fillText(company, 64 + titleW + 14, 82, headerMaxW - titleW - 14)
  }

  // Report title
  ctx.fillStyle = '#F6F4FF'
  ctx.font = '800 32px system-ui, -apple-system, Segoe UI, Roboto, sans-serif'
  const titleLines = wrapLines(ctx, quizTitle, W - 128, 2)
  titleLines.forEach((line, i) => ctx.fillText(line, 64, 134 + i * 40, W - 128))
  const afterTitleY = 134 + titleLines.length * 40 + 18

  // Right hero: Glowing Badge Medal (compute box first so left column has an exact non-overlapping width)
  const badgeText = summary.mode === 'straight'
    ? (summary.passed ? `PASSED · ${summary.accuracy}%` : `NOT YET · ${summary.accuracy}%`)
    : (summary.badgeInfo?.label ?? summary.badge ?? summary.modeName).toUpperCase()
  ctx.font = '800 22px ui-monospace, SFMono-Regular, Menlo, monospace'
  const badgeW = Math.min(440, Math.max(250, ctx.measureText(badgeText).width + 58))
  const badgeX = W - 64 - badgeW
  const badgeY = afterTitleY + 8
  roundRect(ctx, badgeX, badgeY, badgeW, 70, 10)
  ctx.fillStyle = `${medalColor}26`
  ctx.fill()
  ctx.lineWidth = 3
  ctx.strokeStyle = medalColor
  ctx.stroke()
  ctx.fillStyle = '#FFFFFF'
  ctx.textAlign = 'center'
  ctx.fillText(badgeText, badgeX + badgeW / 2, badgeY + 43, badgeW - 28)
  ctx.textAlign = 'left'

  if (summary.badgeInfo?.description) {
    ctx.fillStyle = '#A9A4CC'
    ctx.font = '500 16px system-ui, -apple-system, Segoe UI, Roboto, sans-serif'
    const descLines = wrapLines(ctx, summary.badgeInfo.description, badgeW, 2)
    descLines.forEach((l, i) => {
      ctx.textAlign = 'right'
      ctx.fillText(l, W - 64, badgeY + 98 + i * 22, badgeW)
    })
    ctx.textAlign = 'left'
  }

  // Left hero: Score + Player (measure score BEFORE switching font so "PTS" never overlaps the digits!)
  const leftMaxW = Math.max(300, badgeX - 64 - 28)
  ctx.fillStyle = '#FFFFFF'
  ctx.shadowColor = accent
  ctx.shadowBlur = 24
  ctx.font = '900 84px ui-monospace, SFMono-Regular, Menlo, monospace'
  const scoreText = `${summary.score.toLocaleString()}`
  const scoreMaxW = leftMaxW - 76
  const measuredScoreW = Math.min(scoreMaxW, ctx.measureText(scoreText).width)
  ctx.fillText(scoreText, 64, afterTitleY + 64, scoreMaxW)
  ctx.shadowBlur = 0
  ctx.fillStyle = accent
  ctx.font = '800 26px ui-monospace, SFMono-Regular, Menlo, monospace'
  ctx.fillText('PTS', 64 + measuredScoreW + 18, afterTitleY + 60)

  // Player & rank line (constrained to left column so it never collides with badge description)
  const who = [playerName?.trim() || 'Player', team?.trim() || ''].filter(Boolean).join(' · ')
  ctx.fillStyle = '#C9C4E8'
  ctx.font = '600 21px system-ui, -apple-system, Segoe UI, Roboto, sans-serif'
  const rankPart = summary.rank ? ` · #${summary.rank} of ${summary.players}` : ''
  const whoLines = wrapLines(ctx, `${who}${rankPart}`, leftMaxW, 1)
  ctx.fillText(whoLines[0] ?? who, 64, afterTitleY + 112, leftMaxW)

  // Wordle-style arcade answer blocks
  const grid = summary.resultsGrid ?? []
  const gridY = 408
  if (grid.length > 0) {
    const shown = grid.slice(0, 20)
    const box = 28
    const gap = 8
    shown.forEach((ok, idx) => {
      const x = 64 + idx * (box + gap)
      roundRect(ctx, x, gridY, box, box, 5)
      ctx.fillStyle = ok ? '#2FBF71' : '#F0506E'
      ctx.fill()
      ctx.fillStyle = 'rgba(255,255,255,0.22)'
      ctx.fillRect(x + 4, gridY + 3, box - 8, 4)
    })
  }

  // 3-column HUD strip
  const hudY = 464
  const cardW = (W - 128 - 32) / 3
  const isJukebox = summary.mode === 'jukebox'
  const stats = [
    { label: isJukebox ? 'MUSIC HITS' : 'REPORT HITS', value: isJukebox ? `${summary.music.correct}/${summary.music.answered}` : `${summary.report.correct}/${summary.report.answered}` },
    { label: 'ACCURACY', value: `${summary.accuracy}%` },
    { label: 'BEST STREAK', value: `x${summary.streakMax}` },
  ]
  stats.forEach((s, i) => {
    const x = 64 + i * (cardW + 16)
    roundRect(ctx, x, hudY, cardW, 78, 10)
    ctx.fillStyle = 'rgba(255,255,255,0.05)'
    ctx.fill()
    ctx.lineWidth = 1.5
    ctx.strokeStyle = 'rgba(255,255,255,0.12)'
    ctx.stroke()
    ctx.fillStyle = '#A9A4CC'
    ctx.font = '700 13px ui-monospace, SFMono-Regular, Menlo, monospace'
    ctx.fillText(s.label, x + 20, hudY + 28)
    ctx.fillStyle = '#FFFFFF'
    ctx.font = '800 30px ui-monospace, SFMono-Regular, Menlo, monospace'
    ctx.fillText(s.value, x + 20, hudY + 62)
  })

  // Footer
  ctx.fillStyle = 'rgba(255,255,255,0.45)'
  ctx.font = '500 15px system-ui, -apple-system, Segoe UI, Roboto, sans-serif'
  ctx.fillText('Spoiler-free scorecard · No report contents shared', 64, H - 48)
  ctx.textAlign = 'right'
  ctx.fillStyle = accent
  ctx.font = '700 15px ui-monospace, SFMono-Regular, Menlo, monospace'
  ctx.fillText('CAN YOU BEAT THIS SCORE?', W - 64, H - 48)
  ctx.textAlign = 'left'

  return canvasToBlob(canvas)
}

export async function renderCertificatePng(opts: {
  branding: Branding
  quizTitle: string
  playerName?: string | null
  team?: string | null
  summary: Summary
}): Promise<Blob> {
  const { branding, quizTitle, playerName, team, summary } = opts
  const W = 1200
  const H = 800
  const canvas = document.createElement('canvas')
  canvas.width = W
  canvas.height = H
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('Canvas unavailable')

  const gold = branding.accentColor || '#FFB020'
  const brand = branding.primaryColor || '#6D5EF8'

  // Crisp executive navy backdrop
  ctx.fillStyle = '#0F172A'
  ctx.fillRect(0, 0, W, H)

  // Double gold certificate border
  roundRect(ctx, 32, 32, W - 64, H - 64, 14)
  ctx.fillStyle = '#151E34'
  ctx.fill()
  ctx.lineWidth = 4
  ctx.strokeStyle = gold
  ctx.stroke()

  roundRect(ctx, 48, 48, W - 96, H - 96, 8)
  ctx.lineWidth = 1.5
  ctx.strokeStyle = 'rgba(255, 176, 32, 0.45)'
  ctx.stroke()

  // Header ribbon
  ctx.textAlign = 'center'
  ctx.fillStyle = gold
  ctx.font = '800 18px ui-monospace, SFMono-Regular, Menlo, monospace'
  ctx.fillText((branding.companyName || branding.gameTitle).toUpperCase(), W / 2, 108, W - 180)

  ctx.fillStyle = '#FFFFFF'
  ctx.font = '900 44px system-ui, -apple-system, Segoe UI, Roboto, sans-serif'
  const heading = summary.mode === 'straight' && summary.passed
    ? 'CERTIFICATE OF COMPLETION'
    : 'CERTIFICATE OF READINESS'
  ctx.fillText(heading, W / 2, 166, W - 180)

  // Gold divider
  ctx.fillStyle = gold
  ctx.fillRect(W / 2 - 120, 188, 240, 3)

  ctx.fillStyle = '#94A3B8'
  ctx.font = '600 18px ui-monospace, SFMono-Regular, Menlo, monospace'
  ctx.fillText('THIS CERTIFIES THAT', W / 2, 240)

  // Recipient name
  ctx.fillStyle = '#FFFFFF'
  ctx.font = '800 54px system-ui, -apple-system, Segoe UI, Roboto, sans-serif'
  ctx.fillText(playerName?.trim() || 'Player', W / 2, 306, W - 180)
  if (team?.trim()) {
    ctx.fillStyle = '#CBD5E1'
    ctx.font = '600 22px system-ui, -apple-system, Segoe UI, Roboto, sans-serif'
    ctx.fillText(team.trim(), W / 2, 342, W - 220)
  }

  ctx.fillStyle = '#94A3B8'
  ctx.font = '600 18px ui-monospace, SFMono-Regular, Menlo, monospace'
  ctx.fillText('HAS DEMONSTRATED MASTERY OF', W / 2, 398)

  ctx.fillStyle = gold
  ctx.font = '800 32px system-ui, -apple-system, Segoe UI, Roboto, sans-serif'
  const lines = wrapLines(ctx, quizTitle, W - 220, 2)
  lines.forEach((l, i) => ctx.fillText(l, W / 2, 446 + i * 40, W - 200))

  // 4 verified stat boxes
  const boxY = 532
  const boxW = 224
  const gap = 22
  const startX = (W - (boxW * 4 + gap * 3)) / 2
  const certStats = [
    { label: 'SECTIONS CLEARED', value: `${summary.readiness.cleared}/${summary.readiness.total} (${summary.readiness.percent}%)` },
    { label: 'RUN ACCURACY', value: `${summary.accuracy}%` },
    { label: summary.mode === 'straight' ? 'RESULT' : 'FINAL SCORE', value: summary.mode === 'straight' ? `${summary.report.correct}/${summary.report.answered} right` : `${summary.score.toLocaleString()} pts` },
    { label: 'RANK EARNED', value: summary.mode === 'straight' ? 'PASSED' : (summary.badgeInfo?.label ?? summary.badge ?? 'READY') },
  ]
  certStats.forEach((s, i) => {
    const x = startX + i * (boxW + gap)
    roundRect(ctx, x, boxY, boxW, 92, 10)
    ctx.fillStyle = 'rgba(255,255,255,0.04)'
    ctx.fill()
    ctx.lineWidth = 1.5
    ctx.strokeStyle = i === 0 ? '#2FBF71' : `${brand}88`
    ctx.stroke()
    ctx.textAlign = 'center'
    ctx.fillStyle = '#94A3B8'
    ctx.font = '700 12px ui-monospace, SFMono-Regular, Menlo, monospace'
    ctx.fillText(s.label, x + boxW / 2, boxY + 28, boxW - 20)
    ctx.fillStyle = '#FFFFFF'
    ctx.font = '800 19px system-ui, -apple-system, Segoe UI, Roboto, sans-serif'
    const valLines = wrapLines(ctx, s.value, boxW - 24, 2)
    if (valLines.length === 1) {
      ctx.fillText(valLines[0], x + boxW / 2, boxY + 64, boxW - 24)
    } else {
      ctx.font = '800 16px system-ui, -apple-system, Segoe UI, Roboto, sans-serif'
      const wrapped = wrapLines(ctx, s.value, boxW - 24, 2)
      wrapped.forEach((wl, wi) => ctx.fillText(wl, x + boxW / 2, boxY + 55 + wi * 20, boxW - 24))
    }
  })

  // Footer: verified date + seal
  const dateStr = new Date().toLocaleDateString(undefined, { year: 'numeric', month: 'long', day: 'numeric' })
  ctx.textAlign = 'left'
  ctx.fillStyle = '#94A3B8'
  ctx.font = '600 16px system-ui, -apple-system, Segoe UI, Roboto, sans-serif'
  ctx.fillText(`Issued ${dateStr} · ${branding.gameTitle}`, 84, H - 82, 560)

  ctx.textAlign = 'right'
  ctx.fillStyle = '#2FBF71'
  ctx.font = '800 16px ui-monospace, SFMono-Regular, Menlo, monospace'
  ctx.fillText('★ VERIFIED READY · SPOILER-FREE', W - 84, H - 82)
  ctx.textAlign = 'left'

  return canvasToBlob(canvas)
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1500)
}

export async function shareOrDownloadImage(
  blob: Blob,
  filename: string,
  title: string,
  text: string,
): Promise<'shared' | 'downloaded' | 'cancelled'> {
  const file = new File([blob], filename, { type: 'image/png' })
  if (typeof navigator !== 'undefined' && navigator.canShare && navigator.canShare({ files: [file] })) {
    try {
      await navigator.share({ files: [file], title, text })
      return 'shared'
    } catch (err) {
      if ((err as Error)?.name === 'AbortError') return 'cancelled'
    }
  }
  downloadBlob(blob, filename)
  return 'downloaded'
}
