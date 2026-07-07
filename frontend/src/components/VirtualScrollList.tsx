import {
  useCallback,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type UIEvent,
} from 'react'

interface VirtualScrollListProps<T> {
  items: T[]
  getKey: (item: T, index: number) => string | number
  renderRow: (item: T, index: number) => ReactNode
  estimateRowHeight?: number
  overscan?: number
  defaultCap?: number
  className?: string
  empty?: ReactNode
  onScroll?: () => void
}

export default function VirtualScrollList<T>({
  items,
  getKey,
  renderRow,
  estimateRowHeight = 72,
  overscan = 8,
  defaultCap = 150,
  className = '',
  empty,
  onScroll,
}: VirtualScrollListProps<T>) {
  const [showAll, setShowAll] = useState(false)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewportHeight, setViewportHeight] = useState(400)
  const containerRef = useRef<HTMLDivElement>(null)

  const displayItems = useMemo(() => {
    if (showAll || items.length <= defaultCap) return items
    return items.slice(-defaultCap)
  }, [items, showAll, defaultCap])

  const hiddenCount = items.length - displayItems.length

  const { startIndex, endIndex, offsetY, totalHeight } = useMemo(() => {
    const total = displayItems.length * estimateRowHeight
    const start = Math.max(0, Math.floor(scrollTop / estimateRowHeight) - overscan)
    const visible = Math.ceil(viewportHeight / estimateRowHeight) + overscan * 2
    const end = Math.min(displayItems.length, start + visible)
    return {
      startIndex: start,
      endIndex: end,
      offsetY: start * estimateRowHeight,
      totalHeight: total,
    }
  }, [displayItems.length, scrollTop, viewportHeight, estimateRowHeight, overscan])

  const visibleSlice = displayItems.slice(startIndex, endIndex)

  const handleScroll = useCallback(
    (event: UIEvent<HTMLDivElement>) => {
      const el = event.currentTarget
      setScrollTop(el.scrollTop)
      setViewportHeight(el.clientHeight)
      onScroll?.()
    },
    [onScroll],
  )

  if (items.length === 0) {
    return <div className={className}>{empty}</div>
  }

  return (
    <div className={`flex flex-col min-h-0 ${className}`}>
      {hiddenCount > 0 && !showAll && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="shrink-0 text-[10px] text-indigo-400 hover:text-indigo-300 py-1 px-3 border-b border-cat-surface1/40"
        >
          Show all ({items.length}) — currently showing newest {defaultCap}
        </button>
      )}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 min-h-0 overflow-y-auto"
      >
        <div style={{ height: totalHeight, position: 'relative' }}>
          <div style={{ transform: `translateY(${offsetY}px)` }}>
            {visibleSlice.map((item, i) => {
              const index = startIndex + i
              return (
                <div key={getKey(item, index)} style={{ minHeight: estimateRowHeight }}>
                  {renderRow(item, index)}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
