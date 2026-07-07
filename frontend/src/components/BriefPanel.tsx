import { memo, useEffect, useState } from 'react'

const BRIEF_OPEN_KEY = 'allhands-brief-open'

export function readBriefOpen(): boolean {
  try {
    const stored = localStorage.getItem(BRIEF_OPEN_KEY)
    if (stored === 'false') return false
    if (stored === 'true') return true
  } catch {
    /* ignore */
  }
  return true
}

function writeBriefOpen(open: boolean): void {
  try {
    localStorage.setItem(BRIEF_OPEN_KEY, String(open))
  } catch {
    /* ignore */
  }
}

interface BriefPanelProps {
  brief: string
  onBriefChange: (value: string) => void
  onOpenManualTask: () => void
  autonomousMode?: boolean
}

export default memo(function BriefPanel({
  brief,
  onBriefChange,
  onOpenManualTask,
  autonomousMode = false,
}: BriefPanelProps) {
  const [open, setOpen] = useState(readBriefOpen)

  useEffect(() => {
    writeBriefOpen(open)
  }, [open])

  const preview =
    brief.trim().length > 0
      ? brief.trim().replace(/\s+/g, ' ').slice(0, 120) + (brief.trim().length > 120 ? '…' : '')
      : 'No brief yet — describe your project goals and features.'

  return (
    <div className="mx-4 mt-2 shrink-0 bg-cat-surface0 border border-cat-surface1 rounded-xl overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-4 py-2 border-b border-cat-surface1 bg-cat-mantle/40">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-2 text-left min-w-0 flex-1"
        >
          <i className={`fa-solid fa-chevron-${open ? 'up' : 'down'} text-cat-overlay text-[10px] shrink-0`} />
          <span className="text-xs font-bold uppercase tracking-wider text-cat-subtext shrink-0">
            Project Brief
          </span>
          {!open && (
            <span className="text-[11px] text-cat-overlay truncate font-normal normal-case">
              {preview}
            </span>
          )}
        </button>
        <button
          type="button"
          onClick={onOpenManualTask}
          className="text-xs text-indigo-400 hover:text-indigo-300 font-semibold flex items-center gap-1 shrink-0"
        >
          <i className="fa-solid fa-square-plus" />
          Add Feature
        </button>
      </div>
      {open && (
        <div className="p-4 space-y-2">
          {autonomousMode && (
            <p className="text-[11px] text-violet-300 bg-violet-950/25 border border-violet-500/30 rounded-lg px-3 py-2">
              Autonomous mode — sprint will proceed with minimal prompts. Needs User stops are
              capped per sprint.
            </p>
          )}
          <p className="text-[11px] text-cat-overlay leading-relaxed">
            Describe your project. Plan & Run automates PO → Dev → QA. Developer questions go to
            Needs PO; user decisions go to Needs User.
          </p>
          <textarea
            value={brief}
            onChange={(e) => onBriefChange(e.target.value)}
            rows={8}
            className="w-full min-h-[140px] bg-cat-base border border-cat-surface1 rounded-lg p-3 text-sm text-white focus:outline-none focus:border-indigo-500 resize-y font-mono"
            placeholder="Describe your project goals, features, and constraints…"
          />
        </div>
      )}
    </div>
  )
})
