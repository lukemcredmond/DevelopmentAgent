import { useEffect, useState } from 'react'
import type { ProjectToolEvidence } from '../types'

interface EvidencePanelProps {
  entries: ProjectToolEvidence[]
  defaultCommand: string
  onInject: (payload: {
    toolName: string
    toolArgs: Record<string, unknown>
    toolOutput: string
    note?: string
  }) => void | Promise<void>
  onDelete: (entryId: string) => void | Promise<void>
  onClearAll: () => void | Promise<void>
}

export default function EvidencePanel({
  entries,
  defaultCommand,
  onInject,
  onDelete,
  onClearAll,
}: EvidencePanelProps) {
  const [command, setCommand] = useState(defaultCommand)
  const [output, setOutput] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setCommand(defaultCommand)
  }, [defaultCommand])

  const handleInject = async () => {
    if (!output.trim()) return
    setBusy(true)
    try {
      await onInject({
        toolName: 'run_command',
        toolArgs: { command: command.trim() || defaultCommand || 'analyze' },
        toolOutput: output.trim(),
        note: note.trim() || undefined,
      })
      setOutput('')
      setNote('')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col h-full min-h-0 p-3 gap-3 overflow-y-auto">
      <div className="space-y-1 shrink-0">
        <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
          Project evidence
        </h3>
        <p className="text-[11px] text-cat-overlay leading-relaxed">
          Paste workspace-wide analyze/test output here so every agent can see it. For results that
          belong to one card (QA gate / stuck In Progress), open that card and use{' '}
          <span className="text-cat-subtext">Provide command output</span> instead.
        </p>
      </div>

      <div className="bg-indigo-950/20 border border-indigo-500/30 rounded-lg p-3 space-y-2 shrink-0">
        <label className="flex flex-col gap-1">
          <span className="text-[10px] uppercase text-cat-overlay">Command</span>
          <input
            type="text"
            value={command}
            onChange={(e) => setCommand(e.target.value)}
            placeholder={defaultCommand || 'e.g. npm run lint'}
            className="bg-cat-base border border-cat-surface1 rounded px-2 py-1 text-[11px] text-white"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[10px] uppercase text-cat-overlay">Output</span>
          <textarea
            value={output}
            onChange={(e) => setOutput(e.target.value)}
            rows={6}
            placeholder="Paste command output…"
            className="bg-cat-base border border-cat-surface1 rounded px-2 py-1 text-[11px] text-white font-mono"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-[10px] uppercase text-cat-overlay">Note (optional)</span>
          <input
            type="text"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className="bg-cat-base border border-cat-surface1 rounded px-2 py-1 text-[11px] text-white"
          />
        </label>
        <button
          type="button"
          disabled={busy || !output.trim()}
          onClick={() => void handleInject()}
          className="w-full bg-indigo-600/40 hover:bg-indigo-600/60 disabled:opacity-50 text-indigo-100 text-xs py-2 px-3 rounded-lg border border-indigo-500/30"
        >
          {busy ? 'Injecting…' : 'Inject project evidence'}
        </button>
      </div>

      <div className="flex items-center justify-between shrink-0">
        <h4 className="text-[10px] font-bold uppercase tracking-wider text-cat-overlay">
          Recent ({entries.length})
        </h4>
        {entries.length > 0 && (
          <button
            type="button"
            disabled={busy}
            onClick={() => void onClearAll()}
            className="text-[10px] text-rose-300 hover:text-rose-200"
          >
            Clear all
          </button>
        )}
      </div>

      <div className="space-y-2 pb-2">
        {entries.length === 0 && (
          <p className="text-[11px] text-cat-overlay italic">No project evidence yet.</p>
        )}
        {entries.map((e) => (
          <div
            key={e.id}
            className="rounded-lg border border-cat-surface1 bg-cat-base/40 p-2 space-y-1"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="min-w-0">
                <p className="text-[11px] font-mono text-indigo-200 truncate">
                  {e.command || e.toolName}
                </p>
                <p className="text-[10px] text-cat-overlay">{e.timestamp}</p>
              </div>
              <button
                type="button"
                onClick={() => void onDelete(e.id)}
                className="text-[10px] text-cat-overlay hover:text-rose-300 shrink-0"
              >
                Remove
              </button>
            </div>
            {e.note && <p className="text-[10px] text-cat-subtext">{e.note}</p>}
            <pre className="text-[10px] text-cat-subtext font-mono whitespace-pre-wrap max-h-24 overflow-y-auto">
              {(e.toolOutput || '').slice(0, 600)}
              {(e.toolOutput || '').length > 600 ? '…' : ''}
            </pre>
          </div>
        ))}
      </div>
    </div>
  )
}
