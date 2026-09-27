import { useCallback, useMemo, useState } from 'react'
import type { FeaturePackPreview } from '../types'
import { approveFeaturePack, exportFeaturesPackUrl } from '../api/client'

interface FeatureReviewModalProps {
  open: boolean
  pack: FeaturePackPreview | null | undefined
  onClose: () => void
  onApproved: () => void
  onRegenerate?: () => void
  exportMarkdown: () => Promise<string>
}

export default function FeatureReviewModal({
  open,
  pack,
  onClose,
  onApproved,
  onRegenerate,
  exportMarkdown,
}: FeatureReviewModalProps) {
  const [busy, setBusy] = useState(false)
  const [copyMsg, setCopyMsg] = useState('')

  const stats = pack?.stats
  const epics = pack?.epics ?? []
  const issues = pack?.issues ?? []

  const summaryLine = useMemo(() => {
    if (!stats) return ''
    return `${stats.epicCount} epic(s), ${stats.childCount} child card(s), ${stats.childrenSpecOk ?? 0} spec-ready`
  }, [stats])

  const handleCopy = useCallback(async () => {
    try {
      const md = await exportMarkdown()
      await navigator.clipboard.writeText(md)
      setCopyMsg('Copied markdown to clipboard')
    } catch {
      setCopyMsg('Copy failed')
    }
  }, [exportMarkdown])

  const handleApprove = useCallback(async () => {
    setBusy(true)
    try {
      await approveFeaturePack()
      onApproved()
      onClose()
    } finally {
      setBusy(false)
    }
  }, [onApproved, onClose])

  if (!open || !pack) return null

  return (
    <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4">
      <div
        className="bg-cat-mantle border border-cat-surface1 rounded-xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col"
        role="dialog"
        aria-labelledby="feature-review-title"
      >
        <div className="px-4 py-3 border-b border-cat-surface1 flex items-center justify-between gap-2">
          <h2 id="feature-review-title" className="text-sm font-semibold text-white">
            Review generated features
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="text-cat-subtext hover:text-white text-lg leading-none"
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="px-4 py-2 text-[11px] text-cat-overlay border-b border-cat-surface1">
          {summaryLine}
          {pack.ok ? (
            <span className="ml-2 text-emerald-300">Quality: OK</span>
          ) : (
            <span className="ml-2 text-amber-300">Quality: needs review</span>
          )}
        </div>
        <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 text-xs">
          {issues.length > 0 && (
            <ul className="space-y-1 text-[11px]">
              {issues.map((issue, i) => (
                <li
                  key={`${issue.code}-${i}`}
                  className={
                    issue.severity === 'fail'
                      ? 'text-amber-200 bg-amber-950/30 border border-amber-500/30 rounded px-2 py-1'
                      : 'text-cat-overlay bg-cat-base border border-cat-surface1 rounded px-2 py-1'
                  }
                >
                  {issue.message}
                </li>
              ))}
            </ul>
          )}
          {epics.map((epic) => (
            <div key={epic.title} className="border border-cat-surface1 rounded-lg p-2 bg-cat-base/40">
              <p className="font-semibold text-violet-200">{epic.title}</p>
              {epic.description && (
                <p className="text-cat-subtext mt-0.5 text-[11px]">{epic.description}</p>
              )}
              <ul className="mt-2 space-y-2">
                {(epic.children ?? []).map((child) => {
                  const ready = child.specReadiness
                  return (
                    <li key={child.title} className="pl-2 border-l-2 border-cat-surface1">
                      <p className="text-white">{child.title}</p>
                      {ready?.ok ? (
                        <p className="text-[10px] text-emerald-300/90">Spec readiness: OK</p>
                      ) : (
                        <p className="text-[10px] text-amber-200/90">
                          Gaps: {(ready?.missing ?? []).join(', ') || 'see card'}
                        </p>
                      )}
                    </li>
                  )
                })}
              </ul>
            </div>
          ))}
        </div>
        <div className="px-4 py-3 border-t border-cat-surface1 flex flex-wrap gap-2 items-center">
          <button
            type="button"
            onClick={() => void handleCopy()}
            className="text-xs bg-cat-surface1 hover:bg-cat-surface2 text-white py-2 px-3 rounded-lg"
          >
            Copy markdown
          </button>
          <a
            href={exportFeaturesPackUrl('md')}
            download="features-pack.md"
            className="text-xs bg-cat-surface1 hover:bg-cat-surface2 text-white py-2 px-3 rounded-lg"
          >
            Download .md
          </a>
          <a
            href={exportFeaturesPackUrl('json')}
            download="features-pack.json"
            className="text-xs bg-cat-surface1 hover:bg-cat-surface2 text-white py-2 px-3 rounded-lg"
          >
            Download .json
          </a>
          {onRegenerate && (
            <button
              type="button"
              onClick={onRegenerate}
              className="text-xs text-cat-overlay hover:text-white py-2 px-3"
            >
              Regenerate
            </button>
          )}
          <div className="flex-1" />
          {copyMsg && <span className="text-[10px] text-cat-overlay">{copyMsg}</span>}
          <button
            type="button"
            disabled={busy}
            onClick={() => void handleApprove()}
            className="text-xs bg-violet-700 hover:bg-violet-600 disabled:opacity-50 text-white font-medium py-2 px-4 rounded-lg"
          >
            {busy ? 'Creating…' : 'Approve & create cards'}
          </button>
        </div>
      </div>
    </div>
  )
}
