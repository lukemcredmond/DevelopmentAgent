import type { AgentId, BriefCategory, Skill, SkillSuggestion } from '../types'
import { AGENT_LABELS } from '../types'
import SlideOver from './SlideOver'

interface SkillModalProps {
  agent: AgentId | null
  skills: Skill[]
  assignedSkills: string[]
  skillsDir: string
  loading: boolean
  search: string
  selectedFiles: string[]
  assigning: boolean
  briefCategories: BriefCategory[]
  suggestions: SkillSuggestion[]
  onSearchChange: (v: string) => void
  onToggleFile: (filename: string) => void
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
  selectedFiles,
  assigning,
  briefCategories,
  suggestions,
  onSearchChange,
  onToggleFile,
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
  const selectedSet = new Set(selectedFiles)
  const suggestedNotAssigned = suggestions.filter(
    (s) => !assignedSkills.includes(s.filename),
  )

  return (
    <SlideOver
      open
      onClose={onClose}
      side="right"
      title={
        <span className="flex flex-col min-w-0">
          <span className="flex items-center gap-2">
            <i className="fa-solid fa-graduation-cap text-indigo-400" />
            Add Skills — {AGENT_LABELS[agent]}
          </span>
          <span className="text-[10px] text-cat-subtext font-mono font-normal mt-0.5 truncate">
            Library: {skillsDir}
          </span>
        </span>
      }
      widthClass="w-full max-w-2xl"
      footer={
        <div className="flex items-center justify-between gap-2">
          <p className="text-[10px] text-cat-overlay italic">
            Select multiple skills, then assign in one batch
          </p>
          <div className="flex gap-2 shrink-0">
            <button
              type="button"
              onClick={onClose}
              className="bg-cat-base border border-cat-surface1 hover:bg-cat-surface1 text-cat-subtext py-1.5 px-3 rounded-lg text-xs"
            >
              Close
            </button>
            <button
              type="button"
              disabled={selectedFiles.length === 0 || assigning}
              onClick={onAssign}
              className="bg-indigo-600 hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed text-white font-semibold py-1.5 px-4 rounded-lg text-xs transition-colors flex items-center gap-1"
            >
              {assigning ? (
                <i className="fa-solid fa-spinner animate-spin" />
              ) : (
                <i className="fa-solid fa-check" />
              )}
              Assign ({selectedFiles.length})
            </button>
          </div>
        </div>
      }
    >
      <div className="p-4 space-y-4">
        {briefCategories.length > 0 && (
          <div className="space-y-1.5">
            <div className="text-[10px] uppercase tracking-wider text-cat-overlay">
              Detected from brief
            </div>
            <div className="flex flex-wrap gap-1.5">
              {briefCategories.map((cat) => (
                <span
                  key={cat.id}
                  className="text-[10px] px-2 py-0.5 rounded-full bg-indigo-950/50 border border-indigo-500/30 text-indigo-200"
                >
                  {cat.label}
                </span>
              ))}
            </div>
          </div>
        )}

        {!loading && suggestedNotAssigned.length > 0 && (
          <div className="space-y-1.5 border border-cat-surface1 rounded-lg p-3 bg-cat-base/50">
            <div className="text-[10px] uppercase tracking-wider text-cat-overlay">
              Suggested for {AGENT_LABELS[agent]}
            </div>
            <div className="space-y-1">
              {suggestedNotAssigned.map((s) => {
                const isSelected = selectedSet.has(s.filename)
                return (
                  <button
                    key={s.filename}
                    type="button"
                    onClick={() => onToggleFile(s.filename)}
                    className={`w-full text-left p-2 rounded-lg border text-xs flex items-center gap-2 transition-colors ${
                      isSelected
                        ? 'bg-indigo-950/40 border-indigo-500/60'
                        : 'border-cat-surface1 hover:border-indigo-500/40'
                    }`}
                  >
                    <input
                      type="checkbox"
                      readOnly
                      checked={isSelected}
                      className="shrink-0 pointer-events-none"
                    />
                    <div className="flex-1 min-w-0">
                      <div className="font-semibold text-indigo-300 truncate">{s.title}</div>
                      <div className="text-[10px] text-cat-overlay">{s.reason}</div>
                    </div>
                    <span className="text-[9px] text-cat-subtext shrink-0">{s.score} pts</span>
                  </button>
                )
              })}
            </div>
          </div>
        )}

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
          <span>{selectedFiles.length} selected</span>
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
              const isSelected = selectedSet.has(skill.filename)
              const isAssigned = assignedSkills.includes(skill.filename)
              return (
                <button
                  key={skill.filename}
                  type="button"
                  onClick={() => onToggleFile(skill.filename)}
                  className={`w-full text-left p-3 rounded-xl border transition-colors flex items-center gap-3 ${
                    isSelected
                      ? 'bg-indigo-950/40 border-indigo-500/60'
                      : 'bg-cat-base border-cat-surface1 hover:border-indigo-500/40'
                  }`}
                >
                  <input
                    type="checkbox"
                    readOnly
                    checked={isSelected}
                    className="shrink-0 pointer-events-none"
                  />
                  <div className="space-y-0.5 truncate flex-1 min-w-0">
                    <div className="font-bold text-xs text-indigo-300 truncate">
                      {skill.title}
                    </div>
                    <div className="text-[10px] text-cat-subtext font-mono truncate">
                      {skill.filename}
                    </div>
                  </div>
                  {isAssigned && (
                    <span className="text-[9px] bg-emerald-950/50 text-emerald-400 border border-emerald-500/30 px-1.5 py-0.5 rounded shrink-0">
                      Assigned
                    </span>
                  )}
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
      </div>
    </SlideOver>
  )
}
