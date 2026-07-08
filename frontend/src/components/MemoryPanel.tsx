import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  createProjectMemory,
  deleteProjectMemory,
  fetchProjectMemories,
  updateProjectMemory,
} from '../api/client'
import type { ProjectMemoryEntry } from '../types'

type MemoryTypeFilter = 'all' | 'user_note' | 'auto'

const CATEGORY_OPTIONS = ['user_note', 'fix_pattern', 'failure', 'tool_usage'] as const

function agentLabel(agent: string): string {
  return agent === '__project__' ? 'Project (all agents)' : agent
}

interface MemoryPanelProps {
  ollamaUrl?: string
  onCountChange?: (count: number) => void
}

export default function MemoryPanel({ ollamaUrl = 'http://localhost:11434', onCountChange }: MemoryPanelProps) {
  const [entries, setEntries] = useState<ProjectMemoryEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [typeFilter, setTypeFilter] = useState<MemoryTypeFilter>('all')
  const [agentFilter, setAgentFilter] = useState('all')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [groupDuplicates, setGroupDuplicates] = useState(true)
  const [newNote, setNewNote] = useState('')
  const [saving, setSaving] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editContent, setEditContent] = useState('')
  const [editCategory, setEditCategory] = useState('user_note')
  const [agentOptionsSource, setAgentOptionsSource] = useState<ProjectMemoryEntry[]>([])

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedSearch(searchQuery.trim()), 300)
    return () => window.clearTimeout(timer)
  }, [searchQuery])

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const apiCategory =
        categoryFilter !== 'all'
          ? categoryFilter
          : typeFilter === 'user_note'
            ? 'user_note'
            : undefined

      const data = await fetchProjectMemories(ollamaUrl, 200, {
        agent: agentFilter !== 'all' ? agentFilter : undefined,
        category: apiCategory,
        q: debouncedSearch || undefined,
        dedupe: groupDuplicates,
      })

      let list = data.entries ?? []

      if (categoryFilter === 'all' && typeFilter === 'auto') {
        list = list.filter((e) => e.category !== 'user_note')
      }

      setEntries(list)
      onCountChange?.(list.length)

      if (agentFilter === 'all' && categoryFilter === 'all' && !debouncedSearch) {
        setAgentOptionsSource(data.entries ?? [])
      }
    } catch {
      setEntries([])
      onCountChange?.(0)
    } finally {
      setLoading(false)
    }
  }, [
    ollamaUrl,
    onCountChange,
    agentFilter,
    categoryFilter,
    typeFilter,
    debouncedSearch,
    groupDuplicates,
  ])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const agentOptions = useMemo(() => {
    const source = agentOptionsSource.length > 0 ? agentOptionsSource : entries
    const counts = new Map<string, number>()
    for (const e of source) {
      counts.set(e.agent, (counts.get(e.agent) ?? 0) + 1)
    }
    return Array.from(counts.entries())
      .sort(([a], [b]) => agentLabel(a).localeCompare(agentLabel(b)))
      .map(([agent, count]) => ({ agent, count }))
  }, [agentOptionsSource, entries])

  const handleAdd = () => {
    const text = newNote.trim()
    if (!text) return
    setSaving(true)
    void createProjectMemory(text, ollamaUrl)
      .then(() => {
        setNewNote('')
        return refresh()
      })
      .finally(() => setSaving(false))
  }

  const startEdit = (entry: ProjectMemoryEntry) => {
    setEditingId(entry.id)
    setEditContent(entry.content)
    setEditCategory(entry.category || 'user_note')
  }

  const cancelEdit = () => {
    setEditingId(null)
    setEditContent('')
  }

  const saveEdit = () => {
    if (!editingId || !editContent.trim()) return
    setSaving(true)
    void updateProjectMemory(editingId, editContent.trim(), editCategory, ollamaUrl)
      .then(() => {
        cancelEdit()
        return refresh()
      })
      .finally(() => setSaving(false))
  }

  const handleDelete = (entry: ProjectMemoryEntry) => {
    const ids = entry.duplicateIds?.length ? entry.duplicateIds : [entry.id]
    const message =
      ids.length > 1
        ? `Delete ${ids.length} duplicate memory entries?`
        : 'Delete this memory entry?'
    if (!window.confirm(message)) return
    void Promise.all(ids.map((id) => deleteProjectMemory(id))).then(() => refresh())
  }

  return (
    <div className="flex flex-col h-full min-h-0 bg-cat-base text-[11px]">
      <div className="shrink-0 border-b border-cat-surface1 px-4 py-2 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[10px] uppercase text-cat-overlay tracking-wide">Project memory</span>
          <button
            type="button"
            onClick={() => void refresh()}
            disabled={loading}
            className="text-[10px] text-cat-overlay hover:text-white disabled:opacity-50 ml-auto"
          >
            {loading ? 'Loading…' : 'Refresh'}
          </button>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value as MemoryTypeFilter)}
            className="bg-cat-mantle border border-cat-surface1 rounded px-2 py-0.5 text-[10px]"
          >
            <option value="all">All types</option>
            <option value="user_note">User notes</option>
            <option value="auto">Tool outcomes</option>
          </select>
          <select
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            className="bg-cat-mantle border border-cat-surface1 rounded px-2 py-0.5 text-[10px]"
          >
            <option value="all">All agents</option>
            {agentOptions.map(({ agent, count }) => (
              <option key={agent} value={agent}>
                {agentLabel(agent)} ({count})
              </option>
            ))}
          </select>
          <select
            value={categoryFilter}
            onChange={(e) => setCategoryFilter(e.target.value)}
            className="bg-cat-mantle border border-cat-surface1 rounded px-2 py-0.5 text-[10px]"
          >
            <option value="all">All categories</option>
            {CATEGORY_OPTIONS.map((cat) => (
              <option key={cat} value={cat}>
                {cat}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-1.5 text-[10px] text-cat-subtext cursor-pointer">
            <input
              type="checkbox"
              checked={groupDuplicates}
              onChange={(e) => setGroupDuplicates(e.target.checked)}
              className="rounded border-cat-surface1"
            />
            Group duplicates
          </label>
        </div>
        <input
          type="search"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder="Search memory content…"
          className="w-full text-[10px] bg-cat-mantle border border-cat-surface1 rounded px-2 py-1 text-white"
        />
      </div>

      <div className="shrink-0 border-b border-cat-surface1 px-4 py-2 space-y-2">
        <p className="text-[10px] text-cat-overlay leading-relaxed">
          Memories are searched across all agents and injected into sprint prompts (top 3 matches).
          Pin facts here so Dev/PO/QA do not forget project conventions.
        </p>
        <textarea
          value={newNote}
          onChange={(e) => setNewNote(e.target.value)}
          placeholder="Add a project fact (e.g. API keys in .env, auth pattern in Program.cs)…"
          className="w-full text-[11px] bg-cat-mantle border border-cat-surface1 rounded p-2 min-h-[56px] text-white"
        />
        <button
          type="button"
          disabled={saving || !newNote.trim()}
          onClick={handleAdd}
          className="text-[10px] px-3 py-1 rounded bg-indigo-600/50 text-white disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save note'}
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto p-3 space-y-2">
        {entries.length === 0 ? (
          <p className="text-cat-overlay text-center py-8 italic">
            No memories match these filters. Save notes above or run sprint steps — tool outcomes are
            recorded for file edits and failures.
          </p>
        ) : (
          entries.map((entry) => (
            <div
              key={entry.id}
              className="border border-cat-surface1 rounded-lg p-3 bg-cat-mantle/30 space-y-2"
            >
              <div className="flex flex-wrap gap-2 text-[10px] items-center">
                <span className="text-indigo-300 font-semibold">{agentLabel(entry.agent)}</span>
                <span className="text-cat-overlay">{entry.category}</span>
                <span className="text-cat-overlay">{entry.timestamp}</span>
                {(entry.duplicateCount ?? 1) > 1 && (
                  <span className="text-[9px] bg-amber-950/50 text-amber-300 px-1.5 py-0.5 rounded-full">
                    ×{entry.duplicateCount}
                  </span>
                )}
              </div>
              {editingId === entry.id ? (
                <div className="space-y-2">
                  <textarea
                    value={editContent}
                    onChange={(e) => setEditContent(e.target.value)}
                    className="w-full text-[11px] bg-cat-base border border-cat-surface1 rounded p-2 min-h-[72px] text-white font-mono"
                  />
                  <input
                    type="text"
                    value={editCategory}
                    onChange={(e) => setEditCategory(e.target.value)}
                    className="w-full text-[10px] bg-cat-base border border-cat-surface1 rounded px-2 py-1 text-white"
                    placeholder="category"
                  />
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={saveEdit}
                      disabled={saving || !editContent.trim()}
                      className="text-[10px] px-2 py-1 rounded bg-emerald-700/50 text-white disabled:opacity-50"
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={cancelEdit}
                      className="text-[10px] px-2 py-1 rounded border border-cat-surface1 text-cat-subtext"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <>
                  <p className="text-[11px] text-cat-subtext whitespace-pre-wrap font-mono">{entry.content}</p>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => startEdit(entry)}
                      className="text-[10px] text-indigo-400 hover:text-indigo-300"
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => handleDelete(entry)}
                      className="text-[10px] text-rose-400 hover:text-rose-300"
                    >
                      Delete
                    </button>
                  </div>
                </>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
