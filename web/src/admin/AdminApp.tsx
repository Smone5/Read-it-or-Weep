import { Component, useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Link, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { ErrorNote, Spinner } from '../components/ui'
import { ApiError, onAdminTokenChange, session } from '../lib/api'
import './admin.css'
import AdminsPage from './AdminsPage'
import { call, errorText, isAbort, loginAdmin, setUnauthorizedHandler } from './api'
import BrandingPage from './BrandingPage'
import { ConfirmDialog, Dialog, DialogActions, EmptyState, Field } from './components'
import {
  ChromeContext,
  DirtyContext,
  SessionContext,
  useChrome,
  useDirtyGuard,
  usePageTitle,
  useSession,
  type AdminSession,
  type Chrome,
  type DirtyGuard,
  type NotifyOptions,
} from './context'
import GameDefaultsPage from './GameDefaultsPage'
import { forgetMeta } from './hooks'
import HostPage from './HostPage'
import Login from './Login'
import MusicPage from './MusicPage'
import { paths } from './paths'
import QuizDetail from './QuizDetail'
import QuizList from './QuizList'
import type { AdminUser, BrandingSettings, SettingsRead } from './types'

type Toast = {
  id: number
  message: string
  tone: 'good' | 'bad'
  actionLabel?: string
  onAction?: () => void
}

/**
 * Everything under /admin. The root `.admin` element carries the admin's own fixed, light
 * palette (see admin.css), so the game's colours and the company's branding stop here.
 */
export default function AdminApp() {
  const [portal, setPortal] = useState<HTMLDivElement | null>(null)
  const [toast, setToast] = useState<Toast | null>(null)
  const [toastPaused, setToastPaused] = useState(false)
  const [productName, setProductName] = useState('Quiz')
  const [pageTitle, setPageTitle] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [pendingLeave, setPendingLeave] = useState<(() => void) | null>(null)
  const dirtyRef = useRef(false)
  dirtyRef.current = dirty

  const notify = useCallback((message: string, tone: 'good' | 'bad' = 'good', options?: NotifyOptions) => {
    setToast({ id: Date.now(), message, tone, actionLabel: options?.actionLabel, onAction: options?.onAction })
    setToastPaused(false)
  }, [])

  useEffect(() => {
    if (!toast || toastPaused) return
    const ms = toast.onAction ? 7000 : toast.tone === 'bad' ? 6500 : 3800
    const timer = window.setTimeout(() => setToast(null), ms)
    return () => window.clearTimeout(timer)
  }, [toast, toastPaused])

  const confirmLeave = useCallback((action: () => void) => {
    if (!dirtyRef.current) {
      action()
      return
    }
    setPendingLeave(() => action)
  }, [])

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

  const dirtyGuard = useMemo<DirtyGuard>(
    () => ({ dirty, setDirty, confirmLeave }),
    [dirty, confirmLeave],
  )

  return (
    <div className="admin">
      <ChromeContext.Provider value={chrome}>
        <DirtyContext.Provider value={dirtyGuard}>
          <Gate />
          <ConfirmDialog
            open={pendingLeave !== null}
            title="Leave without saving?"
            confirmLabel="Discard changes"
            danger
            onConfirm={() => {
              const next = pendingLeave
              setPendingLeave(null)
              setDirty(false)
              next?.()
            }}
            onCancel={() => setPendingLeave(null)}
          >
            <p>You have unsaved changes on this screen. Leaving now will discard them.</p>
          </ConfirmDialog>
        </DirtyContext.Provider>
      </ChromeContext.Provider>
      <div ref={setPortal} />
      <div role="status" aria-live="polite">
        {toast && toast.tone !== 'bad' && (
          <ToastBanner
            toast={toast}
            onPause={setToastPaused}
            onDismiss={() => setToast(null)}
          />
        )}
      </div>
      <div role="alert" aria-live="assertive">
        {toast && toast.tone === 'bad' && (
          <ToastBanner
            toast={toast}
            onPause={setToastPaused}
            onDismiss={() => setToast(null)}
          />
        )}
      </div>
    </div>
  )
}

function ToastBanner({
  toast,
  onPause,
  onDismiss,
}: {
  toast: Toast
  onPause: (paused: boolean) => void
  onDismiss: () => void
}) {
  return (
    <div
      key={toast.id}
      className="ad-toast rise"
      data-tone={toast.tone}
      onMouseEnter={() => onPause(true)}
      onMouseLeave={() => onPause(false)}
      onFocus={() => onPause(true)}
      onBlur={() => onPause(false)}
    >
      <span className="min-w-0 flex-1">{toast.message}</span>
      {toast.actionLabel && toast.onAction && (
        <button
          type="button"
          className="ad-toast-btn"
          onClick={() => {
            const fn = toast.onAction
            onDismiss()
            fn?.()
          }}
        >
          {toast.actionLabel}
        </button>
      )}
      <button type="button" className="ad-toast-btn" onClick={onDismiss} aria-label="Dismiss notification">
        <span aria-hidden>✕</span>
      </button>
    </div>
  )
}

type Phase = 'checking' | 'signedOut' | 'signedIn'

/** Shows the sign-in screen until there is a working session; on 401 while signed in, overlays a re-auth dialog so edits survive. */
function Gate() {
  const navigate = useNavigate()
  const { setDirty, confirmLeave } = useDirtyGuard()
  const [phase, setPhase] = useState<Phase>(() => (session.adminToken ? 'checking' : 'signedOut'))
  const [admin, setAdmin] = useState<AdminUser | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [sessionExpired, setSessionExpired] = useState(false)
  const [checkError, setCheckError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)
  const phaseRef = useRef(phase)
  phaseRef.current = phase

  // Any admin call refused with 401 lands here. If already signed in, keep Shell mounted so unsaved edits survive!
  useEffect(() => {
    setUnauthorizedHandler(() => {
      forgetMeta()
      if (phaseRef.current === 'signedIn') {
        setSessionExpired(true)
      } else {
        setAdmin(null)
        setPhase('signedOut')
        setNotice('Your session has ended. Please sign in again.')
      }
    })
    return () => setUnauthorizedHandler(null)
  }, [])

  // Sync sign-out / re-login across open browser tabs via localStorage events.
  useEffect(() => {
    return onAdminTokenChange((token) => {
      if (!token) {
        forgetMeta()
        if (phaseRef.current === 'signedIn') {
          setSessionExpired(true)
        } else {
          setAdmin(null)
          setPhase('signedOut')
          setNotice('You were signed out in another tab.')
        }
      } else if (sessionExpired) {
        setSessionExpired(false)
      }
    })
  }, [sessionExpired])

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
        if (error instanceof ApiError && error.status === 401) return
        setCheckError(errorText(error))
      },
    )
    return () => controller.abort()
  }, [phase, attempt])

  const doSignOut = useCallback(() => {
    session.setAdminToken(null)
    forgetMeta()
    setDirty(false)
    setSessionExpired(false)
    setAdmin(null)
    setNotice(null)
    setPhase('signedOut')
    navigate(paths.home)
  }, [navigate, setDirty])

  const signOut = useCallback(() => {
    confirmLeave(doSignOut)
  }, [confirmLeave, doSignOut])

  const value = useMemo<AdminSession | null>(() => (admin ? { admin, signOut } : null), [admin, signOut])

  if (phase === 'signedOut') {
    return (
      <Login
        notice={notice}
        onSignedIn={(who) => {
          setAdmin(who)
          setNotice(null)
          setSessionExpired(false)
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
              <button type="button" className="btn btn-ghost" onClick={doSignOut}>
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
      {sessionExpired && (
        <ReauthDialog
          defaultEmail={admin?.email ?? ''}
          onSignedIn={(who) => {
            setAdmin(who)
            setSessionExpired(false)
          }}
          onSignOut={doSignOut}
        />
      )}
    </SessionContext.Provider>
  )
}

function ReauthDialog({
  defaultEmail,
  onSignedIn,
  onSignOut,
}: {
  defaultEmail: string
  onSignedIn: (admin: AdminUser) => void
  onSignOut: () => void
}) {
  const [email, setEmail] = useState(defaultEmail)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const result = await loginAdmin<{ token: string; admin: AdminUser }>(email.trim(), password)
      session.setAdminToken(result.token)
      onSignedIn(result.admin)
    } catch (reason) {
      setError(errorText(reason))
      setBusy(false)
    }
  }

  return (
    <Dialog open onClose={() => {}} title="Session expired — sign in to continue">
      <form className="grid gap-4" onSubmit={submit}>
        <p className="text-sm text-muted">
          Your sign-in timed out, but any unsaved edits behind this dialog are still intact. Sign in again to keep working.
        </p>
        <Field label="Email">
          {(props) => (
            <input
              className="field"
              type="email"
              autoComplete="username"
              required
              {...props}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          )}
        </Field>
        <Field label="Password">
          {(props) => (
            <input
              className="field"
              type="password"
              autoComplete="current-password"
              required
              data-autofocus
              {...props}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          )}
        </Field>
        <ErrorNote>{error}</ErrorNote>
        <DialogActions>
          <button type="button" className="btn btn-ghost" onClick={onSignOut} disabled={busy}>
            Sign out
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? 'Signing in…' : 'Sign in'}
          </button>
        </DialogActions>
      </form>
    </Dialog>
  )
}

