import type { AgentRunState } from '../types'

interface AgentRunBarProps {
  activeRun: AgentRunState | null
  currentTool?: string | null
}

export default function AgentRunBar({ activeRun, currentTool }: AgentRunBarProps) {
  if (!activeRun || activeRun.status === 'completed' || activeRun.status === 'idle') {
    return null
  }

  const toolLabel = currentTool || activeRun.currentTool
  const statusLabel =
    activeRun.status === 'awaiting_approval'
      ? 'awaiting approval'
      : activeRun.status === 'tool_executing'
        ? 'running tool'
        : activeRun.status

  return (
    <div className="shrink-0 border-b border-indigo-500/30 bg-indigo-950/30 px-4 py-1.5 flex items-center gap-3 text-[11px] font-mono">
      <span className="inline-block w-2 h-2 rounded-full bg-indigo-400 animate-pulse" />
      <span className="text-indigo-200 font-bold">{activeRun.agent}</span>
      <span className="text-cat-subtext">{statusLabel}</span>
      {toolLabel && (
        <span className="text-indigo-300 truncate">
          {toolLabel}
        </span>
      )}
      <span className="text-cat-overlay ml-auto text-[10px]">{activeRun.taskId}</span>
    </div>
  )
}
