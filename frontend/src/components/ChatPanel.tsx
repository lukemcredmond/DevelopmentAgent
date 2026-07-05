import { useEffect, useRef, useState } from 'react'
import { streamChat } from '../api/client'
import type { AgentId } from '../types'
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
  hidden?: boolean
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
  hidden = false,
}: ChatPanelProps) {
  const [streaming, setStreaming] = useState(false)
  const [showFilePicker, setShowFilePicker] = useState(false)
  const bottomRef = useRef<HTMLDivElement>(null)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

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

    try {
      let full = ''
      for await (const token of streamChat(
        {
          agent,
          message: text,
          contextFiles: contextFiles.length > 0 ? contextFiles : undefined,
          ollama_url: ollamaUrl,
        },
        abortRef.current.signal,
      )) {
        full += token
        const content = full
        onMessagesChange((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, content } : m)),
        )
      }
    } catch {
      onMessagesChange((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? { ...m, content: m.content || '(Stream unavailable — check /api/chat/stream)' }
            : m,
        ),
      )
    } finally {
      setStreaming(false)
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
      <div className="px-4 py-2 border-b border-cat-surface1 flex items-center gap-3 shrink-0">
        <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
          Agent Chat
        </h3>
        <select
          value={agent}
          onChange={(e) => onAgentChange(e.target.value as AgentId)}
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
          placeholder={`Message ${AGENT_LABELS[agent]}…`}
          disabled={streaming}
          className="flex-1 bg-cat-surface0 border border-cat-surface1 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-indigo-500"
        />
        <button
          type="button"
          onClick={() => void sendMessage()}
          disabled={streaming || !input.trim()}
          className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white px-4 py-2 rounded-lg text-xs"
        >
          {streaming ? (
            <i className="fa-solid fa-spinner animate-spin" />
          ) : (
            <i className="fa-solid fa-paper-plane" />
          )}
        </button>
      </div>
    </div>
  )
}
