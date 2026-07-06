import type { PendingToolApproval } from '../types'
import { formatTaskText } from '../utils/taskFormat'

interface ToolApprovalModalProps {
  pending: PendingToolApproval | null
  onClose: () => void
  onResolved: () => void
  onApprove: (id: string, approved: boolean) => Promise<void>
}

export default function ToolApprovalModal({
  pending,
  onClose,
  onResolved,
  onApprove,
}: ToolApprovalModalProps) {
  if (!pending) return null

  const handle = async (approved: boolean) => {
    await onApprove(pending.id, approved)
    onResolved()
    onClose()
  }

  return (
    <div className="fixed inset-0 bg-black/75 flex items-center justify-center p-4 z-[60]">
      <div className="bg-cat-surface0 rounded-2xl max-w-lg w-full border border-amber-500/40 shadow-2xl p-6">
        <h3 className="text-base font-bold text-white mb-1">Approve tool execution?</h3>
        <p className="text-[11px] text-cat-subtext mb-4">
          {formatTaskText(pending.agent)} wants to run{' '}
          <span className="text-amber-300 font-mono">{pending.toolName}</span>
          {pending.taskId ? ` on task ${pending.taskId}` : ''}.
          {(pending.nonBlocking ?? true) && (
            <span className="block mt-2 text-cat-overlay text-[10px]">
              Non-blocking mode: the sprint step is paused until you approve or deny.
            </span>
          )}
        </p>
        <pre className="text-[10px] font-mono bg-cat-base border border-cat-surface1 rounded p-3 mb-4 overflow-x-auto text-cat-subtext">
          {JSON.stringify(pending.toolArgs ?? {}, null, 2)}
        </pre>
        <div className="flex gap-2 justify-end">
          <button
            type="button"
            onClick={() => void handle(false)}
            className="px-4 py-2 text-sm rounded-lg border border-rose-500/40 text-rose-300 hover:bg-rose-950/30"
          >
            Deny
          </button>
          <button
            type="button"
            onClick={() => void handle(true)}
            className="px-4 py-2 text-sm rounded-lg bg-emerald-700/60 text-white hover:bg-emerald-600/60"
          >
            Approve
          </button>
        </div>
      </div>
    </div>
  )
}
