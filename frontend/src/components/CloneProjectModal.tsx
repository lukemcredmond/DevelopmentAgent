import { useEffect, useState } from 'react'
import SlideOver from './SlideOver'

export interface CloneProjectFormState {
  name: string
  workspaceDir: string
  poModel: string
  devModel: string
  crModel: string
  qaModel: string
  poBackupModel: string
  devBackupModel: string
  crBackupModel: string
  qaBackupModel: string
}

interface CloneProjectModalProps {
  open: boolean
  loading: boolean
  defaultName: string
  models: Pick<
    CloneProjectFormState,
    | 'poModel'
    | 'devModel'
    | 'crModel'
    | 'qaModel'
    | 'poBackupModel'
    | 'devBackupModel'
    | 'crBackupModel'
    | 'qaBackupModel'
  >
  onSubmit: (form: CloneProjectFormState) => void
  onClose: () => void
}

export default function CloneProjectModal({
  open,
  loading,
  defaultName,
  models,
  onSubmit,
  onClose,
}: CloneProjectModalProps) {
  const [name, setName] = useState(defaultName)
  const [workspaceDir, setWorkspaceDir] = useState('')
  const [poModel, setPoModel] = useState(models.poModel)
  const [devModel, setDevModel] = useState(models.devModel)
  const [crModel, setCrModel] = useState(models.crModel)
  const [qaModel, setQaModel] = useState(models.qaModel)
  const [poBackupModel, setPoBackupModel] = useState(models.poBackupModel)
  const [devBackupModel, setDevBackupModel] = useState(models.devBackupModel)
  const [crBackupModel, setCrBackupModel] = useState(models.crBackupModel)
  const [qaBackupModel, setQaBackupModel] = useState(models.qaBackupModel)
  const [showBackups, setShowBackups] = useState(false)

  useEffect(() => {
    if (!open) return
    setName(defaultName)
    setWorkspaceDir('')
    setPoModel(models.poModel)
    setDevModel(models.devModel)
    setCrModel(models.crModel)
    setQaModel(models.qaModel)
    setPoBackupModel(models.poBackupModel)
    setDevBackupModel(models.devBackupModel)
    setCrBackupModel(models.crBackupModel)
    setQaBackupModel(models.qaBackupModel)
  }, [open, defaultName, models])

  const canSubmit = Boolean(name.trim() && workspaceDir.trim())

  return (
    <SlideOver
      open={open}
      onClose={onClose}
      side="right"
      title={
        <span className="flex items-center gap-2">
          <i className="fa-solid fa-clone text-violet-400" />
          Clone for model test
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
            disabled={loading || !canSubmit}
            onClick={() =>
              onSubmit({
                name: name.trim(),
                workspaceDir: workspaceDir.trim(),
                poModel,
                devModel,
                crModel,
                qaModel,
                poBackupModel,
                devBackupModel,
                crBackupModel,
                qaBackupModel,
              })
            }
            className="bg-violet-600 hover:bg-violet-500 disabled:opacity-50 text-white font-semibold py-1.5 px-4 rounded-lg text-xs"
          >
            {loading ? 'Cloning…' : 'Clone project'}
          </button>
        </div>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (canSubmit) {
            onSubmit({
              name: name.trim(),
              workspaceDir: workspaceDir.trim(),
              poModel,
              devModel,
              crModel,
              qaModel,
              poBackupModel,
              devBackupModel,
              crBackupModel,
              qaBackupModel,
            })
          }
        }}
        className="p-4 space-y-3 text-xs"
      >
        <p className="text-cat-subtext text-[11px] leading-relaxed">
          Copies brief, workflow settings, and agent skills. Board and plan start empty—run Plan
          outline and backlog after adjusting models.
        </p>
        <label className="block">
          <span className="text-[10px] text-cat-subtext block mb-1">PROJECT NAME</span>
          <input
            type="text"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-medium focus:outline-none focus:border-violet-500"
          />
        </label>
        <label className="block">
          <span className="text-[10px] text-cat-subtext block mb-1">WORKSPACE DIRECTORY</span>
          <input
            type="text"
            required
            value={workspaceDir}
            onChange={(e) => setWorkspaceDir(e.target.value)}
            placeholder="Required — separate folder from source"
            className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-mono focus:outline-none focus:border-violet-500"
          />
        </label>
        <div className="grid grid-cols-2 gap-2">
          <label className="block">
            <span className="text-[10px] text-cat-subtext block mb-1">PO MODEL</span>
            <input
              type="text"
              value={poModel}
              onChange={(e) => setPoModel(e.target.value)}
              className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-mono text-[11px] focus:outline-none focus:border-violet-500"
            />
          </label>
          <label className="block">
            <span className="text-[10px] text-cat-subtext block mb-1">DEV MODEL</span>
            <input
              type="text"
              value={devModel}
              onChange={(e) => setDevModel(e.target.value)}
              className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-mono text-[11px] focus:outline-none focus:border-violet-500"
            />
          </label>
          <label className="block">
            <span className="text-[10px] text-cat-subtext block mb-1">REVIEWER MODEL</span>
            <input
              type="text"
              value={crModel}
              onChange={(e) => setCrModel(e.target.value)}
              className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-mono text-[11px] focus:outline-none focus:border-violet-500"
            />
          </label>
          <label className="block">
            <span className="text-[10px] text-cat-subtext block mb-1">QA MODEL</span>
            <input
              type="text"
              value={qaModel}
              onChange={(e) => setQaModel(e.target.value)}
              className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-mono text-[11px] focus:outline-none focus:border-violet-500"
            />
          </label>
        </div>
        <button
          type="button"
          className="text-[10px] text-violet-300 hover:text-violet-200"
          onClick={() => setShowBackups((v) => !v)}
        >
          {showBackups ? 'Hide backup models' : 'Backup models (optional)'}
        </button>
        {showBackups && (
          <div className="grid grid-cols-2 gap-2">
            {(
              [
                ['PO backup', poBackupModel, setPoBackupModel],
                ['Dev backup', devBackupModel, setDevBackupModel],
                ['Reviewer backup', crBackupModel, setCrBackupModel],
                ['QA backup', qaBackupModel, setQaBackupModel],
              ] as const
            ).map(([label, val, setVal]) => (
              <label key={label} className="block">
                <span className="text-[10px] text-cat-subtext block mb-1">{label.toUpperCase()}</span>
                <input
                  type="text"
                  value={val}
                  onChange={(e) => setVal(e.target.value)}
                  className="w-full bg-cat-base border border-cat-surface1 rounded p-2 text-white font-mono text-[11px] focus:outline-none focus:border-violet-500"
                />
              </label>
            ))}
          </div>
        )}
      </form>
    </SlideOver>
  )
}
