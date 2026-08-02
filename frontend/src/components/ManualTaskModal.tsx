import SlideOver from './SlideOver'

interface ManualTaskModalProps {
  open: boolean
  title: string
  description: string
  acceptanceCriteria?: string
  loading: boolean
  preferredFeatureId?: string | null
  preferredFeatureTitle?: string | null
  onTitleChange: (v: string) => void
  onDescriptionChange: (v: string) => void
  onAcceptanceCriteriaChange?: (v: string) => void
  onSubmit: () => void
  onClose: () => void
}

export default function ManualTaskModal({
  open,
  title,
  description,
  acceptanceCriteria = '',
  loading,
  preferredFeatureId = null,
  preferredFeatureTitle = null,
  onTitleChange,
  onDescriptionChange,
  onAcceptanceCriteriaChange,
  onSubmit,
  onClose,
}: ManualTaskModalProps) {
  const isFollowUp = Boolean(preferredFeatureId)
  return (
    <SlideOver
      open={open}
      onClose={onClose}
      side="right"
      title={
        <span className="flex items-center gap-2">
          <i className="fa-solid fa-square-plus text-indigo-400" />
          {isFollowUp ? 'Add follow-up' : 'Add Feature to Brief'}
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
        {isFollowUp && (
          <p className="text-[10px] text-violet-300 bg-violet-950/40 border border-violet-500/30 rounded px-2 py-1.5 font-mono">
            Updating feature {preferredFeatureId}
            {preferredFeatureTitle ? ` — ${preferredFeatureTitle}` : ''}
          </p>
        )}
        <label className="block">
          <span className="text-[10px] text-cat-subtext block mb-1">TASK TITLE</span>
          <input
            type="text"
            required
            value={title}
            onChange={(e) => onTitleChange(e.target.value)}
            placeholder={
              isFollowUp
                ? 'Short title for this follow-up slice'
                : 'Feature title for the Product Owner'
            }
            className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-medium focus:outline-none focus:border-indigo-500"
          />
        </label>
        <label className="block">
          <span className="text-[10px] text-cat-subtext block mb-1">DESCRIPTION</span>
          <textarea
            required
            value={description}
            onChange={(e) => onDescriptionChange(e.target.value)}
            placeholder={
              isFollowUp
                ? 'What should change on this existing feature? PO will spawn one new backlog child.'
                : 'What should this feature do? The PO will refine it and add to the brief.'
            }
            rows={6}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white focus:outline-none focus:border-indigo-500 min-h-[120px]"
          />
        </label>
        {onAcceptanceCriteriaChange ? (
          <label className="block">
            <span className="text-[10px] text-cat-subtext block mb-1">
              ACCEPTANCE CRITERIA (optional)
            </span>
            <textarea
              value={acceptanceCriteria ?? ''}
              onChange={(e) => onAcceptanceCriteriaChange(e.target.value)}
              placeholder="One testable criterion per line — PO can refine"
              rows={4}
              className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-mono text-[11px] focus:outline-none focus:border-indigo-500"
            />
          </label>
        ) : null}
      </form>
    </SlideOver>
  )
}
