// A picture you can pinch, drag and double-tap, like a photo on a phone, or wheel-zoom and pan on desktop.
//
// Charts from a report are drawn for a page, not for a pocket: at phone width their labels are a
// few pixels tall. So wherever the game shows a picture it can be enlarged in place with two
// fingers, and opened full screen – where a wide picture is turned on its side to use the long
// edge of the phone, which roughly doubles its size before any zooming at all.

import { useCallback, useEffect, useImperativeHandle, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode, type Ref } from 'react'
import { IconClose, IconRotate, IconZoomIn, IconZoomOut } from './Icons'

type Size = { w: number; h: number }

export type ZoomHandle = {
  zoomBy: (factor: number) => void
  reset: () => void
}

type ZoomImageProps = {
  src: string
  alt: string
  /** Show the picture turned a quarter turn (a wide picture on an upright phone). */
  quarterTurn?: boolean
  /** A second tap zooms in on that spot (and a third zooms back out). */
  doubleTapZoom?: boolean
  /**
   * Start zoomed in far enough to fill the frame's height, showing the left-hand end: a wide
   * picture in a narrow frame is then readable at once, and a sideways swipe shows the rest.
   */
  cover?: boolean
  /** A plain tap with no movement, when double-tap zoom is off. */
  onTap?: () => void
  /** The picture's own size, once known. */
  onSize?: (size: Size) => void
  onZoomChange?: (zoom: number) => void
  zoomRef?: Ref<ZoomHandle>
  maxZoom?: number
  className?: string
  style?: React.CSSProperties
  children?: ReactNode
}

const TAP_SLOP = 8 // pixels a finger may wander and still count as a tap
const DOUBLE_TAP_MS = 320
const DOUBLE_TAP_ZOOM = 2.6

const clamp = (value: number, low: number, high: number) => Math.min(high, Math.max(low, value))

const MOST_COVER = 2.8 // never start more zoomed-in than this, however wide the picture

