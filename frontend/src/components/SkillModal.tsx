import type { AgentId, Skill } from '../types'
import { AGENT_LABELS } from '../types'

interface SkillModalProps {
  agent: AgentId | null
  skills: Skill[]
  assignedSkills: string[]
  skillsDir: string
  loading: boolean
  search: string
  selectedFile: string | null
  assigning: boolean
  onSearchChange: (v: string) => void
  onSelectFile: (filename: string | null) => void
  onAssign: () => void
  onClose: () => void
}

export default function SkillModal({
  agent,
  skills,
  assignedSkills,
  skillsDir,
  loading,
  search,
  selectedFile,
  assigning,
  onSearchChange,
  onSelectFile,
  onAssign,
  onClose,
}: SkillModalProps) {
  if (!agent) return null

  const q = search.toLowerCase().trim()
  const filtered = skills.filter(
    (s) =>
      !q ||
      s.title.toLowerCase().includes(q) ||
      s.filename.toLowerCase().includes(q) ||
      s.folder.toLowerCase().includes(q),
  )

  return (
    <div className="fixed inset-0 bg-black/75 flex items-center justify-center p-4 z-50">
      <div className="bg-cat-surface0 rounded-2xl max-w-2xl w-full p-6 border border-cat-surface1 space-y-4 shadow-2xl flex flex-col max-h-[85vh]">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-base font-bold text-white flex items-center gap-2">
              <i className="fa-solid fa-graduation-cap text-indigo-400" />
              Add Skill — {AGENT_LABELS[agent]}
            </h3>
            <p className="text-[10px] text-cat-subtext font-mono mt-1">
              Library: {skillsDir}
            </p>
          </div>
          <button type="button" onClick={onClose} className="text-cat-subtext hover:text-white">
            <i className="fa-solid fa-xmark" />
          </button>
        </div>

        <div className="relative">
          <input
            type="text"
            autoFocus
            placeholder="Filter by name, path, or folder..."
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
            className="w-full bg-cat-base border border-cat-surface1 rounded-lg p-2.5 pl-9 text-xs text-white focus:outline-none focus:border-indigo-500 font-mono"
          />
          <i className="fa-solid fa-magnifying-glass absolute left-3 top-3.5 text-xs text-slate-500" />
        </div>

        <div className="text-[10px] text-cat-subtext flex items-center justify-between px-1">
          <span>{skills.length} skill(s) in directory</span>
          <span>{filtered.length} shown</span>
        </div>

        <div className="space-y-1.5 overflow-y-auto flex-1 min-h-[200px] pr-1">
          {loading && (
            <div className="text-center py-12 text-xs text-cat-subtext">
              <i className="fa-solid fa-spinner animate-spin mr-2" />
              Scanning skills directory...
            </div>
          )}
          {!loading &&
            filtered.map((skill) => {
              const isSelected = selectedFile === skill.filename
              const isAssigned = assignedSkills.includes(skill.filename)
              return (
                <button
                  key={skill.filename}
                  type="button"
                  onClick={() => onSelectFile(skill.filename)}
                  onDoubleClick={onAssign}
                  className={`w-full text-left p-3 rounded-xl border transition-colors flex items-center justify-between ${
                    isSelected
                      ? 'bg-indigo-950/40 border-indigo-500/60'
                      : 'bg-cat-base border-cat-surface1 hover:border-indigo-500/40'
                  }`}
                >
                  <div className="space-y-0.5 truncate pr-2 min-w-0">
                    <div className="font-bold text-xs text-indigo-300 truncate">
                      {skill.title}
                    </div>
                    <div className="text-[10px] text-cat-subtext font-mono truncate">
                      {skill.filename}
                    </div>
                    {skill.folder && skill.folder !== '.' && (
                      <div className="text-[9px] text-cat-overlay font-mono truncate">
                        {skill.folder}
                      </div>
                    )}
                  </div>
                  <div className="flex flex-col items-end gap-1 shrink-0">
                    {isAssigned && (
                      <span className="text-[9px] bg-emerald-950/50 text-emerald-400 border border-emerald-500/30 px-1.5 py-0.5 rounded">
                        Assigned
                      </span>
                    )}
                    {isSelected && (
                      <span className="text-[9px] text-indigo-400">
                        <i className="fa-solid fa-check mr-1" />
                        Selected
                      </span>
                    )}
                  </div>
                </button>
              )
            })}
          {!loading && filtered.length === 0 && (
            <div className="text-center py-8 text-xs text-cat-overlay italic">
              {skills.length === 0
                ? `No .md or .txt skills found in ${skillsDir}`
                : 'No skills match your filter'}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between pt-2 border-t border-cat-surface1">
          <p className="text-[10px] text-cat-overlay italic">
            {selectedFile ? `Selected: ${selectedFile}` : 'Click a skill to select, then assign'}
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="bg-cat-base border border-cat-surface1 hover:bg-cat-surface1 text-cat-subtext py-1.5 px-3 rounded-lg text-xs"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={!selectedFile || assigning}
              onClick={onAssign}
              className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold py-1.5 px-4 rounded-lg text-xs transition-colors flex items-center gap-1"
            >
              <i className="fa-solid fa-copy text-[10px]" />
              Copy to Workspace & Assign
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
