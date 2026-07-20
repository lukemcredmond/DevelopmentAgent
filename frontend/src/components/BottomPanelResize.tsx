import { useCallback, useEffect, useRef, type RefObject } from 'react'

export const BOTTOM_PANEL_STORAGE_KEY = 'allhands-bottom-panel-h'
export const BOTTOM_PANEL_COLLAPSED_KEY = 'allhands-bottom-panel-collapsed'
export const BOTTOM_PANEL_MIN = 220
export const BOTTOM_PANEL_DEFAULT = 320
/** Height when only the tab strip (+ run bars) should stay visible. */
export const BOTTOM_PANEL_COLLAPSED_HEIGHT = 40

export function readBottomPanelHeight(): number {
  try {
    const stored = localStorage.getItem(BOTTOM_PANEL_STORAGE_KEY)
    if (stored) {
      const n = Number(stored)
      if (!Number.isNaN(n) && n >= BOTTOM_PANEL_MIN) return n
    }
  } catch {
    /* ignore */
  }
  return BOTTOM_PANEL_DEFAULT
}

export function writeBottomPanelHeight(height: number): void {
  try {
    localStorage.setItem(BOTTOM_PANEL_STORAGE_KEY, String(Math.round(height)))
  } catch {
    /* ignore */
  }
}

export function readBottomPanelCollapsed(): boolean {
  try {
    return localStorage.getItem(BOTTOM_PANEL_COLLAPSED_KEY) === 'true'
  } catch {
    return false
  }
}

export function writeBottomPanelCollapsed(collapsed: boolean): void {
  try {
    localStorage.setItem(BOTTOM_PANEL_COLLAPSED_KEY, String(collapsed))
  } catch {
    /* ignore */
  }
}

interface BottomPanelResizeProps {
  onResize: (height: number) => void
  containerRef: RefObject<HTMLElement | null>
  disabled?: boolean
}

export default function BottomPanelResize({
  onResize,
  containerRef,
  disabled = false,
}: BottomPanelResizeProps) {
  const draggingRef = useRef(false)

  const handlePointerDown = useCallback(
    (e: React.PointerEvent) => {
      if (disabled) return
      e.preventDefault()
      draggingRef.current = true
      const container = containerRef.current
      if (!container) return

      const startY = e.clientY
      const startHeight = container.querySelector<HTMLElement>('[data-bottom-panel]')?.offsetHeight ?? BOTTOM_PANEL_DEFAULT
      const parentHeight = container.clientHeight

      const onMove = (ev: PointerEvent) => {
        if (!draggingRef.current) return
        const delta = startY - ev.clientY
        const maxH = parentHeight * 0.7
        const next = Math.min(maxH, Math.max(BOTTOM_PANEL_MIN, startHeight + delta))
        onResize(next)
      }

      const onUp = () => {
        draggingRef.current = false
        window.removeEventListener('pointermove', onMove)
        window.removeEventListener('pointerup', onUp)
      }

      window.addEventListener('pointermove', onMove)
      window.addEventListener('pointerup', onUp)
    },
    [containerRef, onResize, disabled],
  )

  useEffect(() => {
    return () => {
      draggingRef.current = false
    }
  }, [])

  if (disabled) {
    return <div className="shrink-0 h-px bg-cat-surface1" aria-hidden />
  }

  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-label="Resize bottom panel"
      title="Drag up to enlarge panel"
      onPointerDown={handlePointerDown}
      className="shrink-0 h-1 cursor-row-resize bg-cat-surface1 hover:bg-indigo-500/50 active:bg-indigo-500/70 transition-colors group flex items-center justify-center"
    >
      <div className="w-10 h-0.5 rounded bg-cat-overlay group-hover:bg-indigo-300/80" />
    </div>
  )
}
