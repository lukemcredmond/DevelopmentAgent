import { useCallback, useEffect, useRef, type RefObject } from 'react'

export const BOTTOM_PANEL_STORAGE_KEY = 'allhands-bottom-panel-h'
export const BOTTOM_PANEL_MIN = 220
export const BOTTOM_PANEL_DEFAULT = 320

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

interface BottomPanelResizeProps {
  onResize: (height: number) => void
  containerRef: RefObject<HTMLElement | null>
}

export default function BottomPanelResize({ onResize, containerRef }: BottomPanelResizeProps) {
  const draggingRef = useRef(false)

  const handlePointerDown = useCallback(
    (e: React.PointerEvent) => {
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
    [containerRef, onResize],
  )

  useEffect(() => {
    return () => {
      draggingRef.current = false
    }
  }, [])

  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      aria-label="Resize bottom panel"
      onPointerDown={handlePointerDown}
      className="shrink-0 h-1 cursor-row-resize bg-cat-surface1 hover:bg-indigo-500/50 active:bg-indigo-500/70 transition-colors group flex items-center justify-center"
    >
      <div className="w-10 h-0.5 rounded bg-cat-overlay group-hover:bg-indigo-300/80" />
    </div>
  )
}