export function ZoomImage({ src, alt, quarterTurn = false, doubleTapZoom = false, cover = false, onTap, onSize, onZoomChange, zoomRef, maxZoom = 6, className = '', style, children }: ZoomImageProps) {
  const frame = useRef<HTMLDivElement>(null)
  const image = useRef<HTMLImageElement>(null)
  const [natural, setNatural] = useState<Size | null>(null)
  const [box, setBox] = useState<Size>({ w: 0, h: 0 })
  const [zoomed, setZoomed] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [tall, setTall] = useState(false) // zoomed in so far that it also overflows up and down
  const [touched, setTouched] = useState(false)
  // Zoom is relative to "fits the frame"; x and y are how far the picture's centre sits from the frame's, in screen pixels.
  const view = useRef({ zoom: 1, x: 0, y: 0 })
  const fingers = useRef(new Map<number, { x: number; y: number }>())
  const pinch = useRef<{ spread: number; zoom: number; x: number; y: number; cx: number; cy: number } | null>(null)
  const press = useRef<{ x: number; y: number; moved: boolean; many: boolean } | null>(null)
  const lastTap = useRef({ at: 0, x: 0, y: 0 })

  const shownWide = natural ? (quarterTurn ? natural.h : natural.w) : 1
  const shownTall = natural ? (quarterTurn ? natural.w : natural.h) : 1
  const fit = natural && box.w && box.h ? Math.min(box.w / shownWide, box.h / shownTall) : 0

  /** Keep the picture inside its frame and put it on screen. */
  const paint = useCallback(() => {
    const picture = image.current
    if (!picture || !fit) return
    const v = view.current
    v.zoom = clamp(v.zoom, 1, maxZoom)
    const spareX = Math.max(0, (shownWide * fit * v.zoom - box.w) / 2)
    const spareY = Math.max(0, (shownTall * fit * v.zoom - box.h) / 2)
    v.x = clamp(v.x, -spareX, spareX)
    v.y = clamp(v.y, -spareY, spareY)
    picture.style.transform = `translate(-50%, -50%) translate(${v.x}px, ${v.y}px) rotate(${quarterTurn ? 90 : 0}deg) scale(${fit * v.zoom})`
    setZoomed(v.zoom > 1.02)
    setTall(spareY > 1)
    onZoomChange?.(v.zoom)
  }, [fit, box.w, box.h, shownWide, shownTall, quarterTurn, maxZoom, onZoomChange])

  /** Where a picture starts: the whole of it, or (cover) filling the height from its left-hand end. */
  const home = useCallback(() => {
    if (!cover || !fit) return { zoom: 1, x: 0, y: 0 }
    const zoom = clamp(box.h / (shownTall * fit), 1, MOST_COVER)
    return { zoom, x: Math.max(0, (shownWide * fit * zoom - box.w) / 2), y: 0 }
  }, [cover, fit, box.w, box.h, shownWide, shownTall])

  /** Change the zoom while keeping the point under the fingers (cx, cy from the frame's centre) where it is. */
  const zoomAbout = useCallback((zoom: number, cx: number, cy: number, from = view.current) => {
    const next = clamp(zoom, 1, maxZoom)
    const ratio = next / from.zoom
    view.current = { zoom: next, x: cx - (cx - from.x) * ratio, y: cy - (cy - from.y) * ratio }
  }, [maxZoom])

  useImperativeHandle(
    zoomRef,
    () => ({
      zoomBy: (factor: number) => {
        zoomAbout(view.current.zoom * factor, 0, 0)
        paint()
      },
      reset: () => {
        view.current = home()
        paint()
      },
    }),
    [zoomAbout, paint, home],
  )

  // Follow the frame's size (the phone turning, the layout settling).
  useEffect(() => {
    const element = frame.current
    if (!element) return
    const measure = () => setBox({ w: element.clientWidth, h: element.clientHeight })
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  // A new picture, a turn, or a new frame size starts from the home position.
  useEffect(() => {
    view.current = home()
    paint()
  }, [src, quarterTurn, home, paint])

  // Mouse wheel / trackpad pinch on a computer. (Needs a real listener: it must be able to stop the page scrolling.)
  useEffect(() => {
    const element = frame.current
    if (!element) return
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey && !doubleTapZoom && view.current.zoom <= 1.02) return // let the page scroll past an un-zoomed picture
      event.preventDefault()
      const bounds = element.getBoundingClientRect()
      zoomAbout(view.current.zoom * Math.exp(-event.deltaY * (event.ctrlKey ? 0.012 : 0.0022)),
        event.clientX - bounds.left - bounds.width / 2, event.clientY - bounds.top - bounds.height / 2)
      paint()
    }
    element.addEventListener('wheel', onWheel, { passive: false })
    return () => element.removeEventListener('wheel', onWheel)
  }, [paint, zoomAbout, doubleTapZoom])

  const centreOf = (event: ReactPointerEvent) => {
    const bounds = frame.current!.getBoundingClientRect()
    return { x: event.clientX - bounds.left - bounds.width / 2, y: event.clientY - bounds.top - bounds.height / 2 }
  }

  const startPinch = () => {
    const [a, b] = [...fingers.current.values()]
    pinch.current = { spread: Math.hypot(a.x - b.x, a.y - b.y) || 1, zoom: view.current.zoom, x: view.current.x, y: view.current.y, cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2 }
  }

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType === 'mouse' && event.button !== 0) return
    setTouched(true)
    if (view.current.zoom > 1.02) setDragging(true)
    try {
      event.currentTarget.setPointerCapture(event.pointerId) // keep following a finger that slides off the picture
    } catch {
      /* not a pointer that can be captured: the gesture still works inside the frame */
    }
    const point = centreOf(event)
    fingers.current.set(event.pointerId, point)
    if (fingers.current.size === 1) press.current = { ...point, moved: false, many: false }
    else {
      if (press.current) press.current.many = true
      startPinch()
    }
  }

  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const before = fingers.current.get(event.pointerId)
    if (!before) return
    const point = centreOf(event)
    fingers.current.set(event.pointerId, point)
    if (press.current && Math.hypot(point.x - press.current.x, point.y - press.current.y) > TAP_SLOP) press.current.moved = true
    if (fingers.current.size >= 2 && pinch.current) {
      // Two fingers: their spread sets the zoom, and the picture follows their midpoint.
      const [a, b] = [...fingers.current.values()]
      const start = pinch.current
      const middle = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
      zoomAbout(start.zoom * (Math.hypot(a.x - b.x, a.y - b.y) / start.spread), start.cx, start.cy, start)
      view.current.x += middle.x - start.cx
      view.current.y += middle.y - start.cy
      paint()
    } else if (view.current.zoom > 1.02) {
      view.current.x += point.x - before.x
      view.current.y += point.y - before.y
      paint()
    }
  }

  const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!fingers.current.delete(event.pointerId)) return
    if (fingers.current.size >= 2) startPinch()
    else pinch.current = null
    if (fingers.current.size > 0) return
    setDragging(false)
    const tap = press.current
    press.current = null
    if (view.current.zoom < 1.04) {
      view.current = { zoom: 1, x: 0, y: 0 } // nearly back to normal: settle there
      paint()
    }
    if (!tap || tap.moved || tap.many || event.type === 'pointercancel') return
    if (!doubleTapZoom) {
      onTap?.()
      return
    }
    const now = performance.now()
    const again = now - lastTap.current.at < DOUBLE_TAP_MS && Math.hypot(tap.x - lastTap.current.x, tap.y - lastTap.current.y) < 40
    lastTap.current = again ? { at: 0, x: 0, y: 0 } : { at: now, x: tap.x, y: tap.y }
    if (!again) return
    if (view.current.zoom > 1.2) view.current = { zoom: 1, x: 0, y: 0 }
    else zoomAbout(DOUBLE_TAP_ZOOM, tap.x, tap.y)
    paint()
  }

  return (
    <div
      ref={frame}
      className={`relative overflow-hidden select-none ${className}`}
      // An up-and-down drag scrolls the page for as long as the picture has nothing more to show that way;
      // sideways drags (and every drag, once it is zoomed in further) move the picture.
      style={{
        touchAction: doubleTapZoom || tall ? 'none' : 'pan-y',
        cursor: dragging ? 'grabbing' : zoomed ? 'grab' : onTap ? 'zoom-in' : 'default',
        ...style,
      }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
    >
      <img
        ref={image}
        src={src}
        alt={alt}
        draggable={false}
        onLoad={(event) => {
          const size = { w: event.currentTarget.naturalWidth, h: event.currentTarget.naturalHeight }
          setNatural(size)
          onSize?.(size)
        }}
        className="pointer-events-none absolute left-1/2 top-1/2 max-w-none will-change-transform"
        style={{ width: natural?.w, height: natural?.h, visibility: fit ? 'visible' : 'hidden', transformOrigin: 'center' }}
      />
      {cover && zoomed && !touched && (
        <span className="pixel pointer-events-none absolute bottom-2 left-2 rounded-sm bg-black/80 px-2.5 py-1.5 text-[10px] text-white" aria-hidden>
          ← swipe →
        </span>
      )}
      {children}
    </div>
  )
}

