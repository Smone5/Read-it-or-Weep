import { useCallback, useEffect, useRef, useState } from 'react'
import { call, errorText } from './api'
import { Badge, ErrorBlock, LoadingBlock, Note, PageHeader, Panel, Segmented } from './components'
import { useNotify, usePageTitle } from './context'
import { decadeLabel, plural, type Tone } from './format'
import { useInterval, useLoad } from './hooks'
import type { MusicPolicy, MusicSearchResult, MusicStatus, MusicTrackAdmin } from './types'

const WATCH_MS = 4 * 60_000
const CHECK_MS = 5000

type TrackFilter = 'all' | 'approved' | 'unapproved' | 'disabled'

const FILTER_OPTIONS: { value: TrackFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'approved', label: 'Approved' },
  { value: 'unapproved', label: 'Needs review' },
  { value: 'disabled', label: 'Disabled' },
]

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
  const [filling, setFilling] = useState<Record<string, number>>({})
  const [refreshing, setRefreshing] = useState(false)

  // Track browser state
  const [selectedDecade, setSelectedDecade] = useState<string>('80s')
  const [statusFilter, setStatusFilter] = useState<TrackFilter>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [tracks, setTracks] = useState<MusicTrackAdmin[]>([])
  const [tracksLoading, setTracksLoading] = useState(false)
  const [tracksError, setTracksError] = useState<string | null>(null)
  const [busyTrackId, setBusyTrackId] = useState<string | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)

  // Inline 30s audio preview player
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const [playingUrl, setPlayingUrl] = useState<string | null>(null)

  // Add-a-song catalog search
  const [catalogQuery, setCatalogQuery] = useState('')
  const [catalogResults, setCatalogResults] = useState<MusicSearchResult[]>([])
  const [catalogSearching, setCatalogSearching] = useState(false)
  const [addingKey, setAddingKey] = useState<string | null>(null)

  // Org-wide blocklist policy
  const [blockedArtists, setBlockedArtists] = useState<string[]>([])
  const [blockedTitles, setBlockedTitles] = useState<string[]>([])
  const [artistDraft, setArtistDraft] = useState('')
  const [titleDraft, setTitleDraft] = useState('')
  const [policyDirty, setPolicyDirty] = useState(false)
  const [savingPolicy, setSavingPolicy] = useState(false)

  useEffect(() => {
    if (data?.policy && !policyDirty) {
      setBlockedArtists(data.policy.blocked_artists ?? [])
      setBlockedTitles(data.policy.blocked_titles ?? [])
    }
  }, [data?.policy, policyDirty])

  const stopPreview = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.src = ''
      audioRef.current = null
    }
    setPlayingUrl(null)
  }, [])

  useEffect(() => () => stopPreview(), [stopPreview])

  const togglePreview = (url: string) => {
    if (playingUrl === url) {
      stopPreview()
      return
    }
    stopPreview()
    const audio = new Audio(url)
    audio.volume = 0.75
    audio.onended = () => setPlayingUrl((cur) => (cur === url ? null : cur))
    audio.onerror = () => {
      setPlayingUrl((cur) => (cur === url ? null : cur))
      notify('Could not play that preview clip.', 'bad')
    }
    audioRef.current = audio
    setPlayingUrl(url)
    audio.play().catch(() => {
      setPlayingUrl(null)
      notify('Preview playback was blocked by the browser.', 'bad')
    })
  }

  const loadTracks = useCallback(
    async (decade: string, status: TrackFilter, q: string) => {
      setTracksLoading(true)
      setTracksError(null)
      try {
        const params = new URLSearchParams()
        if (decade && decade !== 'all') params.set('decade', decade)
        params.set('status', status)
        if (q.trim()) params.set('q', q.trim())
        const res = await call<{ tracks: MusicTrackAdmin[] }>(`/music/tracks?${params.toString()}`)
        setTracks(res.tracks)
      } catch (reason) {
        setTracksError(errorText(reason))
      } finally {
        setTracksLoading(false)
      }
    },
    [],
  )

  useEffect(() => {
    const timer = window.setTimeout(() => {
      void loadTracks(selectedDecade, statusFilter, searchQuery)
    }, searchQuery ? 200 : 0)
    return () => window.clearTimeout(timer)
  }, [selectedDecade, statusFilter, searchQuery, loadTracks])

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
      await Promise.all([refresh(), loadTracks(selectedDecade, statusFilter, searchQuery)])
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      setRefreshing(false)
    }
  }

  const patchTrack = async (track: MusicTrackAdmin, patch: { active?: boolean; approved?: boolean }) => {
    setBusyTrackId(track.id)
    try {
      const res = await call<{ track: MusicTrackAdmin }>(`/music/tracks/${track.id}`, {
        method: 'PATCH',
        body: patch,
      })
      setTracks((cur) => cur.map((t) => (t.id === track.id ? res.track : t)))
      refresh().catch(() => {})
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      setBusyTrackId(null)
    }
  }

  const bulkAction = async (action: 'approve' | 'unapprove') => {
    if (selectedDecade === 'all') return
    setBulkBusy(true)
    try {
      const res = await call<{ ok: boolean; updated: number }>('/music/tracks/bulk', {
        method: 'POST',
        body: { action, decade: selectedDecade },
      })
      notify(
        action === 'approve'
          ? `Approved ${plural(res.updated, 'active track')} in the ${decadeLabel(selectedDecade)}.`
          : `Cleared approval on ${plural(res.updated, 'track')} in the ${decadeLabel(selectedDecade)}.`,
      )
      await Promise.all([refresh(), loadTracks(selectedDecade, statusFilter, searchQuery)])
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      setBulkBusy(false)
    }
  }

  const searchCatalog = async (event: React.FormEvent) => {
    event.preventDefault()
    if (catalogQuery.trim().length < 2) return
    setCatalogSearching(true)
    try {
      const res = await call<{ results: MusicSearchResult[] }>(`/music/search?q=${encodeURIComponent(catalogQuery.trim())}`)
      setCatalogResults(res.results)
      if (!res.results.length) notify('No clean previewable songs matched that search.')
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      setCatalogSearching(false)
    }
  }

  const addCatalogTrack = async (item: MusicSearchResult) => {
    const key = `${item.artist}|${item.title}`
    setAddingKey(key)
    const targetDecade = selectedDecade === 'all' ? item.suggestedDecade : selectedDecade
    try {
      await call<{ track: MusicTrackAdmin }>('/music/tracks', {
        method: 'POST',
        body: {
          decade: targetDecade,
          artist: item.artist,
          title: item.title,
          previewUrl: item.previewUrl,
          artworkUrl: item.artworkUrl,
          trackUrl: item.trackUrl,
          releaseYear: item.releaseYear,
          source: item.source,
          approved: true,
        },
      })
      notify(`Added "${item.title}" by ${item.artist} to the ${decadeLabel(targetDecade)} (approved).`)
      await Promise.all([refresh(), loadTracks(selectedDecade, statusFilter, searchQuery)])
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      setAddingKey(null)
    }
  }

  const addBlockedArtist = () => {
    const cleaned = artistDraft.trim()
    if (!cleaned) return
    if (!blockedArtists.some((a) => a.toLowerCase() === cleaned.toLowerCase())) {
      setBlockedArtists((cur) => [...cur, cleaned])
      setPolicyDirty(true)
    }
    setArtistDraft('')
  }

  const addBlockedTitle = () => {
    const cleaned = titleDraft.trim()
    if (!cleaned) return
    if (!blockedTitles.some((t) => t.toLowerCase() === cleaned.toLowerCase())) {
      setBlockedTitles((cur) => [...cur, cleaned])
      setPolicyDirty(true)
    }
    setTitleDraft('')
  }

  const savePolicy = async () => {
    setSavingPolicy(true)
    try {
      const res = await call<{ policy: MusicPolicy }>('/music/policy', {
        method: 'PUT',
        body: { blocked_artists: blockedArtists, blocked_titles: blockedTitles },
      })
      setBlockedArtists(res.policy.blocked_artists)
      setBlockedTitles(res.policy.blocked_titles)
      setPolicyDirty(false)
      notify('Music blocklist saved. Blocked artists and songs are excluded immediately.')
      await Promise.all([refresh(), loadTracks(selectedDecade, statusFilter, searchQuery)])
    } catch (reason) {
      notify(errorText(reason), 'bad')
    } finally {
      setSavingPolicy(false)
    }
  }

  const totalActive = data ? data.decades.reduce((sum, row) => sum + row.tracks, 0) : 0
  const totalApproved = data ? data.decades.reduce((sum, row) => sum + (row.approved ?? 0), 0) : 0

  return (
    <>
      <PageHeader
        title="Music & Soundtrack"
        description="Review and approve individual tracks, preview 30-second clips, add corporate-safe songs, and maintain your organization's blocklist."
        actions={
          <button type="button" className="btn btn-ghost" onClick={refreshNow} disabled={refreshing || !data}>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        }
      />

      <div className="mb-5">
        <Note tone="info">
          <ul className="list-disc space-y-1 pl-5">
            <li>Explicit tracks are automatically excluded from Apple/iTunes previews.</li>
            <li>
              Turn on <strong>Approved tracks only</strong> in <em>Settings → Game defaults</em> (or on an individual quiz’s <em>Play styles</em>{' '}
              tab) to guarantee that only songs you have approved here can ever play.
            </li>
            <li>
              For high-stakes events, you can also lock an individual quiz to a custom hand-picked playlist on its <em>Play styles</em> tab.
            </li>
          </ul>
        </Note>
      </div>

      {!data && loading && <LoadingBlock label="Loading the music lists" />}
      {!data && error && <ErrorBlock message={error} onRetry={reload} />}

      {data && (
        <div className="space-y-6">
          <Panel
            title="Decade catalogues"
            description={`${plural(totalActive, 'active track')} across all decades · ${plural(totalApproved, 'approved track')} vetted for corporate-safe mode.`}
          >
            <div className="ad-table-wrap">
              <table className="ad-table" style={{ minWidth: 540 }}>
                <thead>
                  <tr>
                    <th scope="col">Decade</th>
                    <th scope="col" className="num">Active</th>
                    <th scope="col" className="num">Approved</th>
                    <th scope="col" className="num">Disabled</th>
                    <th scope="col">Stock</th>
                    <th scope="col">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {data.decades.map((row) => {
                    const stock = stockOf(row.tracks)
                    const isFilling = row.decade in filling
                    const toppingUp = isFilling && stock.tone !== 'good'
                    const isSelected = selectedDecade === row.decade
                    return (
                      <tr key={row.decade} style={isSelected ? { background: 'var(--brand-soft)' } : undefined}>
                        <th scope="row">
                          <span className="font-semibold">{decadeLabel(row.decade)}</span>
                        </th>
                        <td className="num">{row.tracks}</td>
                        <td className="num">
                          <span className={(row.approved ?? 0) > 0 ? 'font-semibold text-good' : 'text-muted'}>{row.approved ?? 0}</span>
                        </td>
                        <td className="num text-muted">{row.disabled ?? 0}</td>
                        <td>
                          <Badge tone={toppingUp ? 'info' : stock.tone}>{toppingUp ? 'Filling…' : stock.label}</Badge>
                        </td>
                        <td className="num">
                          <div className="inline-flex items-center gap-2">
                            <button
                              type="button"
                              className={`btn btn-sm ${isSelected ? 'btn-primary' : 'btn-ghost'}`}
                              onClick={() => setSelectedDecade(row.decade)}
                            >
                              {isSelected ? 'Viewing' : 'Browse'}
                            </button>
                            <button
                              type="button"
                              className="btn btn-ghost btn-sm"
                              onClick={() => void fill(row.decade)}
                              disabled={asking !== null || isFilling}
                              aria-label={`Fill the ${decadeLabel(row.decade)}`}
                            >
                              {asking === row.decade ? 'Starting…' : 'Fill'}
                            </button>
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </Panel>

          <Panel
            title={selectedDecade === 'all' ? 'All tracks' : `${decadeLabel(selectedDecade)} track list`}
            description="Preview any 30-second clip, disable tracks you do not want played, or approve tracks for Approved-only mode."
            actions={
              <div className="flex flex-wrap items-center gap-2">
                <select
                  aria-label="Filter by decade"
                  className="field !w-auto !py-1.5 text-sm"
                  value={selectedDecade}
                  onChange={(e) => setSelectedDecade(e.target.value)}
                >
                  <option value="all">All decades</option>
                  {data.decades.map((d) => (
                    <option key={d.decade} value={d.decade}>
                      {decadeLabel(d.decade)} ({d.tracks})
                    </option>
                  ))}
                </select>
                {selectedDecade !== 'all' && (
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    disabled={bulkBusy}
                    onClick={() => void bulkAction('approve')}
                  >
                    Approve all active
                  </button>
                )}
              </div>
            }
          >
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <Segmented label="Filter tracks by status" options={FILTER_OPTIONS} value={statusFilter} onChange={setStatusFilter} />
              <input
                type="search"
                className="field max-w-xs"
                placeholder="Filter by artist or title…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                aria-label="Filter tracks by artist or title"
              />
            </div>

            {tracksError && <ErrorBlock message={tracksError} onRetry={() => void loadTracks(selectedDecade, statusFilter, searchQuery)} />}
            {tracksLoading && !tracks.length && <LoadingBlock label="Loading tracks" />}
            {!tracksLoading && !tracksError && !tracks.length && (
              <p className="py-6 text-center text-sm text-muted">No tracks match this filter yet. Try switching filters, clicking Fill, or adding a song below.</p>
            )}

            {tracks.length > 0 && (
              <div className="ad-table-wrap max-h-[460px] overflow-y-auto">
                <table className="ad-table" style={{ minWidth: 620 }}>
                  <thead>
                    <tr>
                      <th scope="col" style={{ width: 64 }}>Preview</th>
                      <th scope="col">Song</th>
                      <th scope="col">Status</th>
                      <th scope="col" className="num">Controls</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tracks.map((t) => {
                      const isPlaying = playingUrl === t.previewUrl
                      const busy = busyTrackId === t.id
                      return (
                        <tr key={t.id} style={!t.active || t.blocked ? { opacity: 0.62 } : undefined}>
                          <td>
                            <button
                              type="button"
                              className={`btn btn-sm ${isPlaying ? 'btn-primary' : 'btn-ghost'}`}
                              onClick={() => togglePreview(t.previewUrl)}
                              aria-label={`${isPlaying ? 'Stop' : 'Play'} preview of ${t.title} by ${t.artist}`}
                            >
                              {isPlaying ? '⏹' : '▶'}
                            </button>
                          </td>
                          <td>
                            <div className="flex items-center gap-2.5">
                              {t.artworkUrl && (
                                <img src={t.artworkUrl} alt="" className="h-9 w-9 shrink-0 rounded object-cover" loading="lazy" />
                              )}
                              <div className="min-w-0">
                                <div className="truncate font-semibold">{t.title}</div>
                                <div className="truncate text-xs text-muted">
                                  {t.artist}
                                  {t.releaseYear ? ` · ${t.releaseYear}` : ''}
                                  {selectedDecade === 'all' ? ` · ${decadeLabel(t.decade)}` : ''}
                                </div>
                              </div>
                            </div>
                          </td>
                          <td>
                            <div className="flex flex-wrap gap-1.5">
                              {t.blocked && <Badge tone="bad">Blocked</Badge>}
                              {!t.active && <Badge tone="neutral">Disabled</Badge>}
                              {t.active && t.approved && <Badge tone="good">Approved</Badge>}
                              {t.active && !t.approved && !t.blocked && <Badge tone="warn">Needs review</Badge>}
                            </div>
                          </td>
                          <td className="num">
                            <div className="inline-flex items-center gap-1.5">
                              {t.active && (
                                <button
                                  type="button"
                                  className={`btn btn-sm ${t.approved ? 'btn-ghost' : 'btn-primary'}`}
                                  disabled={busy || t.blocked}
                                  onClick={() => void patchTrack(t, { approved: !t.approved })}
                                >
                                  {t.approved ? 'Unapprove' : 'Approve'}
                                </button>
                              )}
                              <button
                                type="button"
                                className="btn btn-ghost btn-sm"
                                disabled={busy}
                                onClick={() => void patchTrack(t, { active: !t.active })}
                              >
                                {t.active ? 'Disable' : 'Re-enable'}
                              </button>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}

            <div className="mt-5 border-t border-line pt-4">
              <h3 className="text-sm font-semibold">Add a vetted song from Apple / iTunes</h3>
              <p className="mt-0.5 text-xs text-muted">
                Search for a clean song, listen to its 30-second preview, and add it directly as Approved
                {selectedDecade !== 'all' ? ` into the ${decadeLabel(selectedDecade)}` : ' into its release decade'}.
              </p>
              <form className="mt-2.5 flex flex-wrap gap-2" onSubmit={(e) => void searchCatalog(e)}>
                <input
                  type="search"
                  className="field max-w-md flex-1"
                  placeholder="Search artist or song title (e.g. Pharrell Happy, Daft Punk)…"
                  value={catalogQuery}
                  onChange={(e) => setCatalogQuery(e.target.value)}
                />
                <button type="submit" className="btn btn-primary btn-sm" disabled={catalogSearching || catalogQuery.trim().length < 2}>
                  {catalogSearching ? 'Searching…' : 'Search catalog'}
                </button>
              </form>
              {catalogResults.length > 0 && (
                <ul className="mt-3 divide-y divide-line rounded-lg border border-line">
                  {catalogResults.map((item) => {
                    const key = `${item.artist}|${item.title}`
                    const isPlaying = playingUrl === item.previewUrl
                    return (
                      <li key={key} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2">
                        <div className="flex min-w-0 items-center gap-2.5">
                          <button
                            type="button"
                            className={`btn btn-sm ${isPlaying ? 'btn-primary' : 'btn-ghost'}`}
                            onClick={() => togglePreview(item.previewUrl)}
                            aria-label={`${isPlaying ? 'Stop' : 'Play'} preview of ${item.title}`}
                          >
                            {isPlaying ? '⏹' : '▶'}
                          </button>
                          {item.artworkUrl && <img src={item.artworkUrl} alt="" className="h-8 w-8 rounded object-cover" />}
                          <div className="min-w-0">
                            <div className="truncate text-sm font-semibold">{item.title}</div>
                            <div className="truncate text-xs text-muted">
                              {item.artist}
                              {item.releaseYear ? ` · ${item.releaseYear}` : ''}
                            </div>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          {item.blocked ? (
                            <Badge tone="bad">On blocklist</Badge>
                          ) : (
                            <button
                              type="button"
                              className="btn btn-primary btn-sm"
                              disabled={addingKey === key}
                              onClick={() => void addCatalogTrack(item)}
                            >
                              {addingKey === key
                                ? 'Adding…'
                                : `Add to ${decadeLabel(selectedDecade === 'all' ? item.suggestedDecade : selectedDecade)}`}
                            </button>
                          )}
                        </div>
                      </li>
                    )
                  })}
                </ul>
              )}
            </div>
          </Panel>

          <Panel
            title="Organization blocklist"
            description="Blocked artists and song titles are immediately excluded from catalogue seeding, background soundtracks, Name That Tune rounds, after-party playlists, and player favorite-artist requests."
            actions={
              <button
                type="button"
                className="btn btn-primary btn-sm"
                disabled={!policyDirty || savingPolicy}
                onClick={() => void savePolicy()}
              >
                {savingPolicy ? 'Saving…' : 'Save blocklist'}
              </button>
            }
          >
            <div className="grid gap-6 md:grid-cols-2">
              <div>
                <label className="label" htmlFor="block-artist-input">Blocked artists</label>
                <div className="flex gap-2">
                  <input
                    id="block-artist-input"
                    type="text"
                    className="field flex-1"
                    placeholder="Add artist name…"
                    value={artistDraft}
                    onChange={(e) => setArtistDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        addBlockedArtist()
                      }
                    }}
                  />
                  <button type="button" className="btn btn-ghost btn-sm" onClick={addBlockedArtist} disabled={!artistDraft.trim()}>
                    Add
                  </button>
                </div>
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  {blockedArtists.length === 0 && <span className="text-xs text-muted">No blocked artists.</span>}
                  {blockedArtists.map((artist) => (
                    <span
                      key={artist}
                      className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-2 px-2.5 py-1 text-xs font-medium"
                    >
                      {artist}
                      <button
                        type="button"
                        className="text-muted hover:text-ink"
                        aria-label={`Remove ${artist} from blocked artists`}
                        onClick={() => {
                          setBlockedArtists((cur) => cur.filter((a) => a !== artist))
                          setPolicyDirty(true)
                        }}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              </div>

              <div>
                <label className="label" htmlFor="block-title-input">Blocked song titles</label>
                <div className="flex gap-2">
                  <input
                    id="block-title-input"
                    type="text"
                    className="field flex-1"
                    placeholder="Add song title…"
                    value={titleDraft}
                    onChange={(e) => setTitleDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        addBlockedTitle()
                      }
                    }}
                  />
                  <button type="button" className="btn btn-ghost btn-sm" onClick={addBlockedTitle} disabled={!titleDraft.trim()}>
                    Add
                  </button>
                </div>
                <div className="mt-2.5 flex flex-wrap gap-1.5">
                  {blockedTitles.length === 0 && <span className="text-xs text-muted">No blocked song titles.</span>}
                  {blockedTitles.map((title) => (
                    <span
                      key={title}
                      className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-2 px-2.5 py-1 text-xs font-medium"
                    >
                      {title}
                      <button
                        type="button"
                        className="text-muted hover:text-ink"
                        aria-label={`Remove ${title} from blocked song titles`}
                        onClick={() => {
                          setBlockedTitles((cur) => cur.filter((t) => t !== title))
                          setPolicyDirty(true)
                        }}
                      >
                        ×
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            </div>
          </Panel>
        </div>
      )}
    </>
  )
}
