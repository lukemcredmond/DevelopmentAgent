import type { PendingToolApproval } from '../types'
import { formatTaskText } from '../utils/taskFormat'
import SlideOver from './SlideOver'

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
  const handle = async (approved: boolean) => {
    if (!pending) return
    await onApprove(pending.id, approved)
    onResolved()
    onClose()
  }

  return (
    <SlideOver
      open={!!pending}
      onClose={onClose}
      side="right"
      title="Approve tool execution?"
      widthClass="w-full max-w-lg"
      zIndexClass="z-[60]"
      footer={
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
      }
    >
      {pending && (
        <div className="p-4 space-y-3">
          <p className="text-[11px] text-cat-subtext">
            {formatTaskText(pending.agent)} wants to run{' '}
            <span className="text-amber-300 font-mono">{pending.toolName}</span>
            {pending.taskId ? ` on task ${pending.taskId}` : ''}.
            {(pending.nonBlocking ?? true) && (
              <span className="block mt-2 text-cat-overlay text-[10px]">
                Non-blocking mode: the sprint step is paused until you approve or deny.
              </span>
            )}
          </p>
          <pre className="text-[10px] font-mono bg-cat-base border border-cat-surface1 rounded p-3 overflow-x-auto text-cat-subtext">
            {JSON.stringify(pending.toolArgs ?? {}, null, 2)}
          </pre>
        </div>
      )}
    </SlideOver>
  )
}
