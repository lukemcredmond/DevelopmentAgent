import { useState, type ReactNode } from 'react'

export type StatusItem = {
  id: string
  tone: 'error' | 'notice' | 'warning'
  summary: string
  detail?: ReactNode
  actions?: ReactNode
}

interface StatusStripProps {
  items: StatusItem[]
}

const toneClass: Record<StatusItem['tone'], string> = {
  error: 'text-rose-200 bg-rose-950/40 border-rose-500/40',
  notice: 'text-emerald-200 bg-emerald-950/40 border-emerald-500/40',
  warning: 'text-amber-200 bg-amber-950/40 border-amber-500/40',
}

export default function StatusStrip({ items }: StatusStripProps) {
  const [expanded, setExpanded] = useState(false)
  if (items.length === 0) return null

  const primary = items[0]
  const extra = items.length - 1

  return (
    <div className={`mx-4 mt-2 shrink-0 border rounded-lg ${toneClass[primary.tone]}`}>
      <div className="flex items-center justify-between gap-2 px-3 py-2 text-[11px]">
        <div className="flex items-center gap-2 min-w-0 flex-1">
          <i className="fa-solid fa-circle-info shrink-0 opacity-80" />
          <span className="truncate">{primary.summary}</span>
          {extra > 0 && (
            <button
              type="button"
              onClick={() => setExpanded((e) => !e)}
              className="shrink-0 text-[10px] underline opacity-90 hover:opacity-100"
            >
              {expanded ? 'Hide' : `+${extra} more`}
            </button>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">{primary.actions}</div>
      </div>
      {(expanded || items.length === 1) && primary.detail ? (
        <div className="px-3 pb-2 text-[10px] opacity-90 border-t border-white/5 pt-2">
          {primary.detail}
        </div>
      ) : null}
      {expanded &&
        items.slice(1).map((item) => (
          <div
            key={item.id}
            className={`px-3 py-2 text-[11px] border-t border-white/10 flex items-start justify-between gap-2 ${toneClass[item.tone]}`}
          >
            <div className="min-w-0 flex-1">
              <div className="font-semibold">{item.summary}</div>
              {item.detail ? <div className="mt-1 text-[10px] opacity-90">{item.detail}</div> : null}
            </div>
            {item.actions ? <div className="shrink-0 flex items-center gap-2">{item.actions}</div> : null}
          </div>
        ))}
    </div>
  )
}
