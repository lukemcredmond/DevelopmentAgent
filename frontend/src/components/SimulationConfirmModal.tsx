import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { confirmSimulation, dismissSimulation, readWorkspaceFile } from '../api/client'
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
  focusNonce?: number
}

function previewLines(pending: PendingSimulation): string[] {
  const p = pending.defaultPreview ?? {}
  const lines: string[] = []
  if (p.fileName) lines.push(`File: ${String(p.fileName)}`)
  if (p.workspaceFileExists) lines.push('Workspace file already exists on disk')
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
  focusNonce = 0,
}: SimulationConfirmModalProps) {
  const seconds = Math.min(60, Math.max(1, workflowSettings?.simulationConfirmSeconds ?? 10))
  const autoAcceptEnabled = workflowSettings?.simulationAutoAccept === true
  const [remaining, setRemaining] = useState(seconds)
  const [overrideOpen, setOverrideOpen] = useState(false)
  const [userEngaged, setUserEngaged] = useState(false)
  const [overrideValue, setOverrideValue] = useState('')
  const [overrideTarget, setOverrideTarget] = useState('agent_text')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [prefillLoading, setPrefillLoading] = useState(false)
  const acceptedRef = useRef(false)
  const dialogRef = useRef<HTMLDivElement>(null)

  const open = Boolean(pending?.id)
  const workspaceFileExists = Boolean(pending?.defaultPreview?.workspaceFileExists)
  const devKind = pending?.kind === 'sprint_dev'
  const fileName = pending?.defaultPreview?.fileName
    ? String(pending.defaultPreview.fileName)
    : ''

  useEffect(() => {
    if (!open) return
    acceptedRef.current = false
    setRemaining(seconds)
    setOverrideOpen(false)
    setUserEngaged(false)
    setOverrideValue('')
    setOverrideTarget(devKind ? 'dev_file_content' : 'agent_text')
    setError(null)
  }, [open, pending?.id, seconds, devKind])

  useEffect(() => {
    if (!open || !autoAcceptEnabled) return
    const grace = window.setTimeout(() => setUserEngaged(true), 3000)
    return () => window.clearTimeout(grace)
  }, [open, pending?.id, autoAcceptEnabled])

  useEffect(() => {
    if (open && focusNonce > 0) {
      dialogRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }, [open, focusNonce])

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

  const useExistingFile = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      const data = await confirmSimulation({
        accept: false,
        overrideTarget: 'use_workspace_file',
      })
      onResolved(data)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  useEffect(() => {
    if (!open || overrideOpen || busy || !autoAcceptEnabled) return
    if (!userEngaged) return
    if (remaining <= 0) {
      void accept()
      return
    }
    const t = window.setTimeout(() => setRemaining((r) => r - 1), 1000)
    return () => window.clearTimeout(t)
  }, [open, overrideOpen, busy, remaining, accept, autoAcceptEnabled, userEngaged])

  const openAlternative = async () => {
    setUserEngaged(true)
    setOverrideOpen(true)
    if (devKind && fileName && !overrideValue) {
      setPrefillLoading(true)
      try {
        const res = await readWorkspaceFile(fileName)
        setOverrideValue(res.content)
        setOverrideTarget('dev_file_content')
      } catch {
        /* prefill optional */
      } finally {
        setPrefillLoading(false)
      }
    }
  }

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
      <div
        ref={dialogRef}
        className="bg-cat-mantle border border-amber-500/40 rounded-xl max-w-lg w-full shadow-xl"
      >
        <div className="px-4 py-3 border-b border-cat-surface1">
          <h2 id="simulation-confirm-title" className="text-sm font-bold text-amber-100">
            Offline simulation
          </h2>
          <p className="text-[11px] text-cat-subtext mt-1">
            Ollama is unavailable. The sprint is paused until you confirm or provide an alternative
            value.
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
          {!overrideOpen && autoAcceptEnabled && userEngaged && (
            <p className="text-amber-200/90">
              Auto-accept in <strong>{remaining}</strong>s
            </p>
          )}
          {!overrideOpen && !autoAcceptEnabled && (
            <p className="text-cat-subtext">Choose an action below — no auto-accept.</p>
          )}
          {error && <p className="text-rose-300">{error}</p>}
        </div>
        {!overrideOpen ? (
          <div className="px-4 py-3 border-t border-cat-surface1 flex flex-wrap gap-2 justify-end">
            {workspaceFileExists && devKind && (
              <button
                type="button"
                disabled={busy}
                onClick={() => void useExistingFile()}
                className="px-3 py-1.5 rounded text-xs bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-50"
                data-testid="simulation-use-existing-file"
              >
                Use existing workspace file
              </button>
            )}
            <button
              type="button"
              disabled={busy}
              onClick={() => void openAlternative()}
              className="px-3 py-1.5 rounded text-xs border border-cat-surface2 hover:bg-cat-surface0 disabled:opacity-50"
              data-testid="simulation-provide-alternative"
            >
              Provide alternative value
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setUserEngaged(true)
                void accept()
              }}
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
              {prefillLoading && (
                <span className="ml-2 text-cat-overlay">Loading from workspace…</span>
              )}
              <textarea
                value={overrideValue}
                onChange={(e) => setOverrideValue(e.target.value)}
                rows={6}
                className="mt-1 w-full rounded bg-cat-crust border border-cat-surface1 px-2 py-1 text-xs text-white"
                disabled={overrideTarget === 'skip_step' || prefillLoading}
                data-testid="simulation-override-value"
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
                disabled={busy || (overrideTarget !== 'skip_step' && !overrideValue.trim())}
                onClick={() => void submitOverride()}
                className="px-3 py-1.5 rounded text-xs bg-emerald-700 hover:bg-emerald-600 text-white disabled:opacity-50"
                data-testid="simulation-continue-new-value"
              >
                Continue with new value
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
