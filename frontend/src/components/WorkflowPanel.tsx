import { useState } from 'react'
import type { BriefChangelogEntry, WorkflowNotifications, WorkflowSettings } from '../types'

interface WorkflowPanelProps {
  settings: WorkflowSettings
  changelog: BriefChangelogEntry[]
  notifications: WorkflowNotifications
  onSettingsChange: (partial: Partial<WorkflowSettings>) => void
}

export default function WorkflowPanel({
  settings,
  changelog,
  notifications,
  onSettingsChange,
}: WorkflowPanelProps) {
  const [dodInput, setDodInput] = useState('')
  const [showChangelog, setShowChangelog] = useState(false)

  return (
    <div className="bg-cat-surface0 p-3 rounded-xl border border-cat-surface1 space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-xs font-bold uppercase tracking-wider text-cat-subtext">
          Workflow
        </h3>
        <div className="flex gap-1 flex-wrap justify-end">
          {notifications.needsPo > 0 && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-indigo-950/50 text-indigo-300">
              PO {notifications.needsPo}
            </span>
          )}
          {notifications.needsUser > 0 && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-amber-950/50 text-amber-300">
              User {notifications.needsUser}
            </span>
          )}
          {notifications.pendingApproval > 0 && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-purple-950/50 text-purple-300">
              Approve {notifications.pendingApproval}
            </span>
          )}
          {notifications.qaFailures > 0 && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-rose-950/50 text-rose-300">
              QA fail {notifications.qaFailures}
            </span>
          )}
        </div>
      </div>

      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.requireBacklogApproval}
          onChange={(e) => onSettingsChange({ requireBacklogApproval: e.target.checked })}
        />
        Require backlog approval (optional)
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.requireCodeReview}
          onChange={(e) => onSettingsChange({ requireCodeReview: e.target.checked })}
        />
        Require code review before QA
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.requireDevVerification ?? false}
          onChange={(e) => onSettingsChange({ requireDevVerification: e.target.checked })}
        />
        Require dev run_command/run_test before QA
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.requireToolApproval ?? false}
          onChange={(e) => onSettingsChange({ requireToolApproval: e.target.checked })}
        />
        Require approval for write_file and run_command
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1 pl-5">
        When unchecked (default), tools run immediately without asking. When checked,{' '}
        <span className="text-indigo-300">write_file</span>,{' '}
        <span className="text-indigo-300">apply_patch</span>, and{' '}
        <span className="text-indigo-300">run_command</span> pause until you approve in the modal.
      </p>

      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.autonomousMode ?? false}
          onChange={(e) => onSettingsChange({ autonomousMode: e.target.checked })}
        />
        Autonomous sprint mode (minimal user input)
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1 pl-5">
        When enabled, agents prefer acting over asking. Needs User moves are capped per sprint (
        {settings.maxNeedsUserPerSprint ?? 2} by default).
      </p>

      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.enableWebSearch ?? false}
          onChange={(e) => onSettingsChange({ enableWebSearch: e.target.checked })}
        />
        Enable web search tool for agents
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1 pl-5">
        Uses DuckDuckGo HTML search locally, or set{' '}
        <span className="font-mono">WEB_SEARCH_API_KEY</span> for Serper.
      </p>

      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.enableSemanticSearch ?? true}
          onChange={(e) => onSettingsChange({ enableSemanticSearch: e.target.checked })}
        />
        Enable semantic codebase search (Qdrant)
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1 pl-5">
        Requires Qdrant at{' '}
        <span className="font-mono">{settings.qdrantUrl ?? 'http://localhost:6333'}</span> and{' '}
        <span className="font-mono">ollama pull nomic-embed-text</span>.
      </p>

      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay block">Max Needs User per sprint</span>
        <input
          type="number"
          min={0}
          max={10}
          value={settings.maxNeedsUserPerSprint ?? 2}
          onChange={(e) =>
            onSettingsChange({ maxNeedsUserPerSprint: parseInt(e.target.value, 10) || 0 })
          }
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
        />
      </label>

      <div className="grid grid-cols-3 gap-2 text-[11px]">
        <label>
          <span className="text-[10px] text-cat-overlay block">Max sprint steps</span>
          <input
            type="number"
            min={1}
            max={100}
            value={settings.maxSprintSteps}
            onChange={(e) =>
              onSettingsChange({ maxSprintSteps: parseInt(e.target.value, 10) || 20 })
            }
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
          />
        </label>
        <label>
          <span className="text-[10px] text-cat-overlay block">Max LLM iter/step</span>
          <input
            type="number"
            min={1}
            max={20}
            value={settings.maxLlmIterationsPerStep}
            onChange={(e) =>
              onSettingsChange({
                maxLlmIterationsPerStep: parseInt(e.target.value, 10) || 8,
              })
            }
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
          />
        </label>
        <label>
          <span className="text-[10px] text-cat-overlay block">Max PO round trips</span>
          <input
            type="number"
            min={1}
            max={10}
            value={settings.maxPoRoundTrips ?? 3}
            onChange={(e) =>
              onSettingsChange({
                maxPoRoundTrips: parseInt(e.target.value, 10) || 3,
              })
            }
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
          />
        </label>
      </div>

      <div>
        <span className="text-[10px] text-cat-overlay block mb-1">Definition of Done</span>
        <div className="flex gap-1 mb-1">
          <input
            type="text"
            value={dodInput}
            onChange={(e) => setDodInput(e.target.value)}
            placeholder="Add DoD item…"
            className="flex-1 bg-cat-base border border-cat-surface1 rounded p-1 text-[11px] text-white"
          />
          <button
            type="button"
            onClick={() => {
              if (!dodInput.trim()) return
              onSettingsChange({
                definitionOfDone: [...settings.definitionOfDone, dodInput.trim()],
              })
              setDodInput('')
            }}
            className="text-[10px] px-2 bg-indigo-600/40 rounded text-white"
          >
            Add
          </button>
        </div>
        <ul className="text-[10px] text-cat-subtext space-y-0.5 max-h-16 overflow-y-auto">
          {settings.definitionOfDone.map((item, i) => (
            <li key={i} className="flex justify-between gap-1">
              <span>{item}</span>
              <button
                type="button"
                onClick={() =>
                  onSettingsChange({
                    definitionOfDone: settings.definitionOfDone.filter((_, j) => j !== i),
                  })
                }
                className="text-rose-400"
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      </div>

      <button
        type="button"
        onClick={() => setShowChangelog((s) => !s)}
        className="text-[10px] text-indigo-400 hover:text-indigo-300"
      >
        Brief changelog ({changelog.length})
      </button>
      {showChangelog && (
        <div className="max-h-24 overflow-y-auto text-[10px] space-y-1 border-t border-cat-surface1 pt-2">
          {changelog.slice(0, 10).map((e, i) => (
            <div key={i} className="text-cat-subtext">
              <span className="text-cat-overlay">{e.timestamp}</span> [{e.source}] {e.summary}
            </div>
          ))}
          {changelog.length === 0 && (
            <p className="text-cat-overlay italic">No brief changes yet</p>
          )}
        </div>
      )}
    </div>
  )
}
