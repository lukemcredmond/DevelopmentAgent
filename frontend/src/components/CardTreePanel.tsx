import { memo, useMemo, useState } from 'react'
import type { Board, Task } from '../types'
import {
  buildDependencyGraph,
  buildHierarchyForest,
  stallChips,
  type CardTreeNode,
  type CardTreeTab,
} from '../utils/cardTree'

interface CardTreePanelProps {
  board: Board
  onTaskClick: (task: Task) => void
}

function TreeNodeRow({
  node,
  depth,
  onTaskClick,
}: {
  node: CardTreeNode
  depth: number
  onTaskClick: (task: Task) => void
}) {
  const [open, setOpen] = useState(depth < 2)
  const chips = stallChips(node.task)
  const hasChildren = node.children.length > 0

  return (
    <div className="select-none">
      <div
        className="flex items-center gap-2 py-1 px-2 rounded hover:bg-cat-surface0/80 cursor-pointer"
        style={{ paddingLeft: `${8 + depth * 14}px` }}
      >
        {hasChildren ? (
          <button
            type="button"
            className="text-cat-overlay w-4 shrink-0"
            onClick={(e) => {
              e.stopPropagation()
              setOpen((o) => !o)
            }}
            aria-label={open ? 'Collapse' : 'Expand'}
          >
            {open ? '▾' : '▸'}
          </button>
        ) : (
          <span className="w-4 shrink-0" />
        )}
        <button
          type="button"
          className="flex flex-wrap items-center gap-2 min-w-0 text-left flex-1"
          onClick={() => onTaskClick(node.task)}
        >
          <span className="text-[10px] font-mono text-cat-overlay truncate max-w-[28%]">
            {node.task.id}
          </span>
          <span className="text-[11px] text-cat-text truncate flex-1">{node.task.title}</span>
          <span className="text-[9px] uppercase tracking-wide text-indigo-300/90 shrink-0">
            {node.lane}
          </span>
          {chips.map((c) => (
            <span
              key={c}
              className="text-[9px] px-1.5 py-0.5 rounded bg-amber-900/30 text-amber-200 shrink-0"
            >
              {c}
            </span>
          ))}
        </button>
      </div>
      {open &&
        node.children.map((child) => (
          <TreeNodeRow
            key={child.task.id}
            node={child}
            depth={depth + 1}
            onTaskClick={onTaskClick}
          />
        ))}
    </div>
  )
}

function ForestSection({
  title,
  nodes,
  onTaskClick,
}: {
  title: string
  nodes: CardTreeNode[]
  onTaskClick: (task: Task) => void
}) {
  if (nodes.length === 0) return null
  return (
    <div className="mb-4">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-cat-overlay px-2 mb-1">
        {title}
      </div>
      {nodes.map((n) => (
        <TreeNodeRow key={n.task.id} node={n} depth={0} onTaskClick={onTaskClick} />
      ))}
    </div>
  )
}

export default memo(function CardTreePanel({ board, onTaskClick }: CardTreePanelProps) {
  const [tab, setTab] = useState<CardTreeTab>('hierarchy')

  const hierarchy = useMemo(() => buildHierarchyForest(board), [board])
  const dependencies = useMemo(() => buildDependencyGraph(board), [board])

  return (
    <div className="flex flex-col flex-1 min-h-0 bg-cat-base/40 border-t border-cat-surface1">
      <div className="shrink-0 flex items-center gap-2 px-4 py-2 border-b border-cat-surface1">
        <span className="text-[10px] uppercase text-cat-overlay mr-2">Tree</span>
        {(['hierarchy', 'dependencies'] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`text-[10px] px-2.5 py-1 rounded font-semibold capitalize ${
              tab === t
                ? 'bg-indigo-600/50 text-indigo-100'
                : 'text-cat-subtext hover:text-white'
            }`}
          >
            {t}
          </button>
        ))}
        {tab === 'dependencies' && dependencies.missingBlockers.length > 0 && (
          <span className="text-[10px] text-amber-300 ml-auto">
            Missing blockers: {dependencies.missingBlockers.join(', ')}
          </span>
        )}
      </div>
      <div className="flex-1 overflow-y-auto py-2" data-testid="card-tree-panel">
        {tab === 'hierarchy' ? (
          <>
            <ForestSection title="Features" nodes={hierarchy.roots} onTaskClick={onTaskClick} />
            <ForestSection title="Unlinked" nodes={hierarchy.unlinked} onTaskClick={onTaskClick} />
            {hierarchy.roots.length === 0 && hierarchy.unlinked.length === 0 && (
              <p className="text-[11px] text-cat-overlay italic px-4">No tasks on board.</p>
            )}
          </>
        ) : (
          <>
            <ForestSection
              title="Dependency roots"
              nodes={dependencies.roots}
              onTaskClick={onTaskClick}
            />
            <ForestSection
              title="Blocked lane"
              nodes={dependencies.blockedLane}
              onTaskClick={onTaskClick}
            />
            {dependencies.edges.length > 0 && (
              <div className="px-4 mt-2 text-[10px] text-cat-subtext font-mono space-y-0.5">
                <div className="text-cat-overlay uppercase mb-1">Edges ({dependencies.edges.length})</div>
                {dependencies.edges.slice(0, 40).map((e, i) => (
                  <div key={`${e.fromId}-${e.toId}-${i}`}>
                    {e.fromId} → {e.toId}
                    {e.outcomeStatus ? ` (${e.outcomeStatus})` : ''}
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
})
