interface ManualTaskModalProps {
  open: boolean
  title: string
  description: string
  loading: boolean
  onTitleChange: (v: string) => void
  onDescriptionChange: (v: string) => void
  onSubmit: () => void
  onClose: () => void
}

export default function ManualTaskModal({
  open,
  title,
  description,
  loading,
  onTitleChange,
  onDescriptionChange,
  onSubmit,
  onClose,
}: ManualTaskModalProps) {
  if (!open) return null

  return (
    <div className="fixed inset-0 bg-black/75 flex items-center justify-center p-4 z-50">
      <form
        onSubmit={(e) => {
          e.preventDefault()
          onSubmit()
        }}
        className="bg-cat-surface0 rounded-2xl max-w-md w-full p-6 border border-cat-surface1 space-y-4 shadow-2xl"
      >
        <div className="flex items-center justify-between">
          <h3 className="text-base font-bold text-white flex items-center gap-2">
            <i className="fa-solid fa-square-plus text-indigo-400" />
            Add Feature to Brief
          </h3>
          <button type="button" onClick={onClose} className="text-cat-subtext hover:text-white">
            <i className="fa-solid fa-xmark" />
          </button>
        </div>
        <div className="space-y-3 text-xs">
          <label className="block">
            <span className="text-[10px] text-cat-subtext block mb-1">TASK TITLE</span>
            <input
              type="text"
              required
              value={title}
              onChange={(e) => onTitleChange(e.target.value)}
              placeholder="Feature title for the Product Owner"
              className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-medium focus:outline-none focus:border-indigo-500"
            />
          </label>
          <label className="block">
            <span className="text-[10px] text-cat-subtext block mb-1">DESCRIPTION</span>
            <textarea
              required
              value={description}
              onChange={(e) => onDescriptionChange(e.target.value)}
              placeholder="What should this feature do? The PO will refine it and add to the brief."
              className="w-full h-24 bg-cat-base border border-cat-surface1 rounded p-2 text-white font-mono focus:outline-none focus:border-indigo-500 resize-none"
            />
          </label>
        </div>
        <div className="flex justify-end pt-2 gap-2">
          <button
            type="button"
            onClick={onClose}
            className="bg-cat-base border border-cat-surface1 hover:bg-cat-surface1 text-cat-subtext py-1.5 px-3 rounded-lg text-xs"
          >
            Cancel
          </button>
          <button
            type="submit"
            disabled={loading || !title || !description}
            className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white font-semibold py-1.5 px-4 rounded-lg text-xs"
          >
            {loading ? 'Sending to PO…' : 'Send to PO & Backlog'}
          </button>
        </div>
      </form>
    </div>
  )
}
