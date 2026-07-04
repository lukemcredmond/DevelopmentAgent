import { useEffect, useState } from 'react'
import { fetchFileTree } from '../api/client'
import type { FileTreeNode } from '../types'

interface FileExplorerProps {
  selectedFile: string | null
  onSelectFile: (path: string) => void
  refreshKey?: number
}

function TreeNode({
  node,
  depth,
  selectedFile,
  onSelectFile,
}: {
  node: FileTreeNode
  depth: number
  selectedFile: string | null
  onSelectFile: (path: string) => void
}) {
  const [open, setOpen] = useState(depth < 2)
  const isDir = node.type === 'directory'
  const isSelected = !isDir && selectedFile === node.path

  if (isDir) {
    return (
      <div>
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          className="w-full text-left text-[11px] font-mono py-0.5 px-1 hover:bg-cat-surface0 rounded flex items-center gap-1 text-cat-subtext"
          style={{ paddingLeft: `${depth * 12 + 4}px` }}
        >
          <i className={`fa-solid fa-chevron-${open ? 'down' : 'right'} text-[8px] w-3`} />
          <i className="fa-solid fa-folder text-amber-400/80" />
          {node.name}
        </button>
        {open &&
          node.children?.map((child) => (
            <TreeNode
              key={child.path}
              node={child}
              depth={depth + 1}
              selectedFile={selectedFile}
              onSelectFile={onSelectFile}
            />
          ))}
      </div>
    )
  }

  return (
    <button
      type="button"
      onClick={() => onSelectFile(node.path)}
      className={`w-full text-left text-[11px] font-mono py-0.5 px-1 rounded flex items-center gap-1 ${
        isSelected
          ? 'bg-indigo-950/40 text-indigo-300'
          : 'text-cat-subtext hover:bg-cat-surface0'
      }`}
      style={{ paddingLeft: `${depth * 12 + 16}px` }}
    >
      <i className="fa-regular fa-file-code text-[10px]" />
      {node.name}
    </button>
  )
}

function buildTreeFromFiles(files: Record<string, string>): FileTreeNode[] {
  const root: FileTreeNode[] = []

  for (const filePath of Object.keys(files).sort()) {
    const parts = filePath.split('/')
    let current = root

    for (let i = 0; i < parts.length; i++) {
      const name = parts[i]!
      const path = parts.slice(0, i + 1).join('/')
      const isFile = i === parts.length - 1

      let existing = current.find((n) => n.name === name)
      if (!existing) {
        existing = {
          name,
          path,
          type: isFile ? 'file' : 'directory',
          children: isFile ? undefined : [],
        }
        current.push(existing)
      }

      if (!isFile && existing.children) {
        current = existing.children
      }
    }
  }

  return root
}

export default function FileExplorer({
  selectedFile,
  onSelectFile,
  refreshKey = 0,
}: FileExplorerProps) {
  const [tree, setTree] = useState<FileTreeNode[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)

    fetchFileTree()
      .then((data) => {
        if (!cancelled) {
          setTree(data)
          setError(null)
        }
      })
      .catch(() => {
        if (!cancelled) setError('Tree API unavailable')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [refreshKey])

  return (
    <div className="flex flex-col h-full border-r border-cat-surface1 bg-cat-base overflow-hidden">
      <div className="px-3 py-2 border-b border-cat-surface1 shrink-0">
        <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
          Files
        </h3>
      </div>
      <div className="flex-1 overflow-y-auto p-1">
        {loading && (
          <p className="text-[11px] text-cat-overlay p-2">
            <i className="fa-solid fa-spinner animate-spin mr-1" />
            Loading tree…
          </p>
        )}
        {error && (
          <p className="text-[10px] text-cat-overlay p-2 italic">{error}</p>
        )}
        {!loading &&
          tree.map((node) => (
            <TreeNode
              key={node.path}
              node={node}
              depth={0}
              selectedFile={selectedFile}
              onSelectFile={onSelectFile}
            />
          ))}
      </div>
    </div>
  )
}

export { buildTreeFromFiles }
