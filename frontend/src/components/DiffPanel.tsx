import { useEffect, useState } from 'react'
import ReactDiffViewer from 'react-diff-viewer-continued'
import { fetchFileDiff } from '../api/client'

interface DiffPanelProps {
  path: string | null
  currentContent?: string
}

export default function DiffPanel({ path, currentContent }: DiffPanelProps) {
  const [oldValue, setOldValue] = useState('')
  const [newValue, setNewValue] = useState('')
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!path) {
      setOldValue('')
      setNewValue('')
      return
    }

    setLoading(true)
    fetchFileDiff(path)
      .then((data) => {
        setOldValue(data.oldValue)
        setNewValue(data.newValue)
      })
      .catch(() => {
        setOldValue('')
        setNewValue(currentContent ?? '')
      })
      .finally(() => setLoading(false))
  }, [path, currentContent])

  if (!path) {
    return (
      <div className="flex items-center justify-center h-full text-cat-overlay text-xs bg-cat-base">
        Select a file to view diff
      </div>
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-cat-subtext text-xs bg-cat-base">
        <i className="fa-solid fa-spinner animate-spin mr-2" />
        Loading diff…
      </div>
    )
  }

  return (
    <div className="h-full overflow-auto bg-cat-base text-xs">
      <div className="px-3 py-2 border-b border-cat-surface1 text-cat-subtext font-mono">
        {path}
      </div>
      <ReactDiffViewer
        oldValue={oldValue}
        newValue={newValue || currentContent || ''}
        splitView
        useDarkTheme
        styles={{
          variables: {
            dark: {
              diffViewerBackground: '#11111b',
              diffViewerColor: '#cdd6f4',
              addedBackground: '#1a3d2e',
              removedBackground: '#3d1a1a',
              wordAddedBackground: '#2d5a3d',
              wordRemovedBackground: '#5a2d2d',
            },
          },
        }}
      />
    </div>
  )
}