class AdminErrorBoundary extends Component<
  { children: ReactNode; resetKey: string },
  { hasError: boolean; message: string }
> {
  state = { hasError: false, message: '' }

  static getDerivedStateFromError(error: Error) {
    return { hasError: true, message: error?.message || 'An unexpected display error occurred.' }
  }

  componentDidUpdate(prevProps: { resetKey: string }) {
    if (prevProps.resetKey !== this.props.resetKey && this.state.hasError) {
      this.setState({ hasError: false, message: '' })
    }
  }

  render() {
    if (this.state.hasError) {
      return (
        <EmptyState
          title="Something broke on this screen"
          action={
            <>
              <button type="button" className="btn btn-primary" onClick={() => this.setState({ hasError: false, message: '' })}>
                Try rendering again
              </button>
              <Link className="btn btn-ghost" to={paths.home}>
                Back to quizzes
              </Link>
            </>
          }
        >
          {this.state.message}
        </EmptyState>
      )
    }
    return this.props.children
  }
}

const under = (base: string) => (pathname: string) => pathname === base || pathname.startsWith(`${base}/`)

const NAV: { to: string; label: string; isCurrent: (pathname: string) => boolean }[] = [
  {
    to: paths.home,
    label: 'Quizzes',
    isCurrent: (pathname) => pathname.replace(/\/+$/, '') === paths.home || under(`${paths.home}/quizzes`)(pathname),
  },
  { to: paths.defaults, label: 'Defaults', isCurrent: under(paths.defaults) },
  { to: paths.branding, label: 'Branding', isCurrent: under(paths.branding) },
  { to: paths.host, label: 'Host', isCurrent: under(paths.host) },
  { to: paths.music, label: 'Music', isCurrent: under(paths.music) },
  { to: paths.admins, label: 'Admins', isCurrent: under(paths.admins) },
]

