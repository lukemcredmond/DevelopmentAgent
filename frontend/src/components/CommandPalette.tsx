import { useEffect, useMemo, useRef, useState } from 'react'
import type { Board, BoardLane, Task } from '../types'
import { formatTaskText } from '../utils/taskFormat'

export interface CommandPaletteItem {
  id: string
  label: string
  hint?: string
  group: string
  run: () => void
}

interface CommandPaletteProps {
  open: boolean
  onClose: () => void
  items: CommandPaletteItem[]
}

function fuzzyMatch(query: string, label: string): boolean {
  const q = query.trim().toLowerCase()
  if (!q) return true
  const hay = label.toLowerCase()
  if (hay.includes(q)) return true
  // subsequence match (c u r s o r style light)
  let qi = 0
  for (let i = 0; i < hay.length && qi < q.length; i++) {
    if (hay[i] === q[qi]) qi++
  }
  return qi === q.length
}

export function collectBoardTasks(board: Board, lanes: BoardLane[]): Task[] {
  const out: Task[] = []
  for (const lane of lanes) {
    for (const t of board[lane] ?? []) {
      out.push(t)
    }
  }
  return out
}

export default function CommandPalette({ open, onClose, items }: CommandPaletteProps) {
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)

  const filtered = useMemo(() => {
    return items.filter((item) => fuzzyMatch(query, `${item.label} ${item.hint ?? ''} ${item.group}`))
  }, [items, query])

  useEffect(() => {
    if (!open) return
    setQuery('')
    setActiveIndex(0)
    const t = window.setTimeout(() => inputRef.current?.focus(), 0)
    return () => window.clearTimeout(t)
  }, [open])

  useEffect(() => {
    setActiveIndex(0)
  }, [query])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      } else if (e.key === 'ArrowDown') {
        e.preventDefault()
        setActiveIndex((i) => Math.min(i + 1, Math.max(0, filtered.length - 1)))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setActiveIndex((i) => Math.max(0, i - 1))
      } else if (e.key === 'Enter') {
        e.preventDefault()
        const item = filtered[activeIndex]
        if (item) {
          item.run()
          onClose()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, filtered, activeIndex, onClose])

  if (!open) return null

  return (
    <div
      className="fixed inset-0 z-[80] flex items-start justify-center pt-[12vh] bg-black/50"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="w-full max-w-lg mx-3 rounded-xl border border-cat-surface1 bg-cat-mantle shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-label="Command palette"
      >
        <div className="flex items-center gap-2 px-3 py-2 border-b border-cat-surface1">
          <i className="fa-solid fa-magnifying-glass text-cat-overlay text-xs" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Type a command or card id…"
            className="flex-1 bg-transparent text-sm text-white placeholder:text-cat-overlay focus:outline-none py-1"
          />
          <kbd className="text-[9px] text-cat-overlay border border-cat-surface1 px-1.5 py-0.5 rounded">
            Esc
          </kbd>
        </div>
        <ul className="max-h-[min(50vh,360px)] overflow-y-auto py-1">
          {filtered.length === 0 && (
            <li className="px-3 py-4 text-xs text-cat-overlay text-center">No matches</li>
          )}
          {filtered.map((item, idx) => (
            <li key={item.id}>
              <button
                type="button"
                onMouseEnter={() => setActiveIndex(idx)}
                onClick={() => {
                  item.run()
                  onClose()
                }}
                className={`w-full text-left px-3 py-2 flex items-center gap-2 ${
                  idx === activeIndex ? 'bg-indigo-950/50 text-white' : 'text-cat-subtext hover:bg-cat-surface0'
                }`}
              >
                <span className="text-[9px] uppercase tracking-wider text-cat-overlay w-16 shrink-0">
                  {item.group}
                </span>
                <span className="text-xs flex-1 min-w-0 truncate">{item.label}</span>
                {item.hint && (
                  <span className="text-[10px] font-mono text-cat-overlay truncate max-w-[40%]">
                    {item.hint}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
        <p className="px-3 py-1.5 text-[9px] text-cat-overlay border-t border-cat-surface1">
          ↑↓ navigate · Enter run · Esc close
        </p>
      </div>
    </div>
  )
}

export function cardPaletteItems(
  tasks: Task[],
  onOpen: (task: Task) => void,
): CommandPaletteItem[] {
  return tasks.map((t) => ({
    id: `card:${t.id}`,
    label: formatTaskText(t.title) || t.id,
    hint: t.id,
    group: 'Card',
    run: () => onOpen(t),
  }))
}
