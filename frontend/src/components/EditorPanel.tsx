import FileExplorer from './FileExplorer'
import CodeEditor from './CodeEditor'
import DiffPanel from './DiffPanel'

interface EditorPanelProps {
  hidden?: boolean
  selectedFile: string | null
  localFiles: Record<string, string>
  fileTreeKey: number
  showDiff: boolean
  onSelectFile: (path: string) => void
  onFilesChange: (files: Record<string, string>) => void
  onToggleDiff: () => void
  onCloseWorkspace: () => void
}

export default function EditorPanel({
  hidden = false,
  selectedFile,
  localFiles,
  fileTreeKey,
  showDiff,
  onSelectFile,
  onFilesChange,
  onToggleDiff,
  onCloseWorkspace,
}: EditorPanelProps) {
  if (hidden) return null

  return (
    <div className="absolute inset-0 grid grid-cols-1 lg:grid-cols-[200px_1fr] min-h-0 overflow-hidden bg-cat-base">
      <FileExplorer
        selectedFile={selectedFile}
        onSelectFile={onSelectFile}
        refreshKey={fileTreeKey}
      />
      <div className="min-h-0 flex flex-col overflow-hidden border-l border-cat-surface1">
        {showDiff ? (
          <DiffPanel path={selectedFile} currentContent={localFiles[selectedFile ?? '']} />
        ) : (
          <CodeEditor
            files={localFiles}
            selectedFile={selectedFile}
            onSelectFile={onSelectFile}
            onFilesChange={onFilesChange}
            showDiff={showDiff}
            onToggleDiff={onToggleDiff}
            onCloseWorkspace={onCloseWorkspace}
          />
        )}
      </div>
    </div>
  )
}
