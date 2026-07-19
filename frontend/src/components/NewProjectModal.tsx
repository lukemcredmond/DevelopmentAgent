import SlideOver from './SlideOver'

interface NewProjectModalProps {
  open: boolean
  name: string
  dir: string
  loading: boolean
  onNameChange: (v: string) => void
  onDirChange: (v: string) => void
  onSubmit: () => void
  onClose: () => void
}

export default function NewProjectModal({
  open,
  name,
  dir,
  loading,
  onNameChange,
  onDirChange,
  onSubmit,
  onClose,
}: NewProjectModalProps) {
  return (
    <SlideOver
      open={open}
      onClose={onClose}
      side="right"
      title={
        <span className="flex items-center gap-2">
          <i className="fa-solid fa-folder-plus text-indigo-400" />
          Create New Workspace
        </span>
      }
      widthClass="w-full max-w-md"
      footer={
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="bg-cat-base border border-cat-surface1 hover:bg-cat-surface1 text-cat-subtext py-1.5 px-3 rounded-lg text-xs"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={loading || !name || !dir}
            onClick={onSubmit}
            className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white font-semibold py-1.5 px-4 rounded-lg text-xs"
          >
            {loading ? 'Creating…' : 'Create Workspace'}
          </button>
        </div>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          onSubmit()
        }}
        className="p-4 space-y-3 text-xs"
      >
        <label className="block">
          <span className="text-[10px] text-cat-subtext block mb-1">PROJECT NAME</span>
          <input
            type="text"
            required
            value={name}
            onChange={(e) => onNameChange(e.target.value)}
            placeholder="My Auth Microservice"
            className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-medium focus:outline-none focus:border-indigo-500"
          />
        </label>
        <label className="block">
          <span className="text-[10px] text-cat-subtext block mb-1">WORKSPACE DIRECTORY</span>
          <input
            type="text"
            required
            value={dir}
            onChange={(e) => onDirChange(e.target.value)}
            placeholder="./workspace_auth"
            className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-mono focus:outline-none focus:border-indigo-500"
          />
        </label>
      </form>
    </SlideOver>
  )
}
