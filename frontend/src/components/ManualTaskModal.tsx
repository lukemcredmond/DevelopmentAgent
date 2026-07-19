import SlideOver from './SlideOver'

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
  return (
    <SlideOver
      open={open}
      onClose={onClose}
      side="right"
      title={
        <span className="flex items-center gap-2">
          <i className="fa-solid fa-square-plus text-indigo-400" />
          Add Feature to Brief
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
            disabled={loading || !title || !description}
            onClick={onSubmit}
            className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white font-semibold py-1.5 px-4 rounded-lg text-xs"
          >
            {loading ? 'Sending to PO…' : 'Send to PO & Backlog'}
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
      </form>
    </SlideOver>
  )
}