type ViewerProps = {
  src: string
  alt: string
  /** Shown while a question's clock is running, so the time is never out of sight. */
  secondsLeft?: number | null
  /** A line to keep in mind while looking (the question). */
  caption?: string | null
  onClose: () => void
}

/** The picture on its own, using the whole screen in a native modal top-layer dialog. */
export function PictureViewer({ src, alt, secondsLeft, caption, onClose }: ViewerProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const zoomRef = useRef<ZoomHandle>(null)
  const [size, setSize] = useState<Size | null>(null)
  const [turned, setTurned] = useState<boolean | null>(null) // null = decide from the picture and the screen
  const [upright, setUpright] = useState(() => window.innerHeight > window.innerWidth * 1.15)
  const [zoom, setZoom] = useState(1)
  const [isDesktop, setIsDesktop] = useState(() => window.matchMedia('(hover: hover) and (pointer: fine)').matches)

  useEffect(() => {
    const dialog = dialogRef.current
    if (dialog && !dialog.open) dialog.showModal()
    const media = window.matchMedia('(hover: hover) and (pointer: fine)')
    const onMedia = () => setIsDesktop(media.matches)
    const onResize = () => setUpright(window.innerHeight > window.innerWidth * 1.15)
    window.addEventListener('resize', onResize)
    media.addEventListener('change', onMedia)
    return () => {
      window.removeEventListener('resize', onResize)
      media.removeEventListener('change', onMedia)
      if (dialog?.open) dialog.close()
    }
  }, [])

  // A wide picture on an upright phone is shown on its side: it then uses the long edge of the screen.
  const wide = size ? size.w > size.h * 1.15 : false
  const quarterTurn = turned ?? (wide && upright)

  return (
    <dialog
      ref={dialogRef}
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
      className="arcade fixed inset-0 z-50 m-0 h-dvh max-h-none w-screen max-w-none bg-[#06050f] p-0 text-white backdrop:bg-black/90"
      aria-label="The picture, full screen"
    >
      <div className="absolute inset-0">
        <ZoomImage src={src} alt={alt} quarterTurn={quarterTurn} doubleTapZoom onSize={setSize} onZoomChange={setZoom} zoomRef={zoomRef} className="h-full w-full" />
      </div>
      <div
        className="pointer-events-none absolute inset-x-0 top-0 flex items-start justify-between gap-2 p-3"
        style={{ paddingTop: 'max(12px, env(safe-area-inset-top))' }}
      >
        <button className="btn btn-primary btn-sm pointer-events-auto" onClick={onClose} autoFocus>
          <IconClose size={13} /> Back <span className="kbd">ESC</span>
        </button>
        {secondsLeft != null && (
          <span
            className="pixel rounded-sm border-2 bg-black/85 px-3 py-2 text-sm tabular-nums"
            style={{
              borderColor: secondsLeft <= 5 ? '#ff4d6d' : '#ffb020',
              color: secondsLeft <= 5 ? '#ff4d6d' : '#ffb020',
            }}
            role="timer"
            aria-label="Time left"
          >
            {secondsLeft}s
          </span>
        )}
        <div className="pointer-events-auto flex items-center gap-1.5">
          {isDesktop && (
            <>
              <button className="btn btn-ghost btn-sm bg-black/75" onClick={() => zoomRef.current?.zoomBy(1 / 1.45)} disabled={zoom <= 1.02} aria-label="Zoom out" title="Zoom out">
                <IconZoomOut size={15} />
              </button>
              <button className="btn btn-ghost btn-sm bg-black/75 tabular-nums" onClick={() => zoomRef.current?.reset()} disabled={zoom <= 1.02} aria-label="Reset zoom" title="Reset zoom">
                {Math.round(zoom * 100)}%
              </button>
              <button className="btn btn-ghost btn-sm bg-black/75" onClick={() => zoomRef.current?.zoomBy(1.45)} disabled={zoom >= 5.9} aria-label="Zoom in" title="Zoom in">
                <IconZoomIn size={15} />
              </button>
            </>
          )}
          <button
            className="btn btn-ghost btn-sm bg-black/75"
            onClick={() => setTurned(!quarterTurn)}
            aria-label="Turn the picture"
            title="Turn the picture"
          >
            <IconRotate size={15} />
          </button>
        </div>
      </div>
      <div
        className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/90 to-transparent px-4 pb-3 pt-8 text-center"
        style={{ paddingBottom: 'max(12px, env(safe-area-inset-bottom))' }}
      >
        {caption && <p className="mx-auto line-clamp-2 max-w-2xl text-[15px] text-white">{caption}</p>}
        <p className="mt-1.5 text-xs tracking-wider text-white/65 uppercase">
          {isDesktop ? 'Scroll or use + / − to zoom · drag to pan · Esc to close' : 'Pinch or double-tap to zoom · drag to move'}
        </p>
      </div>
    </dialog>
  )
}
