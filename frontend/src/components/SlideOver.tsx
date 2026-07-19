import { useEffect, useId, type ReactNode } from 'react'

export interface SlideOverProps {
  open: boolean
  onClose: () => void
  side?: 'left' | 'right'
  title?: ReactNode
  /** Tailwind width classes, e.g. w-full max-w-md */
  widthClass?: string
  zIndexClass?: string
  children: ReactNode
  footer?: ReactNode
  /** When true, omit the built-in title bar (caller provides sticky header). */
  hideHeader?: boolean
}

export default function SlideOver({
  open,
  onClose,
  side = 'right',
  title,
  widthClass = 'w-full max-w-md',
  zIndexClass = 'z-50',
  children,
  footer,
  hideHeader = false,
}: SlideOverProps) {
  const titleId = useId()

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const fromRight = side === 'right'

  return (
    <div className={`fixed inset-0 ${zIndexClass}`} role="presentation">
      <button
        type="button"
        aria-label="Close panel"
        className="absolute inset-0 bg-black/50 transition-opacity"
        onClick={onClose}
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-labelledby={title && !hideHeader ? titleId : undefined}
        className={`absolute inset-y-0 ${fromRight ? 'right-0' : 'left-0'} ${widthClass} flex flex-col bg-cat-surface0 border-cat-surface1 shadow-2xl ${
          fromRight ? 'border-l' : 'border-r'
        } animate-in ${fromRight ? 'slide-in-from-right' : 'slide-in-from-left'} duration-200`}
        style={{
          animation: fromRight
            ? 'slideOverInRight 180ms ease-out'
            : 'slideOverInLeft 180ms ease-out',
        }}
      >
        {!hideHeader && (
          <div className="shrink-0 flex items-center justify-between gap-3 px-4 py-3 border-b border-cat-surface1">
            <h2 id={titleId} className="text-sm font-bold text-white truncate flex-1 min-w-0">
              {title}
            </h2>
            <button
              type="button"
              onClick={onClose}
              className="p-1.5 rounded-lg text-cat-subtext hover:text-white hover:bg-cat-surface1 shrink-0"
              aria-label="Close"
            >
              <i className="fa-solid fa-xmark" />
            </button>
          </div>
        )}
        <div
          className={
            hideHeader
              ? 'flex-1 min-h-0 overflow-hidden flex flex-col'
              : 'flex-1 min-h-0 overflow-y-auto'
          }
        >
          {children}
        </div>
        {footer ? (
          <div className="shrink-0 border-t border-cat-surface1 px-4 py-3 bg-cat-mantle/50">
            {footer}
          </div>
        ) : null}
      </aside>
      <style>{`
        @keyframes slideOverInRight {
          from { transform: translateX(100%); opacity: 0.85; }
          to { transform: translateX(0); opacity: 1; }
        }
        @keyframes slideOverInLeft {
          from { transform: translateX(-100%); opacity: 0.85; }
          to { transform: translateX(0); opacity: 1; }
        }
      `}</style>
    </div>
  )
}
