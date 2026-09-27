import { memo, useCallback, useEffect, useState } from 'react'
import { validatePlanOutline } from '../api/client'
import type { PlanOutlineValidation } from '../types'
import { SettingHint } from './SettingHint'

function normalizePastedPlanClient(text: string): string {
  const raw = text.trim()
  if (!raw) return raw
  const planMatch = raw.match(/^#\s*plan\b.*$/im)
  if (planMatch?.index != null) return raw.slice(planMatch.index).trim()
  const summaryMatch = raw.match(/^##\s*summary\s*$/im)
  if (summaryMatch?.index != null) return raw.slice(summaryMatch.index).trim()
  return raw
}

const BRIEF_OPEN_KEY = 'allhands-brief-open'
const BRIEF_TAB_KEY = 'allhands-brief-tab'

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

type BriefTab = 'brief' | 'plan'

function readBriefTab(): BriefTab {
  try {
    const stored = localStorage.getItem(BRIEF_TAB_KEY)
    if (stored === 'plan') return 'plan'
  } catch {
    /* ignore */
  }
  return 'brief'
}

interface BriefPanelProps {
  brief: string
  originalBrief?: string
  onBriefChange: (value: string) => void
  onOpenManualTask: () => void
  autonomousMode?: boolean
  planOutline?: string
  onPlanOutlineChange?: (value: string) => void
  planOutlineStreaming?: boolean
  planBacklogActive?: boolean
  planTabFocusKey?: string
  onGenerateBacklog?: () => void
  generateBacklogDisabled?: boolean
  onReviewFeaturePack?: () => void
  hasFeaturePackPreview?: boolean
  /** Controlled open state (optional). */
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export default memo(function BriefPanel({
  brief,
  originalBrief = '',
  onBriefChange,
  onOpenManualTask,
  autonomousMode = false,
  planOutline = '',
  onPlanOutlineChange,
  planOutlineStreaming = false,
  planBacklogActive = false,
  planTabFocusKey,
  onGenerateBacklog,
  generateBacklogDisabled = false,
  onReviewFeaturePack,
  hasFeaturePackPreview = false,
  open: openProp,
  onOpenChange,
}: BriefPanelProps) {
  const [openInternal, setOpenInternal] = useState(readBriefOpen)
  const open = openProp ?? openInternal
  const setOpen = (next: boolean | ((prev: boolean) => boolean)) => {
    const value = typeof next === 'function' ? next(open) : next
    if (onOpenChange) onOpenChange(value)
    else setOpenInternal(value)
  }
  const [tab, setTab] = useState<BriefTab>(readBriefTab)
  const [planValidation, setPlanValidation] = useState<PlanOutlineValidation | null>(null)

  const refreshPlanValidation = useCallback(async () => {
    if (!planOutline.trim()) {
      setPlanValidation(null)
      return
    }
    try {
      const v = await validatePlanOutline(planOutline)
      setPlanValidation(v)
    } catch {
      setPlanValidation(null)
    }
  }, [planOutline])

  useEffect(() => {
    if (tab !== 'plan') return
    const t = window.setTimeout(() => void refreshPlanValidation(), 600)
    return () => window.clearTimeout(t)
  }, [tab, planOutline, refreshPlanValidation])

  useEffect(() => {
    writeBriefOpen(open)
  }, [open])

  useEffect(() => {
    try {
      localStorage.setItem(BRIEF_TAB_KEY, tab)
    } catch {
      /* ignore */
    }
  }, [tab])

  useEffect(() => {
    if (!planTabFocusKey || !planOutline.trim()) return
    setTab('plan')
  }, [planTabFocusKey, planOutline])

  const briefPreview =
    brief.trim().length > 0
      ? brief.trim().replace(/\s+/g, ' ').slice(0, 120) + (brief.trim().length > 120 ? '…' : '')
      : 'No brief yet — describe your project goals and features.'

  const planPreview = planOutline.trim()
    ? planOutline.trim().replace(/\s+/g, ' ').slice(0, 120) + (planOutline.trim().length > 120 ? '…' : '')
    : ''

  const collapsedPreview = planPreview || briefPreview

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
              {planPreview ? `[Plan] ${planPreview}` : collapsedPreview}
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
          <div className="flex gap-1 border-b border-cat-surface1 pb-2">
            <button
              type="button"
              onClick={() => setTab('brief')}
              className={`text-[11px] px-2 py-1 rounded ${tab === 'brief' ? 'bg-indigo-600/30 text-indigo-200' : 'text-cat-overlay hover:text-cat-subtext'}`}
            >
              Brief
            </button>
            <button
              type="button"
              onClick={() => setTab('plan')}
              className={`text-[11px] px-2 py-1 rounded ${tab === 'plan' ? 'bg-violet-600/30 text-violet-200' : 'text-cat-overlay hover:text-cat-subtext'}`}
            >
              Plan
              {planOutlineStreaming && (
                <i className="fa-solid fa-spinner animate-spin ml-1 text-[9px]" />
              )}
            </button>
          </div>
          {tab === 'brief' ? (
            <>
              {autonomousMode && (
                <p className="text-[11px] text-violet-300 bg-violet-950/25 border border-violet-500/30 rounded-lg px-3 py-2">
                  Autonomous mode — sprint will proceed with minimal prompts. Needs User stops are
                  capped per sprint.
                </p>
              )}
              <p className="text-[11px] text-cat-overlay leading-relaxed">
                Describe your project. Use Plan outline for a fast markdown plan, then generate
                backlog cards when ready.
              </p>
              <textarea
                value={brief}
                onChange={(e) => onBriefChange(e.target.value)}
                rows={8}
                className="w-full min-h-[140px] bg-cat-base border border-cat-surface1 rounded-lg p-3 text-sm text-white focus:outline-none focus:border-indigo-500 resize-y font-mono"
                placeholder="Describe your project goals, features, and constraints…"
              />
              {originalBrief.trim() && originalBrief.trim() !== brief.trim() && (
                <details className="text-[11px] text-cat-subtext border border-cat-surface1 rounded-lg px-3 py-2 bg-cat-mantle/40">
                  <summary className="cursor-pointer font-semibold text-cat-overlay">
                    Original brief
                  </summary>
                  <pre className="mt-2 whitespace-pre-wrap font-mono text-[11px] text-cat-subtext">
                    {originalBrief}
                  </pre>
                </details>
              )}
            </>
          ) : (
            <>
              <p className="text-[11px] text-cat-overlay leading-relaxed">
                Handoff-grade markdown (data model, flows, epic details). Paste chat plans here, then
                Normalize. Generate Features builds a preview you can export before Approve.
              </p>
              {planValidation && (
                <div
                  className={`text-[11px] rounded-lg px-3 py-2 border ${
                    planValidation.ok
                      ? 'border-emerald-500/30 bg-emerald-950/20 text-emerald-100'
                      : 'border-amber-500/30 bg-amber-950/20 text-amber-100'
                  }`}
                >
                  Plan quality score: {planValidation.score}
                  {planValidation.issues?.length > 0 && (
                    <ul className="mt-1 list-disc pl-4 text-[10px] opacity-90">
                      {planValidation.issues.slice(0, 5).map((issue) => (
                        <li key={issue.code + issue.message}>{issue.message}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
              <textarea
                value={planOutline}
                onChange={(e) => onPlanOutlineChange?.(e.target.value)}
                rows={12}
                readOnly={planOutlineStreaming}
                className="w-full min-h-[180px] bg-cat-base border border-cat-surface1 rounded-lg p-3 text-sm text-white focus:outline-none focus:border-violet-500 resize-y font-mono"
                placeholder="Run Plan outline from the sidebar — markdown plan streams here…"
              />
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  disabled={!planOutline.trim() || planOutlineStreaming}
                  onClick={() => onPlanOutlineChange?.(normalizePastedPlanClient(planOutline))}
                  className="text-xs bg-cat-surface1 hover:bg-cat-surface2 text-white py-2 px-3 rounded-lg"
                >
                  Normalize pasted plan
                </button>
                {onGenerateBacklog && (
                  <>
                    <button
                      type="button"
                      disabled={
                        generateBacklogDisabled ||
                        planOutlineStreaming ||
                        planBacklogActive ||
                        !planOutline.trim()
                      }
                      onClick={onGenerateBacklog}
                      className="text-xs bg-violet-700 hover:bg-violet-600 disabled:opacity-50 text-white font-medium py-2 px-3 rounded-lg transition-colors flex items-center gap-1.5"
                    >
                      {planBacklogActive ? (
                        <i className="fa-solid fa-spinner animate-spin" />
                      ) : (
                        <i className="fa-solid fa-layer-group" />
                      )}
                      {planBacklogActive ? 'Generating Features…' : 'Generate Features from plan'}
                    </button>
                    <SettingHint hint="Creates a reviewable feature pack (export markdown/JSON) before cards hit the board when approval is on." />
                  </>
                )}
                {hasFeaturePackPreview && onReviewFeaturePack && (
                  <button
                    type="button"
                    onClick={onReviewFeaturePack}
                    className="text-xs bg-indigo-700 hover:bg-indigo-600 text-white font-medium py-2 px-3 rounded-lg"
                  >
                    Review feature pack
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
})
