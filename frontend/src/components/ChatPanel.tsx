import { useEffect, useMemo, useRef, useState } from 'react'
import { sendChat } from '../api/client'
import type { AgentId, AgentRunState, BoardLane, Task, ToolExecutionEvent } from '../types'
import { AGENT_LABELS } from '../types'
import VirtualScrollList from './VirtualScrollList'

export const CLIENT_CHAT_MESSAGE_CAP = 100
const STUCK_STREAMING_MS = 5 * 60 * 1000

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
  /** Cursor-style /rewind — drop recent chat turns, keep earlier context. */
  onRewindChat?: (opts?: {
    dropTurns?: number
    mode?: 'turns' | 'before_last_error'
  }) => void | Promise<void>
  hidden?: boolean
  /** Live agent run (same SSE as sprint) for waiting status. */
  activeRun?: AgentRunState | null
}

function capChatMessages(messages: ChatUiMessage[]): ChatUiMessage[] {
  if (messages.length <= CLIENT_CHAT_MESSAGE_CAP) return messages
  return messages.slice(-CLIENT_CHAT_MESSAGE_CAP)
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
  onRewindChat,
  hidden = false,
  activeRun = null,
}: ChatPanelProps) {
  const [streaming, setStreaming] = useState(false)
  const [chatSending, setChatSending] = useState(false)
  const [clearing, setClearing] = useState(false)
  const [showFilePicker, setShowFilePicker] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const streamingSinceRef = useRef<number | null>(null)

  useEffect(() => {
    if (streaming) {
      if (streamingSinceRef.current == null) streamingSinceRef.current = Date.now()
    } else {
      streamingSinceRef.current = null
      setChatSending(false)
    }
  }, [streaming])

  useEffect(() => {
    if (hidden || !streaming) return
    const timer = window.setInterval(() => {
      const since = streamingSinceRef.current
      if (since == null) return
      if (Date.now() - since > STUCK_STREAMING_MS && !abortRef.current) {
        setStreaming(false)
        streamingSinceRef.current = null
      }
    }, 30_000)
    return () => window.clearInterval(timer)
  }, [hidden, streaming])

  const chatTailKey = useMemo(
    () => (messages.length > 0 ? messages[messages.length - 1]?.id : 'empty'),
    [messages],
  )

  const taskActionMode = Boolean(pinnedTask)

  const waitingStatus = useMemo(() => {
    if (!streaming) return ''
    if (chatSending) {
      return 'Sending to /api/chat… (waiting for backend). Stop to cancel.'
    }
    const runningTool = [...toolEvents].reverse().find((e) => e.status === 'running')
    const toolName = activeRun?.currentTool || runningTool?.toolName
    const iter =
      activeRun?.iteration != null && activeRun?.maxIterations != null
        ? `iter ${activeRun.iteration}/${activeRun.maxIterations}`
        : activeRun?.iteration != null
          ? `iter ${activeRun.iteration}`
          : ''
    const intent = activeRun?.intent || activeRun?.status
    const phase = activeRun?.devPhase
    const parts = [
      phase ? String(phase) : '',
      toolName ? `tool: ${toolName}` : '',
      iter,
      intent && intent !== 'idle' && intent !== phase ? String(intent) : '',
    ].filter(Boolean)
    if (parts.length === 0) {
      return 'Working… (tools may take a few minutes). You can switch tabs — the request keeps running.'
    }
    return `Working… ${parts.join(' · ')}. You can switch tabs — the request keeps running.`
  }, [streaming, chatSending, toolEvents, activeRun])

  const setMessagesCapped = (
    update: ChatUiMessage[] | ((prev: ChatUiMessage[]) => ChatUiMessage[]),
  ) => {
    onMessagesChange((prev) => {
      const next = typeof update === 'function' ? update(prev) : update
      return capChatMessages(next)
    })
  }

  const renderChatMessage = (msg: ChatUiMessage) => (
    <div className={`max-w-[90%] py-1.5 ${msg.role === 'user' ? 'ml-auto text-right' : ''}`}>
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
        data-testid={
          streaming && !msg.content && msg.id === messages[messages.length - 1]?.id
            ? 'chat-waiting-status'
            : undefined
        }
      >
        {msg.content ||
          (streaming && msg.id === messages[messages.length - 1]?.id ? waitingStatus : '')}
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
  )

  // Do not abort on unmount — App keeps this panel mounted (hidden) so tab switches
  // do not cancel in-flight chat. Stop button still aborts via stopStreaming.

  const stopStreaming = () => {
    abortRef.current?.abort()
  }

  const sendMessage = async () => {
    const text = input.trim()
    if (!text || streaming) return

    const rewindOpts = parseRewindCommand(text)
    if (rewindOpts) {
      onInputChange('')
      if (!onRewindChat) return
      await handleRewindChat(rewindOpts)
      return
    }

    const userMsg: ChatUiMessage = {
      id: crypto.randomUUID(),
      role: 'user',
      content: text,
    }
    setMessagesCapped((prev) => [...prev, userMsg])
    onInputChange('')
    setStreaming(true)

    const assistantId = crypto.randomUUID()
    setMessagesCapped((prev) => [
      ...prev,
      { id: assistantId, role: 'assistant', content: '', agent },
    ])

    abortRef.current = new AbortController()
    const allowDoneRetry = pinnedLane === 'Done'
    const chatPayload = {
      agent,
      message: text,
      contextFiles: contextFiles.length > 0 ? contextFiles : undefined,
      ollama_url: ollamaUrl,
      taskId: pinnedTask?.id,
      allowDoneRetry: allowDoneRetry || undefined,
    }

    const toolBaseline = toolEvents.length

    try {
      setChatSending(true)
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
      setMessagesCapped((prev) =>
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
    } catch (err) {
      if (isAbortError(err)) {
        setMessagesCapped((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  content: m.content
                    ? `${m.content}\n(Stopped — request cancelled)`
                    : '(Stopped — request cancelled)',
                }
              : m,
          ),
        )
      } else {
        setMessagesCapped((prev) =>
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
      setChatSending(false)
      setStreaming(false)
      abortRef.current = null
      onRefreshState?.()
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

  const handleRewindChat = async (opts?: {
    dropTurns?: number
    mode?: 'turns' | 'before_last_error'
  }) => {
    if (!onRewindChat || streaming || clearing || messages.length === 0) return
    setClearing(true)
    try {
      await onRewindChat(opts)
    } finally {
      setClearing(false)
    }
  }

  const parseRewindCommand = (
    text: string,
  ): { dropTurns?: number; mode?: 'turns' | 'before_last_error' } | null => {
    const trimmed = text.trim()
    const m = trimmed.match(/^\/rewind(?:\s+(\d+|error|before[-_]?error))?$/i)
    if (!m) return null
    const arg = (m[1] || '').toLowerCase()
    if (!arg) return { dropTurns: 1, mode: 'turns' }
    if (arg === 'error' || arg.startsWith('before')) {
      return { mode: 'before_last_error', dropTurns: 1 }
    }
    const n = Number(arg)
    if (!Number.isFinite(n) || n < 1) return { dropTurns: 1, mode: 'turns' }
    return { dropTurns: Math.min(50, Math.floor(n)), mode: 'turns' }
  }

  return (
    <div
      className={`flex flex-col h-full bg-cat-base overflow-hidden ${hidden ? 'hidden' : ''}`}
      data-testid="chat-panel"
      data-chat-hidden={hidden ? 'true' : 'false'}
      aria-hidden={hidden}
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
        {onRewindChat && messages.length > 0 && (
          <button
            type="button"
            onClick={() => void handleRewindChat({ dropTurns: 1 })}
            disabled={streaming || clearing}
            title="Drop last chat turn (/rewind). Use /rewind 2 or /rewind error"
            className="text-[10px] px-2 py-0.5 rounded border border-cat-surface1 text-cat-subtext hover:text-white hover:bg-cat-surface0 disabled:opacity-50"
            data-testid="chat-rewind"
          >
            Rewind
          </button>
        )}
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

      {taskActionMode && pinnedTask && (
        <p className="px-4 py-1 text-[10px] text-cat-overlay bg-cat-base/80 border-b border-cat-surface1 shrink-0">
          Type a message and press Send — chat does not start automatically when you open Discuss.
          {pinnedLane === 'Done' && (
            <span className="text-amber-200/90"> Done cards allow chat with allowDoneRetry.</span>
          )}
        </p>
      )}

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

      <div className="flex-1 min-h-0 flex flex-col">
        <VirtualScrollList
          className="flex-1 min-h-0 p-3"
          items={messages}
          newestFirst={false}
          estimateRowHeight={96}
          defaultCap={150}
          itemsTailKey={chatTailKey}
          autoScrollEndKey={`${chatTailKey}-${toolEvents.length}`}
          getKey={(msg) => msg.id}
          empty={
            <p className="text-[10px] text-cat-overlay italic text-center pt-4">
              No messages yet — start a conversation below.
            </p>
          }
          renderRow={(msg) => renderChatMessage(msg)}
        />
        {streaming &&
          toolEvents
            .slice(-3)
            .filter((e) => e.status === 'running')
            .map((e) => (
              <div key={e.id} className="max-w-[90%] px-3 pb-2 shrink-0">
                <ToolCallBlock call={mapToolEvents([e])[0]} />
              </div>
            ))}
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
              ? `Ask ${AGENT_LABELS[agent]} about ${pinnedTask?.id ?? 'this card'}… (/rewind to jump back)`
              : `Message ${AGENT_LABELS[agent]}… @file · /rewind · /rewind error`
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
