import { useCallback, useEffect, useState } from 'react'
import {
  checkQdrantHealth,
  fetchIndexStatus,
  reindexCodebase,
} from '../api/client'
import AgentToolsPanel from './AgentToolsPanel'
import NumberSettingInput from './NumberSettingInput'
import type {
  BriefChangelogEntry,
  IndexProgress,
  WorkflowNotifications,
  WorkflowSettings,
} from '../types'

interface WorkflowPanelProps {
  settings: WorkflowSettings
  changelog: BriefChangelogEntry[]
  notifications: WorkflowNotifications
  onSettingsChange: (partial: Partial<WorkflowSettings>) => void
  ollamaUrl?: string
  indexProgress?: IndexProgress | null
  onOpenMemoryTab?: () => void
}

export default function WorkflowPanel({
  settings,
  changelog,
  notifications,
  onSettingsChange,
  ollamaUrl = 'http://localhost:11434',
  indexProgress = null,
  onOpenMemoryTab,
}: WorkflowPanelProps) {
  const [dodInput, setDodInput] = useState('')
  const [showChangelog, setShowChangelog] = useState(false)
  const [showPerformanceTips, setShowPerformanceTips] = useState(false)
  const [indexStatus, setIndexStatus] = useState<{
    ok?: boolean
    available?: boolean
    chunks?: number
  } | null>(null)
  const [reindexing, setReindexing] = useState(false)
  const [indexError, setIndexError] = useState<string | null>(null)
  const [qdrantApiKeyInput, setQdrantApiKeyInput] = useState('')
  const [qdrantTestStatus, setQdrantTestStatus] = useState<string | null>(null)
  const [qdrantTesting, setQdrantTesting] = useState(false)
  const [reindexResult, setReindexResult] = useState<string | null>(null)

  const refreshIndexStatus = useCallback(async () => {
    try {
      const data = await fetchIndexStatus()
      setIndexStatus(data)
      setIndexError(null)
    } catch {
      setIndexStatus({ ok: false, available: false, chunks: 0 })
    }
  }, [])

  useEffect(() => {
    if (settings.enableSemanticSearch !== false) {
      void refreshIndexStatus()
    }
  }, [settings.enableSemanticSearch, refreshIndexStatus])

  const handleReindex = async () => {
    setReindexing(true)
    setIndexError(null)
    setReindexResult(null)
    try {
      const result = await reindexCodebase(ollamaUrl)
      if (result.filesScanned != null) {
        setReindexResult(
          `${result.filesScanned} files → ${result.chunks ?? 0} chunks` +
            (result.filesSkipped ? ` (${result.filesSkipped} skipped)` : ''),
        )
      }
      await refreshIndexStatus()
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Reindex failed'
      setIndexError(msg)
      setReindexResult(null)
    } finally {
      setReindexing(false)
    }
  }

  const handleQdrantTest = async () => {
    setQdrantTesting(true)
    setQdrantTestStatus(null)
    try {
      const url = settings.qdrantUrl ?? 'http://localhost:6333'
      const key = qdrantApiKeyInput.trim() || undefined
      const result = await checkQdrantHealth(url, key)
      if (result.ok) {
        const cols = result.collections?.length ?? 0
        setQdrantTestStatus(`Connected — ${cols} collection(s)`)
      } else {
        setQdrantTestStatus(result.error ?? 'Connection failed')
      }
    } catch (e) {
      setQdrantTestStatus(e instanceof Error ? e.message : 'Connection failed')
    } finally {
      setQdrantTesting(false)
    }
  }

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
          checked={settings.requireBacklogRefinement ?? false}
          onChange={(e) => onSettingsChange({ requireBacklogRefinement: e.target.checked })}
        />
        Require backlog refinement before dev
      </label>
      {(settings.requireBacklogRefinement ?? false) && (
        <label className="flex items-center gap-2 text-[11px] text-cat-subtext pl-5 cursor-pointer">
          <input
            type="checkbox"
            checked={settings.prioritizeImplementationOverRefinement !== false}
            onChange={(e) =>
              onSettingsChange({ prioritizeImplementationOverRefinement: e.target.checked })
            }
          />
          Claim Backlog / In Progress before more Refinement
        </label>
      )}
      {(settings.requireBacklogRefinement ?? false) && (
        <label className="flex items-center gap-2 text-[11px] text-cat-subtext pl-5">
          <span className="text-cat-overlay shrink-0">Max refinement rounds</span>
          <NumberSettingInput
            value={settings.maxRefinementRoundTrips ?? 3}
            min={1}
            max={10}
            onCommit={(maxRefinementRoundTrips) => onSettingsChange({ maxRefinementRoundTrips })}
            className="w-16 bg-cat-base border border-cat-surface1 rounded px-2 py-0.5 text-cat-text"
          />
        </label>
      )}
      {(settings.requireBacklogRefinement ?? false) && (
        <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1 pl-5">
          New PO stories go to Refinement for Dev↔PO grooming before Backlog. Drag cards in
          Refinement to set execution order. Existing Backlog cards are grandfathered unless
          manually moved to Refinement.
        </p>
      )}
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext pl-5">
        <span className="text-cat-overlay shrink-0">Max subtask depth</span>
        <NumberSettingInput
          value={settings.maxSubtaskDepth ?? 4}
          min={1}
          max={10}
          onCommit={(maxSubtaskDepth) => onSettingsChange({ maxSubtaskDepth })}
          className="w-16 bg-cat-base border border-cat-surface1 rounded px-2 py-0.5 text-cat-text"
        />
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext pl-5">
        <span className="text-cat-overlay shrink-0">Max subtask spawns</span>
        <NumberSettingInput
          value={settings.maxSubtaskSpawns ?? 8}
          min={1}
          max={30}
          onCommit={(maxSubtaskSpawns) => onSettingsChange({ maxSubtaskSpawns })}
          className="w-16 bg-cat-base border border-cat-surface1 rounded px-2 py-0.5 text-cat-text"
        />
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
          checked={settings.requireCleanLint ?? false}
          onChange={(e) => onSettingsChange({ requireCleanLint: e.target.checked })}
        />
        Require clean lint before dev/QA advance (Cursor-like)
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.enableFixVerifyLoop ?? false}
          onChange={(e) => onSettingsChange({ enableFixVerifyLoop: e.target.checked })}
        />
        Enable fix-verify loop on dev steps
      </label>
      {(settings.enableFixVerifyLoop ?? false) && (
        <label className="flex items-center gap-2 text-[11px] text-cat-subtext pl-5">
          <span className="text-cat-overlay shrink-0">Max rounds</span>
          <NumberSettingInput
            value={settings.maxFixVerifyRounds ?? 3}
            min={1}
            max={10}
            onCommit={(maxFixVerifyRounds) => onSettingsChange({ maxFixVerifyRounds })}
            className="w-16 bg-cat-base border border-cat-surface1 rounded px-2 py-0.5 text-cat-text"
          />
        </label>
      )}
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
      {(settings.requireToolApproval ?? false) && (
        <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer pl-5">
          <input
            type="checkbox"
            checked={settings.nonBlockingToolApproval !== false}
            onChange={(e) => onSettingsChange({ nonBlockingToolApproval: e.target.checked })}
          />
          Non-blocking approval (don&apos;t freeze sprint thread)
        </label>
      )}
      {(settings.requireToolApproval ?? false) && (
        <div className="pl-5 space-y-2">
          <label className="text-[11px] text-cat-subtext block">
            <span className="text-[10px] text-cat-overlay block">Command auto-run mode</span>
            <select
              value={settings.commandAutoRunMode ?? 'off'}
              onChange={(e) =>
                onSettingsChange({
                  commandAutoRunMode: e.target.value as
                    | 'off'
                    | 'allowlist'
                    | 'denylist'
                    | 'all',
                })
              }
              className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white text-[11px]"
            >
              <option value="off">Off — all run_command needs approval</option>
              <option value="allowlist">Allowlist — auto-run matching commands</option>
              <option value="denylist">Denylist — block only matching commands</option>
              <option value="all">All — auto-run every command</option>
            </select>
          </label>
          {(settings.commandAutoRunMode === 'allowlist' ||
            settings.commandAutoRunMode === 'denylist') && (
            <label className="text-[11px] text-cat-subtext block">
              <span className="text-[10px] text-cat-overlay block">
                {settings.commandAutoRunMode === 'allowlist' ? 'Allowlist' : 'Denylist'} (one per
                line)
              </span>
              <textarea
                rows={3}
                value={(settings.commandAutoRunMode === 'allowlist'
                  ? settings.commandAllowlist
                  : settings.commandDenylist
                )?.join('\n') ?? ''}
                onChange={(e) => {
                  const lines = e.target.value
                    .split('\n')
                    .map((l) => l.trim())
                    .filter(Boolean)
                  if (settings.commandAutoRunMode === 'allowlist') {
                    onSettingsChange({ commandAllowlist: lines })
                  } else {
                    onSettingsChange({ commandDenylist: lines })
                  }
                }}
                className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-[10px] font-mono text-white"
              />
            </label>
          )}
          <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
            <input
              type="checkbox"
              checked={settings.allowChainedCommands ?? false}
              onChange={(e) => onSettingsChange({ allowChainedCommands: e.target.checked })}
            />
            Allow safe command chaining (&& and ;)
          </label>
        </div>
      )}

      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay block">Max MCP tools (budget)</span>
        <NumberSettingInput
          value={settings.maxMcpTools ?? 40}
          min={0}
          max={200}
          onCommit={(maxMcpTools) => onSettingsChange({ maxMcpTools })}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
        />
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1">
        MCP servers are configured in workflow settings JSON (stdio, http, or sse transport).
        Per-server <span className="font-mono">enabledTools</span> /{' '}
        <span className="font-mono">disabledTools</span> filter which tools register.
      </p>

      <AgentToolsPanel settings={settings} onSettingsChange={onSettingsChange} />

      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.pauseSprintOnNeedsUser ?? false}
          onChange={(e) => onSettingsChange({ pauseSprintOnNeedsUser: e.target.checked })}
        />
        Pause sprint when any card is in Needs User
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1 pl-5">
        Off by default — sprint continues other lanes while cards wait for your input.
      </p>

      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.autoFormatAfterEdit !== false}
          onChange={(e) => onSettingsChange({ autoFormatAfterEdit: e.target.checked })}
        />
        Auto-format Dart files after edits (dart format)
      </label>

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
        {settings.maxNeedsUserPerSprint ?? 2} by default). Duplicate questions and clarification
        requests are routed to Needs PO instead.
      </p>
      <p className="text-[10px] text-cat-overlay leading-relaxed pl-5 border-l-2 border-amber-500/30 ml-1">
        <span className="text-amber-300/90 font-semibold">Too many Needs User cards?</span> Enable
        autonomous mode, set max to 1, increase max stuck steps to 5, and put API keys or design
        defaults in the Project Brief. Cards can be bulk-sent to PO from the Sprint panel.
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
        Requires Qdrant and an Ollama embed model (e.g.{' '}
        <span className="font-mono">ollama pull {settings.embedModel ?? 'nomic-embed-text'}</span>
        ).
      </p>
      {(settings.enableSemanticSearch ?? true) && (
        <div className="pl-5 space-y-2">
          <label className="block text-[10px] text-cat-subtext">
            Embed model (Ollama)
            <input
              type="text"
              value={settings.embedModel ?? 'nomic-embed-text'}
              onChange={(e) => onSettingsChange({ embedModel: e.target.value })}
              placeholder="nomic-embed-text:1.5"
              className="mt-0.5 w-full bg-cat-base border border-cat-surface1 rounded px-2 py-1 font-mono text-[10px] text-white"
            />
          </label>
          <p className="text-[10px] text-cat-overlay leading-relaxed">
            Used for Qdrant indexing and project memory. Must match a name from{' '}
            <span className="font-mono">ollama list</span>.
          </p>
          <label className="block text-[10px] text-cat-subtext">
            Qdrant URL
            <input
              type="text"
              value={settings.qdrantUrl ?? 'http://localhost:6333'}
              onChange={(e) => onSettingsChange({ qdrantUrl: e.target.value })}
              className="mt-0.5 w-full bg-cat-base border border-cat-surface1 rounded px-2 py-1 font-mono text-[10px] text-white"
            />
          </label>
          <label className="block text-[10px] text-cat-subtext">
            Qdrant API key
            {settings.qdrantApiKeyConfigured && (
              <span className="ml-1 text-emerald-400">(configured)</span>
            )}
            <input
              type="password"
              value={qdrantApiKeyInput}
              onChange={(e) => setQdrantApiKeyInput(e.target.value)}
              onBlur={() => {
                if (qdrantApiKeyInput.trim()) {
                  onSettingsChange({ qdrantApiKey: qdrantApiKeyInput.trim() })
                }
              }}
              placeholder={settings.qdrantApiKeyConfigured ? '•••••••• (leave blank to keep)' : 'Optional API key'}
              className="mt-0.5 w-full bg-cat-base border border-cat-surface1 rounded px-2 py-1 font-mono text-[10px] text-white"
            />
          </label>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={qdrantTesting}
              onClick={() => void handleQdrantTest()}
              className="text-[10px] text-indigo-300 hover:text-indigo-200 disabled:opacity-50"
            >
              {qdrantTesting ? 'Testing…' : 'Test connection'}
            </button>
            {qdrantTestStatus && (
              <span
                className={`text-[10px] ${
                  qdrantTestStatus.startsWith('Connected') ? 'text-emerald-300' : 'text-rose-300'
                }`}
              >
                {qdrantTestStatus}
              </span>
            )}
          </div>
        </div>
      )}
      {(settings.enableSemanticSearch ?? true) && (
        <div className="pl-5 flex flex-wrap items-center gap-2 text-[10px]">
          <span
            className={`px-1.5 py-0.5 rounded ${
              indexStatus?.chunks
                ? 'bg-emerald-950/50 text-emerald-300'
                : indexStatus?.available
                  ? 'bg-amber-950/50 text-amber-300'
                  : 'bg-cat-surface1 text-cat-overlay'
            }`}
          >
            Index:{' '}
            {indexStatus == null
              ? '…'
              : indexStatus.chunks
                ? `${indexStatus.chunks} chunks`
                : indexStatus.available
                  ? 'empty — reindex'
                  : 'Qdrant offline'}
          </span>
          <button
            type="button"
            disabled={reindexing}
            onClick={() => void handleReindex()}
            className="text-indigo-300 hover:text-indigo-200 disabled:opacity-50"
          >
            {reindexing ? 'Indexing…' : 'Reindex codebase'}
          </button>
          <button
            type="button"
            onClick={() => void refreshIndexStatus()}
            className="text-cat-overlay hover:text-cat-subtext"
          >
            Refresh
          </button>
        </div>
      )}
      {indexError && (
        <p className="text-[10px] text-rose-300 pl-5">{indexError}</p>
      )}
      {reindexResult && !indexError && (
        <p className="text-[10px] text-emerald-300 pl-5">{reindexResult}</p>
      )}
      {(reindexing || indexProgress) && (
        <div className="pl-5 space-y-1">
          {indexProgress && indexProgress.filesTotal > 0 ? (
            <>
              <progress
                className="w-full h-1.5 accent-indigo-500"
                value={indexProgress.filesDone}
                max={indexProgress.filesTotal}
              />
              <p className="text-[10px] text-cat-overlay font-mono truncate">
                {indexProgress.phase === 'preflight'
                  ? 'Checking embed model…'
                  : `Indexing ${indexProgress.filesDone}/${indexProgress.filesTotal} — ${indexProgress.chunks} chunks`}
                {indexProgress.currentFile ? ` · ${indexProgress.currentFile}` : ''}
              </p>
            </>
          ) : (
            <progress className="w-full h-1.5 accent-indigo-500" />
          )}
        </div>
      )}
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer pl-5">
        <input
          type="checkbox"
          checked={settings.enableSemanticSprintContext !== false}
          onChange={(e) => onSettingsChange({ enableSemanticSprintContext: e.target.checked })}
        />
        Pre-load semantic index chunks at sprint step start
      </label>

      <div className="border-t border-cat-surface1 pt-2">
        <button
          type="button"
          onClick={() => setShowPerformanceTips((v) => !v)}
          className="text-[10px] uppercase tracking-wider text-cat-overlay hover:text-cat-subtext"
        >
          {showPerformanceTips ? '▼' : '▶'} Performance tuning
        </button>
        {showPerformanceTips && (
          <div className="mt-2 space-y-2 text-[10px] text-cat-overlay leading-relaxed">
            <p>
              <span className="text-cat-subtext">Models:</span> use 7b/8b for PO/CR; dev 7b before
              14b. Prefer quantized tags (e.g. <span className="font-mono">:q4_K_M</span>) on limited
              RAM/VRAM.
            </p>
            <p>
              <span className="text-cat-subtext">Qdrant:</span>{' '}
              <span className="font-mono">docker run -p 6333:6333 qdrant/qdrant</span> then Reindex
              above. Index updates incrementally on agent file writes.
            </p>
            <p>
              <span className="text-cat-subtext">Iterations:</span> lower Max LLM iter/step (5) for
              simple tasks. Trim assigned skills per agent.
            </p>
          </div>
        )}
      </div>

      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay block">Ollama keep-alive</span>
        <input
          type="text"
          value={settings.ollamaKeepAlive ?? '30m'}
          onChange={(e) => onSettingsChange({ ollamaKeepAlive: e.target.value || '30m' })}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white font-mono text-[11px]"
        />
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1">
        Keeps model loaded between sprint iterations (e.g. 30m). Reduces reload latency.
      </p>

      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay block">Max tool output chars (to LLM)</span>
        <NumberSettingInput
          value={settings.maxToolOutputCharsForLlm ?? 6000}
          min={1000}
          max={50000}
          onCommit={(maxToolOutputCharsForLlm) => onSettingsChange({ maxToolOutputCharsForLlm })}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
        />
      </label>

      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay block">Message prune threshold (% of num_ctx)</span>
        <NumberSettingInput
          value={settings.messagePruneThresholdPct ?? 60}
          min={30}
          max={90}
          onCommit={(messagePruneThresholdPct) => onSettingsChange({ messagePruneThresholdPct })}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
        />
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1">
        When conversation exceeds this budget, oldest tool messages are dropped before each LLM call.
      </p>

      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay block">Ollama context size (num_ctx)</span>
        <NumberSettingInput
          value={settings.ollamaNumCtx ?? 32768}
          min={4096}
          max={131072}
          onCommit={(ollamaNumCtx) => onSettingsChange({ ollamaNumCtx })}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
        />
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1">
        Increase if you see exceed_context_size_error. Higher values use more RAM.
      </p>

      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay block">Ollama request timeout (seconds)</span>
        <NumberSettingInput
          value={settings.ollamaRequestTimeoutSec ?? 300}
          min={60}
          max={900}
          onCommit={(ollamaRequestTimeoutSec) => onSettingsChange({ ollamaRequestTimeoutSec })}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
        />
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1">
        Per-attempt HTTP timeout. Raise for slow models (default 300s; was 120s).
      </p>

      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay block">Ollama max retries per call</span>
        <NumberSettingInput
          value={settings.ollamaMaxRetries ?? 4}
          min={1}
          max={10}
          onCommit={(ollamaMaxRetries) => onSettingsChange({ ollamaMaxRetries })}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
        />
      </label>

      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.ollamaCooldownRetryEnabled !== false}
          onChange={(e) => onSettingsChange({ ollamaCooldownRetryEnabled: e.target.checked })}
          className="rounded"
        />
        Cooldown retry after initial failures (extra attempts after pause)
      </label>

      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay block">Cooldown pause (seconds)</span>
        <NumberSettingInput
          value={settings.ollamaCooldownRetrySec ?? 15}
          min={0}
          max={120}
          onCommit={(ollamaCooldownRetrySec) => onSettingsChange({ ollamaCooldownRetrySec })}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
        />
      </label>

      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay block">Cooldown extra attempts</span>
        <NumberSettingInput
          value={settings.ollamaCooldownRetryAttempts ?? 2}
          min={0}
          max={5}
          onCommit={(ollamaCooldownRetryAttempts) => onSettingsChange({ ollamaCooldownRetryAttempts })}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
        />
      </label>

      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay block">Max Needs User per sprint</span>
        <NumberSettingInput
          value={settings.maxNeedsUserPerSprint ?? 2}
          min={0}
          max={10}
          onCommit={(maxNeedsUserPerSprint) => onSettingsChange({ maxNeedsUserPerSprint })}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
        />
      </label>
      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay block">Needs User cooldown (sprint steps)</span>
        <NumberSettingInput
          value={settings.needsUserCooldownSteps ?? 3}
          min={0}
          max={20}
          onCommit={(needsUserCooldownSteps) => onSettingsChange({ needsUserCooldownSteps })}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
        />
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1">
        After you resolve a Needs User card, the same question cannot re-escalate for this many
        sprint steps. Prior answers are injected into agent prompts.
      </p>

      <div className="grid grid-cols-3 gap-2 text-[11px]">
        <label>
          <span className="text-[10px] text-cat-overlay block">Max sprint steps</span>
          <NumberSettingInput
            value={settings.maxSprintSteps}
            min={1}
            max={100}
            onCommit={(maxSprintSteps) => onSettingsChange({ maxSprintSteps })}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
          />
        </label>
        <label>
          <span className="text-[10px] text-cat-overlay block">Max LLM iter/step</span>
          <NumberSettingInput
            value={settings.maxLlmIterationsPerStep}
            min={1}
            max={20}
            onCommit={(maxLlmIterationsPerStep) => onSettingsChange({ maxLlmIterationsPerStep })}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
          />
        </label>
        <label>
          <span className="text-[10px] text-cat-overlay block">Max PO round trips</span>
          <NumberSettingInput
            value={settings.maxPoRoundTrips ?? 3}
            min={1}
            max={10}
            onCommit={(maxPoRoundTrips) => onSettingsChange({ maxPoRoundTrips })}
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

      <div className="border-t border-cat-surface1 pt-2">
        <p className="text-[10px] text-cat-overlay leading-relaxed">
          Project memories are injected into agent prompts. View, add, and edit notes in the bottom
          Memory tab.
        </p>
        {onOpenMemoryTab && (
          <button
            type="button"
            onClick={onOpenMemoryTab}
            className="mt-1 text-[10px] text-indigo-400 hover:text-indigo-300"
          >
            Open Memory tab →
          </button>
        )}
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
