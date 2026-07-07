import { useEffect, useRef, useState } from 'react'
import { sendChat } from '../api/client'
import type { AgentId, BoardLane, Task, ToolExecutionEvent } from '../types'
import { AGENT_LABELS } from '../types'

export interface ChatToolCallDisplay {
  toolName: string
  status: 'running' | 'completed' | 'failed' | 'awaiting_approval'
  toolOutput?: string
  toolArgs?: Record<string, unknown>
}

export interface ChatUiMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  agent?: AgentId
  toolCalls?: ChatToolCallDisplay[]
  splitHint?: string
}

interface ChatPanelProps {
  ollamaUrl: string
  filePaths: string[]
  agent: AgentId
  onAgentChange: (agent: AgentId) => void
  input: string
  onInputChange: (value: string) => void
  messages: ChatUiMessage[]
  onMessagesChange: (
    messages: ChatUiMessage[] | ((prev: ChatUiMessage[]) => ChatUiMessage[]),
  ) => void
  contextFiles: string[]
  onContextFilesChange: (files: string[]) => void
  pinnedTask?: Task | null
  pinnedLane?: BoardLane | null
  onClearPinnedTask?: () => void
  onRefreshState?: () => void
  onSplitTask?: (taskId: string) => void
  toolEvents?: ToolExecutionEvent[]
  onClearChat?: () => void | Promise<void>
  hidden?: boolean
}

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
}

function mapToolEvents(events: ToolExecutionEvent[]): ChatToolCallDisplay[] {
  return events.map((e) => ({
    toolName: e.toolName,
    status: e.status,
    toolOutput: e.toolOutput,
    toolArgs: e.toolArgs,
  }))
}

function ToolCallBlock({ call }: { call: ChatToolCallDisplay }) {
  const [open, setOpen] = useState(false)
  const failed = call.status === 'failed'
  const running = call.status === 'running'
  const awaiting = call.status === 'awaiting_approval'
  return (
    <div
      className={`text-left text-[10px] rounded border mb-1 ${
        failed
          ? 'border-rose-500/50 bg-rose-950/20'
          : awaiting
            ? 'border-amber-500/40 bg-amber-950/20'
            : running
              ? 'border-indigo-500/40 bg-indigo-950/20'
              : 'border-cat-surface1 bg-cat-surface0/50'
      }`}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full px-2 py-1 flex items-center justify-between gap-2 text-left"
      >
        <span className="font-mono text-indigo-300">{call.toolName}</span>
        <span className={failed ? 'text-rose-300' : awaiting ? 'text-amber-300' : running ? 'text-indigo-300' : 'text-emerald-400'}>
          {call.status}
        </span>
      </button>
      {open && (
        <div className="px-2 pb-2 font-mono text-cat-subtext whitespace-pre-wrap max-h-32 overflow-y-auto">
          {call.toolArgs && Object.keys(call.toolArgs).length > 0 && (
            <pre className="text-[9px] text-cat-overlay mb-1">
              {JSON.stringify(call.toolArgs, null, 2)}
            </pre>
          )}
          {call.toolOutput?.slice(0, 800) ?? '(no output)'}
        </div>
      )}
    </div>
  )
}

