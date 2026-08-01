import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { confirmSimulation, dismissSimulation } from '../api/client'
import type { AppState, PendingSimulation, WorkflowSettings } from '../types'

const OVERRIDE_TARGETS: { value: string; label: string }[] = [
  { value: 'agent_text', label: 'Agent / chat text (no board simulate)' },
  { value: 'dev_file_content', label: 'Dev file content (write workspace file)' },
  { value: 'board_lane', label: 'Move card to lane' },
  { value: 'qa_pass', label: 'QA pass → Done' },
  { value: 'qa_fail', label: 'QA fail → In Progress' },
  { value: 'po_output', label: 'PO output (plan / backlog JSON or outline)' },
  { value: 'skip_step', label: 'Skip — apply nothing' },
]

interface SimulationConfirmModalProps {
  pending: PendingSimulation | null | undefined
  workflowSettings?: WorkflowSettings
  onResolved: (state: AppState) => void
}

function previewLines(pending: PendingSimulation): string[] {
  const p = pending.defaultPreview ?? {}
  const lines: string[] = []
  if (p.fileName) lines.push(`File: ${String(p.fileName)}`)
  if (p.targetLane) lines.push(`Lane: ${String(p.targetLane)}`)
  if (p.fileContent) lines.push(String(p.fileContent).slice(0, 200))
  if (p.outlineSnippet) lines.push(String(p.outlineSnippet).slice(0, 200))
  if (p.likelyOutcome) lines.push(String(p.likelyOutcome))
  if (p.message) lines.push(String(p.message))
  if (p.findings) lines.push(`Findings: ${String(p.findings)}`)
  if (p.note) lines.push(String(p.note))
  if (p.subtaskCount) lines.push(`Subtasks: ${String(p.subtaskCount)}`)
  return lines
}

export default function SimulationConfirmModal({
  pending,
  workflowSettings,
  onResolved,
}: SimulationConfirmModalProps) {
  const seconds = Math.min(60, Math.max(1, workflowSettings?.simulationConfirmSeconds ?? 10))
  const [remaining, setRemaining] = useState(seconds)
  const [overrideOpen, setOverrideOpen] = useState(false)
  const [overrideValue, setOverrideValue] = useState('')
  const [overrideTarget, setOverrideTarget] = useState('agent_text')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const acceptedRef = useRef(false)

  const open = Boolean(pending?.id)

  useEffect(() => {
    if (!open) return
    acceptedRef.current = false
    setRemaining(seconds)
    setOverrideOpen(false)
    setOverrideValue('')
    setOverrideTarget('agent_text')
    setError(null)
  }, [open, pending?.id, seconds])

  const accept = useCallback(async () => {
    if (acceptedRef.current || busy) return
    acceptedRef.current = true
    setBusy(true)
    setError(null)
    try {
      const data = await confirmSimulation({ accept: true })
      onResolved(data)
    } catch (err) {
      acceptedRef.current = false
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }, [busy, onResolved])

  useEffect(() => {
    if (!open || overrideOpen || busy) return
    if (remaining <= 0) {
      void accept()
      return
    }
    const t = window.setTimeout(() => setRemaining((r) => r - 1), 1000)
    return () => window.clearTimeout(t)
  }, [open, overrideOpen, busy, remaining, accept])

  const submitOverride = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const data = await confirmSimulation({
        accept: false,
        overrideTarget,
        overrideValue: overrideTarget === 'skip_step' ? '' : overrideValue,
      })
      onResolved(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const dismiss = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const data = await dismissSimulation()
      onResolved(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  const hints = useMemo(() => (pending ? previewLines(pending) : []), [pending])

  if (!open || !pending) return null

  return (
    <div
      className="fixed inset-0 z-[90] flex items-center justify-center bg-black/65 p-4"
      role="dialog"
      aria-modal
      aria-labelledby="simulation-confirm-title"
      data-testid="simulation-confirm-modal"
    >
      <div className="bg-cat-mantle border border-amber-500/40 rounded-xl max-w-lg w-full shadow-xl">
        <div className="px-4 py-3 border-b border-cat-surface1">
          <h2 id="simulation-confirm-title" className="text-sm font-bold text-amber-100">
            Offline simulation
          </h2>
          <p className="text-[11px] text-cat-subtext mt-1">
            Ollama is unavailable. The app will apply a simulated result unless you choose otherwise.
          </p>
        </div>
        <div className="px-4 py-3 space-y-2 text-[11px] text-cat-text">
          <p>
            <span className="text-cat-overlay">Task:</span> {pending.taskId ?? '—'}{' '}
            <span className="text-cat-overlay ml-2">Agent:</span> {pending.agent ?? '—'}
          </p>
          <p className="font-medium text-white">{pending.title}</p>
          <p>{pending.summary}</p>
          {hints.length > 0 && (
            <pre className="bg-cat-crust rounded p-2 text-[10px] whitespace-pre-wrap text-cat-subtext max-h-28 overflow-y-auto">
              {hints.join('\n')}
            </pre>
          )}
          {!overrideOpen && (
            <p className="text-amber-200/90">
              Auto-accept in <strong>{remaining}</strong>s
            </p>
          )}
          {error && <p className="text-rose-300">{error}</p>}
        </div>
        {!overrideOpen ? (
          <div className="px-4 py-3 border-t border-cat-surface1 flex flex-wrap gap-2 justify-end">
            <button
              type="button"
              disabled={busy}
              onClick={() => setOverrideOpen(true)}
              className="px-3 py-1.5 rounded text-xs border border-cat-surface2 hover:bg-cat-surface0 disabled:opacity-50"
            >
              Use something else
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void accept()}
              className="px-3 py-1.5 rounded text-xs bg-amber-600 hover:bg-amber-500 text-white disabled:opacity-50"
            >
              Use simulated result
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void dismiss()}
              className="px-3 py-1.5 rounded text-xs text-cat-overlay hover:text-white disabled:opacity-50"
            >
              Dismiss
            </button>
          </div>
        ) : (
          <div className="px-4 py-3 border-t border-cat-surface1 space-y-2">
            <label className="block text-[11px] text-cat-subtext">
              What should be used instead?
              <textarea
                value={overrideValue}
                onChange={(e) => setOverrideValue(e.target.value)}
                rows={4}
                className="mt-1 w-full rounded bg-cat-crust border border-cat-surface1 px-2 py-1 text-xs text-white"
                disabled={overrideTarget === 'skip_step'}
              />
            </label>
            <label className="block text-[11px] text-cat-subtext">
              Use this for
              <select
                value={overrideTarget}
                onChange={(e) => setOverrideTarget(e.target.value)}
                className="mt-1 w-full rounded bg-cat-crust border border-cat-surface1 px-2 py-1 text-xs text-white"
              >
                {OVERRIDE_TARGETS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <div className="flex gap-2 justify-end pt-1">
              <button
                type="button"
                disabled={busy}
                onClick={() => setOverrideOpen(false)}
                className="px-3 py-1.5 rounded text-xs border border-cat-surface2 hover:bg-cat-surface0"
              >
                Back
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => void submitOverride()}
                className="px-3 py-1.5 rounded text-xs bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-50"
              >
                Apply override
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
