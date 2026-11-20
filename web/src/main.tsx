import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { FullScreenMessage, Spinner } from './components/ui'
import HomePage from './game/HomePage'
import QuizPage from './game/QuizPage'
import HostScreen from './live/HostScreen'
import LivePlayer from './live/LivePlayer'
import { applyBranding } from './lib/theme'
import './styles.css'

// The admin screens are only downloaded by people who open /admin.
const AdminApp = lazy(() => import('./admin/AdminApp'))

applyBranding(null)

function NotFound() {
  return (
    <FullScreenMessage title="Nothing here" action={<a className="btn btn-primary" href="/">Go to the start</a>}>
      That link does not lead anywhere. Check the invitation you were sent.
    </FullScreenMessage>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Suspense fallback={<div className="stage grid min-h-dvh place-items-center"><Spinner /></div>}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/q/:slug" element={<QuizPage />} />
          <Route path="/live" element={<LivePlayer />} />
          <Route path="/live/:code" element={<LivePlayer />} />
          <Route path="/host/:code" element={<HostScreen />} />
          <Route path="/admin/*" element={<AdminApp />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  </StrictMode>,
)
