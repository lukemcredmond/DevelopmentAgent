import { useState } from 'react'
import type { AgentId, AppState, BriefCategory, Skill, SkillSuggestion } from '../types'
import { AGENT_LABELS } from '../types'
import {
  assignSkill,
  combineSkills,
  removeSkill,
  saveBuiltSkill,
} from '../api/client'
import SlideOver from './SlideOver'

interface SkillModalProps {
  agent: AgentId | null
  skills: Skill[]
  assignedSkills: string[]
  skillsDir: string
  ollamaUrl: string
  loading: boolean
  search: string
  selectedFiles: string[]
  assigning: boolean
  briefCategories: BriefCategory[]
  suggestions: SkillSuggestion[]
  onSearchChange: (v: string) => void
  onToggleFile: (filename: string) => void
  onAssign: () => void
  onAppState: (state: AppState) => void
  onAfterAssign?: () => void
  onSelectAllAssigned?: () => void
  onClose: () => void
}

export default function SkillModal({
  agent,
  skills,
  assignedSkills,
  skillsDir,
  ollamaUrl,
  loading,
  search,
  selectedFiles,
  assigning,
  briefCategories,
  suggestions,
  onSearchChange,
  onToggleFile,
  onAssign,
  onAppState,
  onAfterAssign,
  onSelectAllAssigned,
  onClose,
}: SkillModalProps) {
  const [view, setView] = useState<'list' | 'build'>('list')
  const [combining, setCombining] = useState(false)
  const [savingBuilt, setSavingBuilt] = useState(false)
  const [combineError, setCombineError] = useState<string | null>(null)
  const [buildMarkdown, setBuildMarkdown] = useState('')
  const [buildSkillRel, setBuildSkillRel] = useState('')
  const [buildSources, setBuildSources] = useState<string[]>([])
  const [buildCharCount, setBuildCharCount] = useState(0)
  const [buildBudget, setBuildBudget] = useState(0)
  const [buildWarning, setBuildWarning] = useState<string | null>(null)
  const [buildMergeRounds, setBuildMergeRounds] = useState<number | null>(null)
  const [outputName, setOutputName] = useState('combined-skill')
  const [removeSourcesAfterAssign, setRemoveSourcesAfterAssign] = useState(false)

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
  const libraryFilenames = new Set(skills.map((s) => s.filename))
  const suggestedNotAssigned = suggestions.filter(
    (s) => !assignedSkills.includes(s.filename),
  )

  async function startCombinePreview() {
    if (selectedFiles.length < 2 || !agent) return
    setCombining(true)
    setCombineError(null)
    try {
      const result = await combineSkills({
        agent,
        skillFiles: selectedFiles,
        outputName: outputName.trim() || undefined,
        ollamaUrl,
      })
      setBuildMarkdown(result.markdown)
      setBuildSkillRel(result.skillRel)
      setBuildSources(result.sources)
      setBuildCharCount(result.charCount)
      setBuildBudget(result.skillsContextMaxChars)
      setBuildWarning(result.warning ?? null)
      setBuildMergeRounds(result.mergeRounds ?? 1)
      setView('build')
    } catch (e) {
      setCombineError(e instanceof Error ? e.message : 'Combine failed')
    } finally {
      setCombining(false)
    }
  }

  async function saveAndAssignBuilt() {
    if (!agent || !buildSkillRel || !buildMarkdown.trim()) return
    setSavingBuilt(true)
    setCombineError(null)
    try {
      let data = await saveBuiltSkill({
        skillRel: buildSkillRel,
        markdown: buildMarkdown,
      })
      onAppState(data)
      data = await assignSkill({ agent, skillFile: buildSkillRel })
      onAppState(data)
      if (removeSourcesAfterAssign) {
        for (const src of buildSources) {
          if (assignedSkills.includes(src)) {
            data = await removeSkill({ agent, skillFile: src })
            onAppState(data)
          }
        }
      }
      onAfterAssign?.()
      setView('list')
      onClose()
    } catch (e) {
      setCombineError(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setSavingBuilt(false)
    }
  }

  const busy = assigning || combining || savingBuilt

  return (
    <SlideOver
      open
      onClose={onClose}
      side="right"
      title={
        <span className="flex flex-col min-w-0">
          <span className="flex items-center gap-2">
            <i className="fa-solid fa-graduation-cap text-indigo-400" />
            {view === 'build' ? 'Build combined skill' : `Add Skills — ${AGENT_LABELS[agent]}`}
          </span>
          <span className="text-[10px] text-cat-subtext font-mono font-normal mt-0.5 truncate">
            Library: {skillsDir}
          </span>
        </span>
      }
      widthClass="w-full max-w-2xl"
      footer={
        view === 'build' ? (
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <button
              type="button"
              disabled={busy}
              onClick={() => setView('list')}
              className="text-[10px] text-cat-subtext underline hover:text-indigo-200"
            >
              ← Back to skill list
            </button>
            <div className="flex gap-2 shrink-0 ml-auto">
              <button
                type="button"
                disabled={busy}
                onClick={onClose}
                className="bg-cat-base border border-cat-surface1 hover:bg-cat-surface1 text-cat-subtext py-1.5 px-3 rounded-lg text-xs"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={busy || !buildMarkdown.trim()}
                onClick={() => void saveAndAssignBuilt()}
                className="bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 text-white font-semibold py-1.5 px-4 rounded-lg text-xs"
              >
                {savingBuilt ? 'Saving…' : 'Save & assign'}
              </button>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <p className="text-[10px] text-cat-overlay italic">
              Select multiple skills, then assign or build a combined project skill
            </p>
            <div className="flex gap-2 shrink-0">
              {selectedFiles.length >= 2 && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void startCombinePreview()}
                  className="bg-cat-base border border-violet-500/50 hover:bg-violet-950/40 text-violet-200 font-semibold py-1.5 px-3 rounded-lg text-xs disabled:opacity-40"
                >
                  {combining
                    ? selectedFiles.length > 5
                      ? 'Merging… (may take a while for many skills)'
                      : 'Merging…'
                    : 'Build combined skill…'}
                </button>
              )}
              <button
                type="button"
                onClick={onClose}
                className="bg-cat-base border border-cat-surface1 hover:bg-cat-surface1 text-cat-subtext py-1.5 px-3 rounded-lg text-xs"
              >
                Close
              </button>
              <button
                type="button"
                disabled={selectedFiles.length === 0 || busy}
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
        )
      }
    >
      <div className="p-4 space-y-4">
        {combineError && (
          <p className="text-xs text-rose-300 border border-rose-500/40 bg-rose-950/30 rounded px-2 py-1.5">
            {combineError}
          </p>
        )}

        {view === 'build' ? (
          <div className="space-y-3">
            <p className="text-[11px] text-cat-subtext">
              Review and edit the merged skill. It will be saved under{' '}
              <span className="font-mono text-indigo-200">workspace/skills/{buildSkillRel}</span>.
            </p>
            <p className="text-[10px] text-cat-subtext">
              Sources: {buildSources.join(', ') || '—'}
            </p>
            {buildMergeRounds != null && buildMergeRounds > 1 && (
              <p className="text-[10px] text-violet-200/90">
                Combined in {buildMergeRounds} merge rounds (up to 5 sources per LLM call).
              </p>
            )}
            <p className="text-[10px] font-mono text-cat-subtext">
              Size {buildCharCount} chars · budget ~{buildBudget} chars
              {buildWarning ? (
                <span className="text-amber-300 block mt-1">{buildWarning}</span>
              ) : null}
            </p>
            <label className="flex items-center gap-2 text-[10px] text-cat-subtext cursor-pointer">
              <input
                type="checkbox"
                checked={removeSourcesAfterAssign}
                onChange={(e) => setRemoveSourcesAfterAssign(e.target.checked)}
              />
              Remove source skills from this agent after assign
            </label>
            <textarea
              value={buildMarkdown}
              onChange={(e) => {
                setBuildMarkdown(e.target.value)
                setBuildCharCount(e.target.value.length)
              }}
              className="w-full min-h-[320px] bg-cat-base border border-cat-surface1 rounded-lg p-3 text-xs font-mono text-cat-text focus:outline-none focus:border-indigo-500"
              spellCheck={false}
            />
          </div>
        ) : (
          <>
            {selectedFiles.length > 15 && (
              <p className="text-xs text-amber-300/90 border border-amber-500/30 bg-amber-950/25 rounded px-2 py-1.5">
                Large merge: {selectedFiles.length} skills selected. This may take several minutes
                and multiple LLM rounds (5 skills per round).
              </p>
            )}
            {selectedFiles.length >= 2 && (
              <div className="flex items-center gap-2 text-[10px]">
                <label className="text-cat-subtext shrink-0">Output name</label>
                <input
                  type="text"
                  value={outputName}
                  onChange={(e) => setOutputName(e.target.value)}
                  className="flex-1 bg-cat-base border border-cat-surface1 rounded px-2 py-1 font-mono text-xs"
                  placeholder="combined-skill"
                />
              </div>
            )}

            {assignedSkills.length > 0 && (
              <div className="space-y-1.5 border border-emerald-500/25 rounded-lg p-3 bg-emerald-950/20">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[10px] uppercase tracking-wider text-emerald-200/90">
                    Already assigned to {AGENT_LABELS[agent]}
                  </div>
                  {assignedSkills.length >= 2 && onSelectAllAssigned && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={onSelectAllAssigned}
                      className="text-[10px] font-semibold text-emerald-300 hover:text-emerald-200 underline"
                    >
                      Select all assigned
                    </button>
                  )}
                </div>
                <div className="space-y-1">
                  {assignedSkills.map((filename) => {
                    const isSelected = selectedSet.has(filename)
                    const projectOnly = !libraryFilenames.has(filename)
                    const title =
                      filename.split('/').pop()?.replace(/\.md$/i, '').replace(/_/g, ' ') ?? filename
                    return (
                      <button
                        key={filename}
                        type="button"
                        onClick={() => onToggleFile(filename)}
                        className={`w-full text-left p-2 rounded-lg border text-xs flex items-center gap-2 transition-colors ${
                          isSelected
                            ? 'bg-indigo-950/40 border-indigo-500/60'
                            : 'border-cat-surface1 hover:border-emerald-500/40'
                        }`}
                      >
                        <input
                          type="checkbox"
                          readOnly
                          checked={isSelected}
                          className="shrink-0 pointer-events-none"
                        />
                        <div className="flex-1 min-w-0">
                          <div className="font-semibold text-indigo-300 truncate">{title}</div>
                          <div className="text-[10px] text-cat-subtext font-mono truncate">
                            {filename}
                          </div>
                        </div>
                        {projectOnly && (
                          <span className="text-[9px] bg-violet-950/50 text-violet-300 border border-violet-500/30 px-1.5 py-0.5 rounded shrink-0">
                            Project
                          </span>
                        )}
                      </button>
                    )
                  })}
                </div>
                <p className="text-[9px] text-cat-overlay italic">
                  Combine uses the workspace copy of each skill when present (same as the agent prompt).
                </p>
              </div>
            )}

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
          </>
        )}
      </div>
    </SlideOver>
  )
}