export default function ChatPanel({
  ollamaUrl,
  filePaths,
  agent,
  onAgentChange,
  input,
  onInputChange,
  messages,
  onMessagesChange,
  contextFiles,
  onContextFilesChange,
  pinnedTask,
  pinnedLane,
  onClearPinnedTask,
  onRefreshState,
  onSplitTask,
  toolEvents = [],
  onClearChat,
  hidden = false,
}: ChatPanelProps) {
  const [streaming, setStreaming] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [showFilePicker, setShowFilePicker] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const taskActionMode = Boolean(pinnedTask)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'auto' })
  }, [messages, toolEvents.length])

  useEffect(() => {
    return () => {
      abortRef.current?.abort()
    }
  }, [])

  const stopStreaming = () => {
    abortRef.current?.abort()
  }

  const sendMessage = async () => {
    const text = input.trim()
    if (!text || streaming) return

    const userMsg: ChatUiMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: text,
    }
    onMessagesChange((prev) => [...prev, userMsg])
    onInputChange('')
    setStreaming(true)

    const assistantId = crypto.randomUUID()
    onMessagesChange((prev) => [
      ...prev,
      { id: assistantId, role: 'assistant', content: '', agent },
    ])

    abortRef.current = new AbortController()
    const chatPayload = {
      agent,
      message: text,
      contextFiles: contextFiles.length > 0 ? contextFiles : undefined,
      ollama_url: ollamaUrl,
      taskId: pinnedTask?.id,
    }

    const toolBaseline = toolEvents.length

    try {
      const res = await sendChat(chatPayload, abortRef.current.signal)
      const content = res.response ?? res.reply ?? ''
      const fromApi = (res.toolCalls ?? []).map((e) => ({
        toolName: String(e.toolName ?? '?'),
        status: (e.status === 'failed' || e.toolSuccess === false
          ? 'failed'
          : 'completed') as ChatToolCallDisplay['status'],
        toolOutput: e.toolOutput,
        toolArgs: e.toolArgs,
      }))
      const capturedTools =
        fromApi.length > 0 ? fromApi : mapToolEvents(toolEvents.slice(toolBaseline))
      onMessagesChange((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? {
                ...m,
                content,
                toolCalls: capturedTools.length > 0 ? capturedTools : undefined,
                splitHint: res.splitHint,
              }
            : m,
        ),
      )
      onRefreshState?.()
    } catch (err) {
      if (isAbortError(err)) {
        onMessagesChange((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: m.content ? `${m.content}\n(Stopped)` : '(Stopped)' }
              : m,
          ),
        )
      } else {
        onMessagesChange((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  content:
                    m.content ||
                    (err instanceof Error ? err.message : '(Chat unavailable — check /api/chat)'),
                }
              : m,
          ),
        )
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
    }
  }

  const toggleContextFile = (path: string) => {
    onContextFilesChange(
      contextFiles.includes(path)
        ? contextFiles.filter((p) => p !== path)
        : [...contextFiles, path],
    )
  }

  const handleClearChat = async () => {
    if (!onClearChat || streaming || clearing || messages.length === 0) return
    setClearing(true)
    try {
      await onClearChat()
    } finally {
      setClearing(false)
    }
  }

  return (
    <div
      className={`flex flex-col h-full bg-cat-base overflow-hidden ${hidden ? 'hidden' : ''}`}
    >
      <div className="px-4 py-2 border-b border-cat-surface1 flex items-center gap-3 shrink-0 flex-wrap">
        <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
          Agent Chat
        </h3>
        <select
          value={agent}
          onChange={(e) => onAgentChange(e.target.value as AgentId)}
          disabled={streaming || clearing}
          className="bg-cat-surface0 border border-cat-surface1 rounded text-[11px] text-white px-2 py-1"
        >
          {(Object.keys(AGENT_LABELS) as AgentId[]).map((id) => (
            <option key={id} value={id}>
              {AGENT_LABELS[id]}
            </option>
          ))}
        </select>
        {onClearChat && messages.length > 0 && (
          <button
            type="button"
            onClick={() => void handleClearChat()}
            disabled={streaming || clearing}
            className="text-[10px] px-2 py-0.5 rounded border border-cat-surface1 text-cat-subtext hover:text-white hover:bg-cat-surface0 disabled:opacity-50"
          >
            {clearing ? 'Clearing…' : 'Clear'}
          </button>
        )}
        <button
          type="button"
          onClick={() => setShowFilePicker((s) => !s)}
          className="text-[11px] text-indigo-400 hover:text-indigo-300"
        >
          @ Files ({contextFiles.length})
        </button>
        {taskActionMode && pinnedTask && (
          <div className="flex items-center gap-2 ml-auto text-[10px] bg-amber-950/30 border border-amber-500/30 rounded px-2 py-1 max-w-[55%]">
            <span className="text-amber-200 truncate">
              Discussing: {pinnedTask.id} — {pinnedTask.title}
              {pinnedLane ? ` (${pinnedLane})` : ''}
            </span>
            {onClearPinnedTask && (
              <button
                type="button"
                onClick={onClearPinnedTask}
                className="text-amber-400 hover:text-amber-200 shrink-0"
                title="Clear pinned task"
              >
                ×
              </button>
            )}
          </div>
        )}
        <span className="text-[9px] text-cat-overlay uppercase tracking-wide">
          Tools run inline — see blocks below assistant replies
        </span>
      </div>

      {agent === 'po' && pinnedTask && (
        <p className="px-4 py-1.5 text-[10px] text-violet-300/90 bg-violet-950/20 border-b border-violet-500/20 shrink-0">
          To split this card, use <strong>Split into subtasks</strong> on the task detail — not a chat
          command. PO chat can still invoke split via tools when pinned to a card.
        </p>
      )}

      {showFilePicker && (
        <div className="border-b border-cat-surface1 max-h-32 overflow-y-auto p-2 space-y-1">
          {filePaths.map((path) => (
            <label
              key={path}
              className="flex items-center gap-2 text-[11px] font-mono text-cat-subtext cursor-pointer"
            >
              <input
                type="checkbox"
                checked={contextFiles.includes(path)}
                onChange={() => toggleContextFile(path)}
              />
              {path}
            </label>
          ))}
          {filePaths.length === 0 && (
            <p className="text-[10px] text-cat-overlay italic">No workspace files</p>
          )}
        </div>
      )}

      <div className="flex-1 overflow-y-auto p-3 space-y-3">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`max-w-[90%] ${msg.role === 'user' ? 'ml-auto text-right' : ''}`}
          >
            {msg.role === 'assistant' && msg.agent && (
              <span className="text-[10px] text-indigo-400 block mb-0.5">
                {AGENT_LABELS[msg.agent]}
              </span>
            )}
            {msg.toolCalls && msg.toolCalls.length > 0 && (
              <div className="mb-1 space-y-0.5">
                {msg.toolCalls.map((call, i) => (
                  <ToolCallBlock key={`${msg.id}-tool-${i}`} call={call} />
                ))}
              </div>
            )}
            <div
              className={`inline-block text-xs rounded-lg px-3 py-2 whitespace-pre-wrap ${
                msg.role === 'user'
                  ? 'bg-indigo-600/30 text-white'
                  : 'bg-cat-surface0 text-cat-text border border-cat-surface1'
              }`}
            >
              {msg.content || (streaming ? '…' : '')}
            </div>
            {msg.splitHint && (
              <div className="mt-1 text-[10px] text-amber-200 bg-amber-950/30 border border-amber-500/30 rounded px-2 py-1.5">
                {msg.splitHint}
                {pinnedTask && onSplitTask && (
                  <button
                    type="button"
                    onClick={() => onSplitTask(pinnedTask.id)}
                    className="ml-2 text-violet-300 hover:text-violet-200 underline"
                  >
                    Split now
                  </button>
                )}
              </div>
            )}
          </div>
        ))}
        {streaming &&
          toolEvents
            .slice(-3)
            .filter((e) => e.status === 'running')
            .map((e) => (
              <div key={e.id} className="max-w-[90%]">
                <ToolCallBlock call={mapToolEvents([e])[0]} />
              </div>
            ))}
        <div ref={bottomRef} />
      </div>

      <div className="p-3 border-t border-cat-surface1 flex gap-2 shrink-0">
        <input
          type="text"
          value={input}
          onChange={(e) => onInputChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === '@') setShowFilePicker(true)
            if (e.key === 'Enter' && !e.shiftKey) void sendMessage()
          }}
          placeholder={
            taskActionMode
              ? `Ask ${AGENT_LABELS[agent]} about ${pinnedTask?.id ?? 'this card'}… (@file to attach)`
              : `Message ${AGENT_LABELS[agent]}… Use @path or @folder/ to attach context`
          }
          disabled={streaming}
          className="flex-1 bg-cat-surface0 border border-cat-surface1 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500"
        />
        {streaming ? (
          <button
            type="button"
            onClick={stopStreaming}
            className="bg-rose-700 hover:bg-rose-600 text-white px-4 py-2 rounded-lg text-xs font-bold"
          >
            Stop
          </button>
        ) : (
          <button
            type="button"
            onClick={() => void sendMessage()}
            disabled={!input.trim()}
            className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-xs"
          >
            <i className="fa-solid fa-paper-plane" />
          </button>
        )}
      </div>
    </div>
  )
}
