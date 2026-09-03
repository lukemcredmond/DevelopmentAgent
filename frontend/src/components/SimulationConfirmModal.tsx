import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { confirmSimulation, dismissSimulation, readWorkspaceFile, ApiError } from '../api/client'
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
  const autoUseExisting =
    workflowSettings?.simulationAutoUseExistingFile !== false
  const [remaining, setRemaining] = useState(seconds)
  const [userEngaged, setUserEngaged] = useState(false)
  const [overrideValue, setOverrideValue] = useState('')
  const [overrideTarget, setOverrideTarget] = useState('agent_text')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [prefillLoading, setPrefillLoading] = useState(false)
  const acceptedRef = useRef(false)
  const autoExistingRef = useRef(false)
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
    autoExistingRef.current = false
    setRemaining(seconds)
    setUserEngaged(false)
    setOverrideValue('')
    setOverrideTarget(devKind ? 'dev_file_content' : 'agent_text')
    setError(null)
  }, [open, pending?.id, seconds, devKind])

  useEffect(() => {
    if (!open || !devKind || !fileName) return
    let cancelled = false
    setPrefillLoading(true)
    void readWorkspaceFile(fileName)
      .then((res) => {
        if (!cancelled) setOverrideValue(res.content)
      })
      .catch(() => {
        /* optional prefill */
      })
      .finally(() => {
        if (!cancelled) setPrefillLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, pending?.id, devKind, fileName])

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

  const useExistingFile = useCallback(async () => {
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
  }, [busy, onResolved])

  useEffect(() => {
    if (
      !open ||
      busy ||
      autoExistingRef.current ||
      !autoUseExisting ||
      !workspaceFileExists ||
      !devKind
    ) {
      return
    }
    autoExistingRef.current = true
    void useExistingFile()
  }, [open, busy, autoUseExisting, workspaceFileExists, devKind, useExistingFile])

  useEffect(() => {
    if (!open || busy || !autoAcceptEnabled) return
    if (!userEngaged) return
    if (remaining <= 0) {
      void accept()
      return
    }
    const t = window.setTimeout(() => setRemaining((r) => r - 1), 1000)
    return () => window.clearTimeout(t)
  }, [open, busy, remaining, accept, autoAcceptEnabled, userEngaged])

  const submitOverride = async () => {
    if (busy) return
    setUserEngaged(true)
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
      onResolved({ ...data, pendingSimulation: null })
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        onResolved({ pendingSimulation: null } as AppState)
        return
      }
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
        className="bg-cat-mantle border border-amber-500/40 rounded-xl max-w-lg w-full max-h-[90vh] flex flex-col shadow-xl"
      >
        <div className="px-4 py-3 border-b border-cat-surface1 shrink-0">
          <h2 id="simulation-confirm-title" className="text-sm font-bold text-amber-100">
            Offline simulation
          </h2>
          <p className="text-[11px] text-cat-subtext mt-1">
            LLM call failed / provider unreachable.
            {pending.lastChatError ? ` ${pending.lastChatError}` : ''}
          </p>
        </div>
        <div className="px-4 py-3 space-y-2 text-[11px] text-cat-text overflow-y-auto flex-1 min-h-0">
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
          {autoAcceptEnabled && userEngaged && (
            <p className="text-amber-200/90">
              Auto-accept simulated result in <strong>{remaining}</strong>s
            </p>
          )}
          {!autoAcceptEnabled && (
            <p className="text-cat-subtext">No auto-accept — choose an action or enter a value below.</p>
          )}
          {error && <p className="text-rose-300">{error}</p>}

          <div className="border-t border-cat-surface1 pt-3 mt-2 space-y-2">
            <h3 className="text-[11px] font-semibold text-white">Or enter an alternative value</h3>
            <label className="block text-[11px] text-cat-subtext">
              Value to use
              {prefillLoading && (
                <span className="ml-2 text-cat-overlay">Loading from workspace…</span>
              )}
              <textarea
                value={overrideValue}
                onChange={(e) => {
                  setUserEngaged(true)
                  setOverrideValue(e.target.value)
                }}
                rows={5}
                placeholder="Paste or edit the outcome (file content, agent text, PO JSON, lane name, …)"
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
            <div className="flex justify-end">
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
        </div>
        <div className="px-4 py-3 border-t border-cat-surface1 flex flex-wrap gap-2 justify-end shrink-0">
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
      </div>
    </div>
  )
}
