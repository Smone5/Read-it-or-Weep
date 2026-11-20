import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { ErrorNote, Spinner } from '../components/ui'
import { ApiError, session } from '../lib/api'
import './admin.css'
import AdminsPage from './AdminsPage'
import { call, errorText, isAbort, setUnauthorizedHandler } from './api'
import BrandingPage from './BrandingPage'
import { EmptyState } from './components'
import { ChromeContext, SessionContext, useChrome, usePageTitle, useSession, type AdminSession, type Chrome } from './context'
import { forgetMeta } from './hooks'
import HostPage from './HostPage'
import Login from './Login'
import MusicPage from './MusicPage'
import { paths } from './paths'
import QuizDetail from './QuizDetail'
import QuizList from './QuizList'
import type { AdminUser, BrandingSettings, SettingsRead } from './types'

type Toast = { id: number; message: string; tone: 'good' | 'bad' }

/**
 * Everything under /admin. The root `.admin` element carries the admin's own fixed, light
 * palette (see admin.css), so the game's colours and the company's branding stop here.
 */
export default function AdminApp() {
  const [portal, setPortal] = useState<HTMLDivElement | null>(null)
  const [toast, setToast] = useState<Toast | null>(null)
  const [productName, setProductName] = useState('Quiz')
  const [pageTitle, setPageTitle] = useState<string | null>(null)
  const toastTimer = useRef<number | undefined>(undefined)

  const notify = useCallback((message: string, tone: 'good' | 'bad' = 'good') => {
    window.clearTimeout(toastTimer.current)
    setToast({ id: Date.now(), message, tone })
    toastTimer.current = window.setTimeout(() => setToast(null), tone === 'bad' ? 6000 : 3500)
  }, [])

  useEffect(() => () => window.clearTimeout(toastTimer.current), [])

  // The game puts its own name on the browser tab and tints the phone's address bar dark.
  // The admin names the open screen instead and uses its own light tint, then hands both
  // back when the person leaves.
  useEffect(() => {
    const previousTitle = document.title
    const tint = document.querySelector('meta[name="theme-color"]')
    const previousTint = tint?.getAttribute('content') ?? null
    tint?.setAttribute('content', '#f5f6fa')
    return () => {
      document.title = previousTitle
      if (previousTint !== null) tint?.setAttribute('content', previousTint)
    }
  }, [])

  useEffect(() => {
    document.title = pageTitle ? `${pageTitle} · ${productName} admin` : `${productName} admin`
  }, [pageTitle, productName])

  const chrome = useMemo<Chrome>(
    () => ({ productName, setProductName, setPageTitle, notify, portal }),
    [productName, notify, portal],
  )

  return (
    <div className="admin">
      <ChromeContext.Provider value={chrome}>
        <Gate />
      </ChromeContext.Provider>
      <div ref={setPortal} />
      <div role="status" aria-live="polite">
        {toast && (
          <div key={toast.id} className="ad-toast rise" data-tone={toast.tone}>
            {toast.message}
          </div>
        )}
      </div>
    </div>
  )
}

type Phase = 'checking' | 'signedOut' | 'signedIn'

/** Shows the sign-in screen until there is a working session, and again the moment one expires. */
function Gate() {
  const navigate = useNavigate()
  const [phase, setPhase] = useState<Phase>(() => (session.adminToken ? 'checking' : 'signedOut'))
  const [admin, setAdmin] = useState<AdminUser | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [checkError, setCheckError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  // Any admin call refused with 401 (the shared client has already dropped the token) lands here.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      forgetMeta()
      setAdmin(null)
      setPhase('signedOut')
      setNotice('Your session has ended. Please sign in again.')
    })
    return () => setUnauthorizedHandler(null)
  }, [])

  // A token saved from an earlier visit may have expired: check it before showing anything.
  useEffect(() => {
    if (phase !== 'checking') return
    const controller = new AbortController()
    setCheckError(null)
    call<{ admin: AdminUser }>('/me', { signal: controller.signal }).then(
      (result) => {
        setAdmin(result.admin)
        setPhase('signedIn')
      },
      (error: unknown) => {
        if (isAbort(error)) return
        if (error instanceof ApiError && error.status === 401) return // already back at sign-in
        setCheckError(errorText(error))
      },
    )
    return () => controller.abort()
  }, [phase, attempt])

  const signOut = useCallback(() => {
    session.setAdminToken(null)
    forgetMeta()
    setAdmin(null)
    setNotice(null)
    setPhase('signedOut')
    navigate(paths.home)
  }, [navigate])

  const value = useMemo<AdminSession | null>(() => (admin ? { admin, signOut } : null), [admin, signOut])

  if (phase === 'signedOut') {
    return (
      <Login
        notice={notice}
        onSignedIn={(who) => {
          setAdmin(who)
          setNotice(null)
          setPhase('signedIn')
        }}
      />
    )
  }

  if (!value) {
    return (
      <main className="grid min-h-dvh place-items-center px-4">
        {checkError ? (
          <div className="card grid w-full max-w-sm gap-4 p-5">
            <ErrorNote>{checkError}</ErrorNote>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn btn-primary" onClick={() => setAttempt((n) => n + 1)}>
                Try again
              </button>
              <button type="button" className="btn btn-ghost" onClick={signOut}>
                Sign out
              </button>
            </div>
          </div>
        ) : (
          <Spinner label="Checking your sign-in" />
        )}
      </main>
    )
  }

  return (
    <SessionContext.Provider value={value}>
      <Shell />
    </SessionContext.Provider>
  )
}

