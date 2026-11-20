import { useState } from 'react'
import { call, errorText } from './api'
import { Badge, ErrorBlock, LoadingBlock, Note, PageHeader } from './components'
import { useNotify, usePageTitle } from './context'
import { decadeLabel, plural, type Tone } from './format'
import { useInterval, useLoad } from './hooks'
import type { MusicStatus } from './types'

// A fill adds a track every few seconds for a couple of minutes. After pressing "Fill" the
// counts are re-read for this long so the numbers can be seen going up.
const WATCH_MS = 4 * 60_000
const CHECK_MS = 5000

// Rough bands for the label only. The game itself copes with any number: with a thin
// catalogue it looks up a well-known hit on the spot.
function stockOf(tracks: number): { label: string; tone: Tone } {
  if (tracks === 0) return { label: 'Empty', tone: 'warn' }
  if (tracks < 6) return { label: 'Just a few', tone: 'warn' }
  if (tracks < 26) return { label: 'Filling up', tone: 'info' }
  return { label: 'Ready', tone: 'good' }
}

export default function MusicPage() {
  usePageTitle('Music')
  const notify = useNotify()
  const { data, error, loading, reload, refresh } = useLoad<MusicStatus>('/music')
  const [asking, setAsking] = useState<string | null>(null)
  /** decade -> when "Fill" was pressed for it. */
  const [filling, setFilling] = useState<Record<string, number>>({})
  const [refreshing, setRefreshing] = useState(false)

  const watching = Object.keys(filling).length > 0

  useInterval(
    () => {
      if (document.hidden) return
      refresh().catch(() => {})
      setFilling((current) => {
        const kept = Object.fromEntries(Object.entries(current).filter(([, since]) => Date.now() - since < WATCH_MS))
        return Object.keys(kept).length === Object.keys(current).length ? current : kept
      })
    },
    watching ? CHECK_MS : null,
  )

  const fill = async (decade: string) => {
    if (asking) return
    setAsking(decade)
    try {
      // The server answers "filling in the background" whether or not the decade needed anything,
      // so the wording here promises only what is certain.
      await call<{ ok: boolean; message: string }>('/music/refresh', { method: 'POST', body: { decade } })
      setFilling((current) => ({ ...current, [decade]: Date.now() }))
      notify(`The ${decadeLabel(decade)} will be topped up if they are running low. New tracks appear over the next couple of minutes.`)
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      setAsking(null)
    }
  }

  const refreshNow = async () => {
    setRefreshing(true)
    try {
      await refresh()
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <>
      <PageHeader
        title="Music"
        description="Music breaks play a short clip and ask players to name the artist or the song. Each decade has its own list of well-known hits."
        actions={
          <button type="button" className="btn btn-ghost" onClick={refreshNow} disabled={refreshing || !data}>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        }
      />

      <div className="mb-5">
        <Note tone="info">
          <ul className="list-disc space-y-1 pl-5">
            <li>The clips are Apple's standard 30-second previews.</li>
            <li>
              Each list fills in the background, by itself, the first time a decade is needed. “Fill” starts that ahead of time; it takes a couple of
              minutes and you do not need to stay on this page.
            </li>
            <li>“Fill” only tops up a decade that is running low. For a decade that already has plenty of tracks it changes nothing.</li>
          </ul>
        </Note>
      </div>

      {!data && loading && <LoadingBlock label="Loading the music lists" />}
      {!data && error && <ErrorBlock message={error} onRetry={reload} />}
      {data && (
        <div className="ad-table-wrap">
          <table className="ad-table" style={{ minWidth: 460 }}>
            <thead>
              <tr>
                <th scope="col">Decade</th>
                <th scope="col" className="num">
                  Tracks
                </th>
                <th scope="col">Stock</th>
                <th scope="col">
                  <span className="sr-only">Fill</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {data.decades.map((row) => {
                const stock = stockOf(row.tracks)
                const isFilling = row.decade in filling
                const toppingUp = isFilling && stock.tone !== 'good' // a full decade stays "Ready": nothing is added to it
                return (
                  <tr key={row.decade}>
                    <th scope="row">
                      <span className="font-semibold">{decadeLabel(row.decade)}</span>
                    </th>
                    <td className="num">{row.tracks}</td>
                    <td>
                      <Badge tone={toppingUp ? 'info' : stock.tone}>{toppingUp ? 'Filling in the background' : stock.label}</Badge>
                    </td>
                    <td className="num">
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        onClick={() => void fill(row.decade)}
                        disabled={asking !== null || isFilling}
                        aria-label={`Fill the ${decadeLabel(row.decade)}`}
                      >
                        {asking === row.decade ? 'Starting…' : 'Fill'}
                      </button>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {data && (
        <p className="mt-3 text-sm text-muted" role="status">
          {plural(
            data.decades.reduce((sum, row) => sum + row.tracks, 0),
            'track',
          )}{' '}
          in all.{watching && ' The numbers on this page update by themselves for a few minutes.'} Which decades players can choose is set for each
          quiz, on its Play styles tab.
        </p>
      )}
    </>
  )
}
