import Editor from '@monaco-editor/react'
import { useCallback, useEffect, useState } from 'react'
import { saveFile } from '../api/client'

interface OpenTab {
  path: string
  content: string
  original: string
}

interface CodeEditorProps {
  files: Record<string, string>
  selectedFile: string | null
  onSelectFile: (path: string) => void
  onFilesChange: (files: Record<string, string>) => void
  showDiff?: boolean
  onToggleDiff?: () => void
  onCloseWorkspace?: () => void
}

function detectLanguage(path: string): string {
  const ext = path.split('.').pop()?.toLowerCase()
  const map: Record<string, string> = {
    ts: 'typescript',
    tsx: 'typescript',
    js: 'javascript',
    jsx: 'javascript',
    json: 'json',
    py: 'python',
    md: 'markdown',
    css: 'css',
    html: 'html',
    yml: 'yaml',
    yaml: 'yaml',
  }
  return map[ext ?? ''] ?? 'plaintext'
}

export default function CodeEditor({
  files,
  selectedFile,
  onSelectFile,
  onFilesChange,
  showDiff,
  onToggleDiff,
  onCloseWorkspace,
}: CodeEditorProps) {
  const [tabs, setTabs] = useState<OpenTab[]>([])
  const [activePath, setActivePath] = useState<string | null>(selectedFile)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (selectedFile && files[selectedFile] !== undefined) {
      setTabs((prev) => {
        const existing = prev.find((t) => t.path === selectedFile)
        if (existing) {
          if (existing.original !== files[selectedFile]) {
            return prev.map((t) =>
              t.path === selectedFile
                ? { ...t, content: files[selectedFile]!, original: files[selectedFile]! }
                : t,
            )
          }
          return prev
        }
        return [
          ...prev,
          {
            path: selectedFile,
            content: files[selectedFile]!,
            original: files[selectedFile]!,
          },
        ]
      })
      setActivePath(selectedFile)
    }
  }, [selectedFile, files])

  const activeTab = tabs.find((t) => t.path === activePath)
  const isDirty = activeTab ? activeTab.content !== activeTab.original : false

  const handleSave = useCallback(async () => {
    if (!activeTab || !isDirty) return
    setSaving(true)
    try {
      await saveFile(activeTab.path, activeTab.content)
      setTabs((prev) =>
        prev.map((t) =>
          t.path === activeTab.path
            ? { ...t, original: t.content }
            : t,
        ),
      )
      onFilesChange({ ...files, [activeTab.path]: activeTab.content })
    } catch {
      onFilesChange({ ...files, [activeTab.path]: activeTab.content })
      setTabs((prev) =>
        prev.map((t) =>
          t.path === activeTab.path ? { ...t, original: t.content } : t,
        ),
      )
    } finally {
      setSaving(false)
    }
  }, [activeTab, isDirty, files, onFilesChange])

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key === 's') {
        e.preventDefault()
        void handleSave()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [handleSave])

  const closeTab = (path: string) => {
    setTabs((prev) => {
      const remaining = prev.filter((t) => t.path !== path)
      if (remaining.length === 0) {
        setActivePath(null)
        onCloseWorkspace?.()
      } else if (activePath === path) {
        const next = remaining[remaining.length - 1]?.path ?? null
        setActivePath(next)
        if (next) onSelectFile(next)
      }
      return remaining
    })
  }

  const fileNames = Object.keys(files)

  return (
    <div className="flex flex-col h-full overflow-hidden bg-cat-base">
      <div className="flex bg-cat-surface0/40 border-b border-cat-surface1 overflow-x-auto text-xs shrink-0">
        {(tabs.length > 0 ? tabs : fileNames.map((p) => ({ path: p }))).map((tab) => {
          const path = tab.path
          const dirty =
            'content' in tab && 'original' in tab
              ? tab.content !== tab.original
              : false
          const active = activePath === path
          return (
            <div
              key={path}
              className={`flex items-center border-r border-cat-surface1 ${
                active ? 'bg-cat-base text-indigo-400 font-medium' : 'text-sky-400 hover:bg-cat-mantle'
              }`}
            >
              <button
                type="button"
                onClick={() => {
                  setActivePath(path)
                  onSelectFile(path)
                  if (!tabs.find((t) => t.path === path) && files[path] !== undefined) {
                    setTabs((prev) => [
                      ...prev,
                      { path, content: files[path]!, original: files[path]! },
                    ])
                  }
                }}
                className="px-3 py-2 whitespace-nowrap"
              >
                <i className="fa-regular fa-file-code mr-1" />
                {path}
                {dirty && <span className="text-amber-400 ml-1">●</span>}
              </button>
              {tabs.some((t) => t.path === path) && (
                <button
                  type="button"
                  onClick={() => closeTab(path)}
                  className="px-1 text-cat-overlay hover:text-white"
                >
                  ×
                </button>
              )}
            </div>
          )
        })}
        {onToggleDiff && (
          <button
            type="button"
            onClick={onToggleDiff}
            className={`ml-auto px-3 py-2 text-[10px] uppercase ${
              showDiff ? 'text-indigo-400' : 'text-cat-subtext'
            }`}
          >
            Diff
          </button>
        )}
        {isDirty && (
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={saving}
            className="px-3 py-2 text-[10px] text-emerald-400 hover:text-emerald-300"
          >
            {saving ? 'Saving…' : 'Ctrl+S Save'}
          </button>
        )}
      </div>
      <div className="flex-1 min-h-0">
        {activeTab ? (
          <Editor
            height="100%"
            language={detectLanguage(activeTab.path)}
            theme="vs-dark"
            value={activeTab.content}
            onChange={(value) => {
              const content = value ?? ''
              setTabs((prev) =>
                prev.map((t) => (t.path === activeTab.path ? { ...t, content } : t)),
              )
            }}
            options={{
              minimap: { enabled: false },
              fontSize: 12,
              wordWrap: 'on',
              scrollBeyondLastLine: false,
            }}
          />
        ) : (
          <div className="flex items-center justify-center h-full text-cat-overlay text-xs">
            Select a file to edit
          </div>
        )}
      </div>
    </div>
  )
}