/** The top bar and the screens beneath it. */
function Shell() {
  const { admin, signOut } = useSession()
  const { productName, setProductName } = useChrome()
  const { confirmLeave } = useDirtyGuard()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)
  const headerRef = useRef<HTMLElement>(null)
  const menuBtnRef = useRef<HTMLButtonElement>(null)

  useEffect(() => setMenuOpen(false), [pathname])

  // Close mobile menu on Escape or outside click and return focus to Menu button.
  useEffect(() => {
    if (!menuOpen) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false)
        menuBtnRef.current?.focus()
      }
    }
    const onPointer = (event: PointerEvent) => {
      if (!headerRef.current?.contains(event.target as Node)) setMenuOpen(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onPointer)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('pointerdown', onPointer)
    }
  }, [menuOpen])

  // The game's title (set on the Branding page) doubles as the product name in the top bar.
  useEffect(() => {
    const controller = new AbortController()
    call<SettingsRead<BrandingSettings>>('/settings/branding', { signal: controller.signal }).then(
      (result) => setProductName(result.value.game_title),
      () => {},
    )
    return () => controller.abort()
  }, [setProductName])

  const guardedClick = (to: string) => (event: React.MouseEvent<HTMLAnchorElement>) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return
    if (to === pathname) return
    event.preventDefault()
    confirmLeave(() => navigate(to))
  }

  const links = NAV.map((item) => (
    <Link
      key={item.to}
      to={item.to}
      onClick={guardedClick(item.to)}
      className="ad-nav-link"
      aria-current={item.isCurrent(pathname) ? 'page' : undefined}
    >
      {item.label}
    </Link>
  ))

  const displayName = admin.name || admin.email

  return (
    <>
      <a href="#admin-main" className="ad-skip">
        Skip to the content
      </a>
      <header ref={headerRef} className="ad-topbar">
        <div className="mx-auto flex w-full max-w-[1180px] items-center gap-3 px-4 py-2 sm:px-6">
          <Link to={paths.home} onClick={guardedClick(paths.home)} className="flex min-h-10 min-w-0 items-center gap-2 font-bold">
            <span className="truncate">{productName}</span>
            <span className="chip" data-tone="brand">
              Admin
            </span>
          </Link>
          <nav aria-label="Admin sections" className="ml-2 hidden items-center gap-1 md:flex">
            {links}
          </nav>
          <div className="ml-auto hidden min-w-0 items-center gap-3 md:flex">
            <a href="/" target="_blank" rel="noopener" className="ad-link hidden text-sm xl:inline">
              Open game ↗
            </a>
            <span className="hidden max-w-44 truncate text-sm text-muted lg:inline" title={admin.email}>
              {displayName}
            </span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={signOut}>
              Sign out
            </button>
          </div>
          <div className="ml-auto md:hidden">
            <button
              ref={menuBtnRef}
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
          <div id="admin-menu" className="max-h-[calc(100dvh-4rem)] overflow-y-auto border-t border-line px-4 pb-3 pt-2 md:hidden">
            <nav aria-label="Admin sections" className="grid gap-1">
              {links}
              <a href="/" target="_blank" rel="noopener" className="ad-nav-link">
                Open game ↗
              </a>
            </nav>
            <div className="mt-2 flex items-center justify-between gap-3 border-t border-line pt-3">
              <span className="min-w-0 truncate text-sm text-muted" title={admin.email}>
                {displayName}
              </span>
              <button type="button" className="btn btn-ghost btn-sm" onClick={signOut}>
                Sign out
              </button>
            </div>
          </div>
        )}
      </header>
      <main id="admin-main" tabIndex={-1} className="mx-auto w-full max-w-[1180px] px-4 pb-24 pt-6 outline-none sm:px-6">
        <AdminErrorBoundary resetKey={pathname}>
          <Routes>
            <Route index element={<QuizList />} />
            <Route path="quizzes/:quizId/*" element={<QuizDetail />} />
            <Route path="defaults" element={<GameDefaultsPage />} />
            <Route path="branding" element={<BrandingPage />} />
            <Route path="host" element={<HostPage />} />
            <Route path="music" element={<MusicPage />} />
            <Route path="admins" element={<AdminsPage />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </AdminErrorBoundary>
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