const under = (base: string) => (pathname: string) => pathname === base || pathname.startsWith(`${base}/`)

const NAV: { to: string; label: string; isCurrent: (pathname: string) => boolean }[] = [
  {
    to: paths.home,
    label: 'Quizzes',
    isCurrent: (pathname) => pathname.replace(/\/+$/, '') === paths.home || under(`${paths.home}/quizzes`)(pathname),
  },
  { to: paths.branding, label: 'Branding', isCurrent: under(paths.branding) },
  { to: paths.host, label: 'Host', isCurrent: under(paths.host) },
  { to: paths.music, label: 'Music', isCurrent: under(paths.music) },
  { to: paths.admins, label: 'Admins', isCurrent: under(paths.admins) },
]

/** The top bar and the screens beneath it. */
function Shell() {
  const { admin, signOut } = useSession()
  const { productName, setProductName } = useChrome()
  const { pathname } = useLocation()
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => setMenuOpen(false), [pathname])

  // The game's title (set on the Branding page) doubles as the product name in the top bar.
  // If it cannot be fetched the bar simply keeps its plain name, so no error is shown.
  useEffect(() => {
    const controller = new AbortController()
    call<SettingsRead<BrandingSettings>>('/settings/branding', { signal: controller.signal }).then(
      (result) => setProductName(result.value.game_title),
      () => {},
    )
    return () => controller.abort()
  }, [setProductName])

  const links = NAV.map((item) => (
    <Link key={item.to} to={item.to} className="ad-nav-link" aria-current={item.isCurrent(pathname) ? 'page' : undefined}>
      {item.label}
    </Link>
  ))

  return (
    <>
      <a href="#admin-main" className="ad-skip">
        Skip to the content
      </a>
      <header className="ad-topbar">
        <div className="mx-auto flex w-full max-w-[1180px] items-center gap-3 px-4 py-2 sm:px-6">
          <Link to={paths.home} className="flex min-h-10 min-w-0 items-center gap-2 font-bold">
            <span className="truncate">{productName}</span>
            <span className="chip" data-tone="brand">
              Admin
            </span>
          </Link>
          <nav aria-label="Admin sections" className="ml-3 hidden items-center gap-1 md:flex">
            {links}
          </nav>
          <div className="ml-auto hidden min-w-0 items-center gap-3 md:flex">
            <span className="max-w-56 truncate text-sm text-muted" title={admin.email}>
              {admin.name || admin.email}
            </span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={signOut}>
              Sign out
            </button>
          </div>
          <div className="ml-auto md:hidden">
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              aria-expanded={menuOpen}
              aria-controls="admin-menu"
              onClick={() => setMenuOpen((open) => !open)}
            >
              {menuOpen ? 'Close menu' : 'Menu'}
            </button>
          </div>
        </div>
        {menuOpen && (
          <div id="admin-menu" className="border-t border-line px-4 pb-3 pt-2 md:hidden">
            <nav aria-label="Admin sections" className="grid gap-1">
              {links}
            </nav>
            <div className="mt-2 flex items-center justify-between gap-3 border-t border-line pt-3">
              <span className="min-w-0 truncate text-sm text-muted">{admin.email}</span>
              <button type="button" className="btn btn-ghost btn-sm" onClick={signOut}>
                Sign out
              </button>
            </div>
          </div>
        )}
      </header>
      <main id="admin-main" className="mx-auto w-full max-w-[1180px] px-4 pb-24 pt-6 sm:px-6">
        <Routes>
          <Route index element={<QuizList />} />
          <Route path="quizzes/:quizId/*" element={<QuizDetail />} />
          <Route path="branding" element={<BrandingPage />} />
          <Route path="host" element={<HostPage />} />
          <Route path="music" element={<MusicPage />} />
          <Route path="admins" element={<AdminsPage />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </>
  )
}

function NotFound() {
  usePageTitle('Page not found')
  return (
    <EmptyState
      title="There is nothing at this address"
      action={
        <Link className="btn btn-primary" to={paths.home}>
          Go to your quizzes
        </Link>
      }
    >
      The link may be old, or the quiz may have been deleted.
    </EmptyState>
  )
}
