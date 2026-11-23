import { createContext, useContext, useEffect } from 'react'
import type { AdminUser } from './types'

export type NotifyOptions = { actionLabel?: string; onAction?: () => void }

/** Things every admin screen can reach: the toast, the tab title, the place dialogs are drawn. */
export type Chrome = {
  /** The game's title as set on the Branding page; shown in the top bar. */
  productName: string
  setProductName: (name: string) => void
  setPageTitle: (title: string | null) => void
  /** Show a short confirmation at the bottom of the screen. */
  notify: (message: string, tone?: 'good' | 'bad', options?: NotifyOptions) => void
  /** Dialogs are drawn here (inside `.admin`, outside any card) so nothing can clip them. */
  portal: HTMLElement | null
}

export const ChromeContext = createContext<Chrome>({
  productName: 'Quiz',
  setProductName: () => {},
  setPageTitle: () => {},
  notify: () => {},
  portal: null,
})

export const useChrome = () => useContext(ChromeContext)

export const useNotify = () => useContext(ChromeContext).notify

/** Name the browser tab after the screen that is open. */
export function usePageTitle(title: string | null) {
  const { setPageTitle } = useChrome()
  useEffect(() => {
    setPageTitle(title)
    return () => setPageTitle(null)
  }, [title, setPageTitle])
}

export type DirtyGuard = {
  dirty: boolean
  setDirty: (dirty: boolean) => void
  confirmLeave: (action: () => void) => void
}

export const DirtyContext = createContext<DirtyGuard>({
  dirty: false,
  setDirty: () => {},
  confirmLeave: (action) => action(),
})

export const useDirtyGuard = () => useContext(DirtyContext)

/** Warn before closing the tab or navigating away while a form has unsaved edits. */
export function useUnsavedGuard(dirty: boolean) {
  const { setDirty } = useContext(DirtyContext)
  useEffect(() => {
    setDirty(dirty)
    return () => setDirty(false)
  }, [dirty, setDirty])

  useEffect(() => {
    if (!dirty) return
    const onBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [dirty])
}

export type AdminSession = { admin: AdminUser; signOut: () => void }

export const SessionContext = createContext<AdminSession | null>(null)

export function useSession(): AdminSession {
  const value = useContext(SessionContext)
  if (!value) throw new Error('useSession must be used inside the signed-in admin shell')
  return value
}
