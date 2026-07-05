import { useEffect, useRef, useState } from 'react'
import { sendChat, streamChat } from '../api/client'
import type { AgentId, BoardLane, Task } from '../types'
import { AGENT_LABELS } from '../types'

export interface ChatUiMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  agent?: AgentId
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
  hidden?: boolean
}

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError'
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
  hidden = false,
}: ChatPanelProps) {
  const [streaming, setStreaming] = useState(false)
  const [showFilePicker, setShowFilePicker] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  const taskActionMode = Boolean(pinnedTask)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

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

    try {
      if (taskActionMode) {
        const res = await sendChat(chatPayload, abortRef.current.signal)
        const content = res.response ?? res.reply ?? ''
        onMessagesChange((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, content } : m)),
        )
        onRefreshState?.()
      } else {
        let full = ''
        for await (const token of streamChat(chatPayload, abortRef.current.signal)) {
          full += token
          const content = full
          onMessagesChange((prev) =>
            prev.map((m) => (m.id === assistantId ? { ...m, content } : m)),
          )
        }
      }
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
          disabled={streaming}
          className="bg-cat-surface0 border border-cat-surface1 rounded text-[11px] text-white px-2 py-1"
        >
          {(Object.keys(AGENT_LABELS) as AgentId[]).map((id) => (
            <option key={id} value={id}>
              {AGENT_LABELS[id]}
            </option>
          ))}
        </select>
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
        {taskActionMode && (
          <span className="text-[9px] text-cat-overlay uppercase tracking-wide">
            Task mode — agent can use tools
          </span>
        )}
      </div>

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
            <div
              className={`inline-block text-xs rounded-lg px-3 py-2 whitespace-pre-wrap ${
                msg.role === 'user'
                  ? 'bg-indigo-600/30 text-white'
                  : 'bg-cat-surface0 text-cat-text border border-cat-surface1'
              }`}
            >
              {msg.content || (streaming ? '…' : '')}
            </div>
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
              ? `Ask ${AGENT_LABELS[agent]} about ${pinnedTask?.id ?? 'this card'}…`
              : `Message ${AGENT_LABELS[agent]}…`
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
