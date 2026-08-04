import { useCallback, useEffect, useState } from 'react'
import {
  checkQdrantHealth,
  exportTrainingJsonl,
  fetchIndexStatus,
  probeMcpServers,
  reindexCodebase,
  reloadMcpServers,
  testPhoneNotify,
} from '../api/client'
import AgentToolsPanel from './AgentToolsPanel'
import AgentPromptsPanel from './AgentPromptsPanel'
import NumberSettingInput from './NumberSettingInput'
import { SettingHint } from './SettingHint'
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
  onOpenCustomTools?: () => void
  discordBotStatus?: {
    status?: string
    lastError?: string
    readyAt?: string
    running?: boolean
  } | null
}

export default function WorkflowPanel({
  settings,
  changelog,
  notifications,
  onSettingsChange,
  ollamaUrl = 'http://localhost:11434',
  indexProgress = null,
  onOpenMemoryTab,
  onOpenCustomTools,
  discordBotStatus = null,
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
  const [mcpServersJson, setMcpServersJson] = useState(() =>
    JSON.stringify(settings.mcpServers ?? [], null, 2),
  )
  const [mcpServersError, setMcpServersError] = useState<string | null>(null)
  const [retryDelayText, setRetryDelayText] = useState(
    () => (settings.ollamaRetryDelaySec ?? [0, 2, 5, 10]).join(', '),
  )
  const [discordWebhookInput, setDiscordWebhookInput] = useState('')
  const [phoneNotifyStatus, setPhoneNotifyStatus] = useState<string | null>(null)
  const [phoneNotifyTesting, setPhoneNotifyTesting] = useState(false)
  const [discordBotTokenInput, setDiscordBotTokenInput] = useState('')
  const [discordAllowedUsersText, setDiscordAllowedUsersText] = useState(
    () => (settings.discordBotAllowedUserIds ?? []).join('\n'),
  )
  const [trainingExportLimit, setTrainingExportLimit] = useState(50)
  const [trainingExportStatus, setTrainingExportStatus] = useState<string | null>(null)
  const [trainingExporting, setTrainingExporting] = useState(false)
  const [workflowTab, setWorkflowTab] = useState<
    'gates' | 'autonomy' | 'rag' | 'tools' | 'prompts' | 'discord'
  >('gates')
  const [mcpProbeStatus, setMcpProbeStatus] = useState<string | null>(null)
  const [mcpBusy, setMcpBusy] = useState(false)


  useEffect(() => {
    setMcpServersJson(JSON.stringify(settings.mcpServers ?? [], null, 2))
    setMcpServersError(null)
  }, [settings.mcpServers])

  useEffect(() => {
    setRetryDelayText((settings.ollamaRetryDelaySec ?? [0, 2, 5, 10]).join(', '))
  }, [settings.ollamaRetryDelaySec])

  useEffect(() => {
    // Secret is never returned from API; keep local input blank when configured.
    if (!settings.phoneNotifyDiscordWebhookConfigured) {
      setDiscordWebhookInput('')
    }
  }, [settings.phoneNotifyDiscordWebhookConfigured])

  useEffect(() => {
    if (!settings.discordBotTokenConfigured) {
      setDiscordBotTokenInput('')
    }
  }, [settings.discordBotTokenConfigured])

  useEffect(() => {
    setDiscordAllowedUsersText((settings.discordBotAllowedUserIds ?? []).join('\n'))
  }, [settings.discordBotAllowedUserIds])

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

      <div
        className="flex flex-wrap gap-1.5 items-center"
        data-testid="workflow-presets"
      >
        <span className="text-[10px] text-cat-overlay mr-1">Presets:</span>
        {(
          [
            {
              id: 'solo',
              label: 'Solo',
              patch: {
                requireToolApproval: false,
                requireCodeReview: false,
                requireCleanLint: false,
                requireBacklogApproval: false,
                autonomousMode: false,
              },
            },
            {
              id: 'gated',
              label: 'Gated',
              patch: {
                requireToolApproval: true,
                requireCodeReview: true,
                requireCleanLint: true,
                requireDevVerification: true,
                autonomousMode: false,
              },
            },
            {
              id: 'autonomous',
              label: 'Autonomous',
              patch: {
                autonomousMode: true,
                requireToolApproval: false,
                maxNeedsUserPerSprint: 8,
                pauseSprintOnNeedsUser: false,
              },
            },
          ] as const
        ).map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => onSettingsChange({ ...p.patch })}
            className="text-[10px] px-2 py-0.5 rounded border border-cat-surface1 text-cat-subtext hover:bg-cat-base hover:text-white"
          >
            {p.label}
          </button>
        ))}
        <span className="text-[9px] text-cat-overlay">
          (never clears Discord/phone secrets)
        </span>
      </div>

      <div className="flex flex-wrap gap-1" data-testid="workflow-tabs">
        {(
          [
            ['gates', 'Gates'],
            ['autonomy', 'Autonomy'],
            ['rag', 'RAG / Memory'],
            ['tools', 'Tools / MCP'],
            ['prompts', 'Prompts'],
            ['discord', 'Phone / Discord'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            onClick={() => setWorkflowTab(id)}
            className={`text-[10px] px-2 py-0.5 rounded border ${
              workflowTab === id
                ? 'border-indigo-500/50 text-indigo-200 bg-indigo-950/40'
                : 'border-cat-surface1 text-cat-subtext hover:bg-cat-base'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {(workflowTab === 'autonomy') && (
      <div className="border border-indigo-500/20 bg-indigo-950/20 rounded-lg p-2 space-y-2">
        <p className="text-[10px] font-bold uppercase tracking-wider text-indigo-200">
          LLM context / speed
        </p>
        <p className="text-[10px] text-cat-overlay leading-relaxed">
          Already on. Raise tool-output chars if tools look truncated; lower prune % to keep more
          history. Also on Settings → Models.
        </p>
        <label className="text-[11px] text-cat-subtext block">
          <span className="text-[10px] text-cat-overlay inline-flex items-center">
            Max tool output chars (to LLM)
            <SettingHint hint="How much of each tool result is sent back to the model. Raise if replies look cut off; lower to save memory and speed." />
          </span>
          <NumberSettingInput
            value={settings.maxToolOutputCharsForLlm ?? 6000}
            min={1000}
            max={50000}
            onCommit={(maxToolOutputCharsForLlm) => onSettingsChange({ maxToolOutputCharsForLlm })}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
          />
        </label>
        <label className="text-[11px] text-cat-subtext block">
          <span className="text-[10px] text-cat-overlay inline-flex items-center">
            Message prune threshold (% of num_ctx)
            <SettingHint hint="When chat history fills this percent of the model context window, older tool messages are dropped to make room." />
          </span>
          <NumberSettingInput
            value={settings.messagePruneThresholdPct ?? 60}
            min={30}
            max={90}
            onCommit={(messagePruneThresholdPct) => onSettingsChange({ messagePruneThresholdPct })}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
          />
        </label>
      </div>
      )}

      {(workflowTab === 'gates') && (
      <>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.requireBacklogApproval}
          onChange={(e) => onSettingsChange({ requireBacklogApproval: e.target.checked })}
        />
        Require backlog approval (optional)
        <SettingHint hint="New stories wait in Pending Approval until you approve them before they enter the backlog." />
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.requireBacklogRefinement ?? false}
          onChange={(e) => onSettingsChange({ requireBacklogRefinement: e.target.checked })}
        />
        Require backlog refinement before dev
        <SettingHint hint="Stories go through a Refinement lane so Dev and PO can groom them before coding starts." />
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.enableBlockedLane !== false}
          onChange={(e) => onSettingsChange({ enableBlockedLane: e.target.checked })}
        />
        Auto Blocked lane (move cards waiting on deps)
        <SettingHint hint="Automatically parks cards that are waiting on other cards into a Blocked lane." />
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
          <SettingHint hint="Prefer coding ready cards over grooming more stories when both are available." />
        </label>
      )}
      {(settings.requireBacklogRefinement ?? false) && (
        <label className="flex items-center gap-2 text-[11px] text-cat-subtext pl-5">
          <span className="text-cat-overlay shrink-0 inline-flex items-center">
            Max refinement rounds
            <SettingHint hint="How many times Dev and PO can bounce a card in Refinement before it must move on." />
          </span>
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
        <span className="text-cat-overlay shrink-0 inline-flex items-center">
          Max subtask depth
          <SettingHint hint="How deep nested subtasks can go when a card is split into smaller pieces." />
        </span>
        <NumberSettingInput
          value={settings.maxSubtaskDepth ?? 4}
          min={1}
          max={10}
          onCommit={(maxSubtaskDepth) => onSettingsChange({ maxSubtaskDepth })}
          className="w-16 bg-cat-base border border-cat-surface1 rounded px-2 py-0.5 text-cat-text"
        />
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext pl-5">
        <span className="text-cat-overlay shrink-0 inline-flex items-center">
          Max subtask spawns
          <SettingHint hint="Upper limit on how many child cards one parent can create." />
        </span>
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
        <SettingHint hint="Cards must pass the Code Reviewer agent before they can enter QA." />
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.requireDevVerification ?? false}
          onChange={(e) => onSettingsChange({ requireDevVerification: e.target.checked })}
        />
        Require dev run_command/run_test before QA
        <SettingHint hint="Developer must successfully run a test or command before the card can leave Dev." />
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.requireCleanLint ?? false}
          onChange={(e) => onSettingsChange({ requireCleanLint: e.target.checked })}
        />
        Require clean lint before dev/QA advance (Cursor-like)
        <SettingHint hint="Blocks progress while the last analyze/lint run still has unresolved findings." />
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.requireAcChecklistForDone ?? true}
          onChange={(e) => onSettingsChange({ requireAcChecklistForDone: e.target.checked })}
        />
        Require AC checklist before Done
        <SettingHint hint="When on, every acceptance criterion on the card must be checked (or QA override) before Done." />
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1 pl-5">
        When on, all acceptance criteria must be checked on the card (or QA override) before Done.
      </p>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.requireWorkspaceStructure ?? true}
          onChange={(e) => onSettingsChange({ requireWorkspaceStructure: e.target.checked })}
        />
        Require workspace structure before Code Review/QA
        <SettingHint hint="Checks that expected project folders/files exist before review or QA." />
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.autoScaffoldOnStructureGap ?? true}
          onChange={(e) => onSettingsChange({ autoScaffoldOnStructureGap: e.target.checked })}
        />
        Auto-scaffold when structure critically incomplete
        <SettingHint hint="If key project files are missing, the agent may create a basic scaffold automatically." />
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.enableFixVerifyLoop ?? false}
          onChange={(e) => onSettingsChange({ enableFixVerifyLoop: e.target.checked })}
        />
        Enable fix-verify loop on dev steps
        <SettingHint hint="After edits, automatically re-run checks and let Dev fix failures for a few rounds." />
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1 pl-5">
        Also runs automatically when &quot;Require clean lint&quot; is on, even if this checkbox is off.
      </p>
      {(settings.enableFixVerifyLoop ?? false) || (settings.requireCleanLint ?? false) ? (
        <label className="flex items-center gap-2 text-[11px] text-cat-subtext pl-5">
          <span className="text-cat-overlay shrink-0 inline-flex items-center">
            Max rounds
            <SettingHint hint="How many fix-then-recheck cycles Dev may run in one step." />
          </span>
          <NumberSettingInput
            value={settings.maxFixVerifyRounds ?? 3}
            min={1}
            max={10}
            onCommit={(maxFixVerifyRounds) => onSettingsChange({ maxFixVerifyRounds })}
            className="w-16 bg-cat-base border border-cat-surface1 rounded px-2 py-0.5 text-cat-text"
          />
        </label>
      ) : null}
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.requireToolApproval ?? false}
          onChange={(e) => onSettingsChange({ requireToolApproval: e.target.checked })}
        />
        Require approval for write_file and run_command
        <SettingHint hint="When on, risky tools pause until you approve them in the UI (or Discord)." />
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
            <span className="text-[10px] text-cat-overlay block">
              Tools requiring approval (one per line)
            </span>
            <textarea
              rows={3}
              value={(settings.toolApprovalTools ?? ['write_file', 'run_command', 'delete_file']).join(
                '\n',
              )}
              onChange={(e) => {
                const lines = e.target.value
                  .split('\n')
                  .map((l) => l.trim())
                  .filter(Boolean)
                onSettingsChange({ toolApprovalTools: lines })
              }}
              className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-[10px] font-mono text-white"
            />
          </label>
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
        </div>
      )}

      </>
      )}

      {(workflowTab === 'tools') && (
      <>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.allowChainedCommands !== false}
          onChange={(e) => onSettingsChange({ allowChainedCommands: e.target.checked })}
        />
        Allow safe command chaining (&amp;&amp; and ;)
        <SettingHint hint="Lets agents run simple command chains like build && test. Redirects and pipes stay blocked." />
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1 pl-5">
        On by default. When off, <span className="font-mono">run_command</span> rejects{' '}
        <span className="font-mono">&amp;&amp;</span> / <span className="font-mono">;</span> chains.
        Redirects (<span className="font-mono">| &gt; &lt;</span>) stay blocked either way.
      </p>

      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay inline-flex items-center">
          Max MCP tools (budget)
          <SettingHint hint="Caps how many external MCP tools can register so the model is not overloaded with tool choices." />
        </span>
        <NumberSettingInput
          value={settings.maxMcpTools ?? 40}
          min={0}
          max={200}
          onCommit={(maxMcpTools) => onSettingsChange({ maxMcpTools })}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
        />
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1">
        MCP servers use stdio, http, or sse transport. Per-server{' '}
        <span className="font-mono">enabledTools</span> /{' '}
        <span className="font-mono">disabledTools</span> filter which tools register.
      </p>
      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay block">MCP servers (JSON array)</span>
        <textarea
          rows={5}
          value={mcpServersJson}
          onChange={(e) => {
            setMcpServersJson(e.target.value)
            setMcpServersError(null)
          }}
          onBlur={() => {
            try {
              const parsed = JSON.parse(mcpServersJson || '[]') as unknown
              if (!Array.isArray(parsed)) {
                setMcpServersError('MCP servers must be a JSON array')
                return
              }
              onSettingsChange({ mcpServers: parsed as WorkflowSettings['mcpServers'] })
              setMcpServersError(null)
            } catch {
              setMcpServersError('Invalid JSON')
            }
          }}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-[10px] font-mono text-white"
          spellCheck={false}
        />
      </label>
      {mcpServersError && (
        <p className="text-[10px] text-rose-400 -mt-1">{mcpServersError}</p>
      )}
      <div className="flex flex-wrap gap-2" data-testid="mcp-actions">
        <button
          type="button"
          disabled={mcpBusy}
          onClick={() => {
            setMcpBusy(true)
            setMcpProbeStatus(null)
            void (async () => {
              try {
                const res = await probeMcpServers()
                const parts = (res.servers || []).map((s) =>
                  s.ok ? `${s.name}: ok (${s.toolCount ?? 0} tools)` : `${s.name}: ${s.error || 'fail'}`,
                )
                setMcpProbeStatus(parts.join(' · ') || 'No MCP servers configured')
              } catch (e) {
                setMcpProbeStatus(e instanceof Error ? e.message : 'Probe failed')
              } finally {
                setMcpBusy(false)
              }
            })()
          }}
          className="text-[10px] px-2.5 py-1 rounded border border-indigo-500/40 text-indigo-200 hover:bg-indigo-950/40 disabled:opacity-40"
        >
          {mcpBusy ? 'Working…' : 'Test MCP'}
        </button>
        <button
          type="button"
          disabled={mcpBusy}
          onClick={() => {
            setMcpBusy(true)
            setMcpProbeStatus(null)
            void (async () => {
              try {
                const res = await reloadMcpServers()
                setMcpProbeStatus(`Reloaded — registered ${res.registered ?? 0} tool(s)`)
              } catch (e) {
                setMcpProbeStatus(e instanceof Error ? e.message : 'Reload failed')
              } finally {
                setMcpBusy(false)
              }
            })()
          }}
          className="text-[10px] px-2.5 py-1 rounded border border-emerald-500/40 text-emerald-200 hover:bg-emerald-950/40 disabled:opacity-40"
        >
          Reload MCP
        </button>
      </div>
      {mcpProbeStatus && (
        <p className="text-[10px] text-violet-300 leading-relaxed">{mcpProbeStatus}</p>
      )}

      <AgentToolsPanel
        settings={settings}
        onSettingsChange={onSettingsChange}
        onOpenCustomTools={onOpenCustomTools}
      />
      </>
      )}

      {(workflowTab === 'prompts') && (
        <AgentPromptsPanel settings={settings} onSettingsChange={onSettingsChange} />
      )}

      {(workflowTab === 'discord') && (
      <details open className="border border-cat-surface1 rounded-lg p-2.5 space-y-2 bg-cat-base/30" data-testid="workflow-section-phone-discord">
        <summary className="text-[10px] font-bold uppercase tracking-wider text-cat-subtext cursor-pointer">
          Phone / Discord control
        </summary>
        <p className="text-[10px] text-cat-overlay leading-relaxed">
          Phone alerts (outbound webhook) and optional Discord control bot (Gateway outbound on this
          PC). Neither opens inbound ports. Prefer a private server; treat tokens/URLs like passwords.
        </p>
        <p className="text-[10px] font-semibold text-cat-subtext pt-1">Phone alerts (outbound)</p>
        <p className="text-[10px] text-cat-overlay leading-relaxed">
          Posts to a Discord webhook for mobile push. Does not open ports on this PC.
        </p>
        <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
          <input
            type="checkbox"
            checked={settings.phoneNotifyEnabled ?? false}
            onChange={(e) => onSettingsChange({ phoneNotifyEnabled: e.target.checked })}
          />
          Enable Discord phone alerts
          <SettingHint hint="Sends outbound Discord webhook messages to your phone for events like Needs User or sprint end." />
        </label>
        <label className="text-[11px] text-cat-subtext block">
          <span className="text-[10px] text-cat-overlay block">
            Discord webhook URL
            {settings.phoneNotifyDiscordWebhookConfigured
              ? ' (saved — leave blank to keep)'
              : ''}
          </span>
          <input
            type="password"
            autoComplete="off"
            value={discordWebhookInput}
            onChange={(e) => setDiscordWebhookInput(e.target.value)}
            onBlur={() => {
              const v = discordWebhookInput.trim()
              if (v) onSettingsChange({ phoneNotifyDiscordWebhookUrl: v })
            }}
            placeholder={
              settings.phoneNotifyDiscordWebhookConfigured
                ? '•••••••• (leave blank to keep)'
                : 'https://discord.com/api/webhooks/…'
            }
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1.5 font-mono text-[11px] text-white focus:outline-none"
          />
        </label>
        <div className="space-y-1 pl-0.5">
          {(
            [
              ['phoneNotifyOnNeedsUser', 'Needs User (needs your answer)', true],
              ['phoneNotifyOnNeedsPo', 'Needs PO', false],
              ['phoneNotifyOnStuckEscalation', 'Stuck escalation → Needs PO', true],
              ['phoneNotifyOnStepTimeout', 'Agent step timeout', true],
              ['phoneNotifyOnBackupArmed', 'Backup model armed', true],
              ['phoneNotifyOnToolApproval', 'Tool approval pending', true],
              ['phoneNotifyOnSprintEnd', 'Sprint end summary', true],
              ['phoneNotifyOnBoardStatus', 'Board status after each sprint step', true],
            ] as const
          ).map(([key, label, defaultOn]) => (
            <label
              key={key}
              className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer"
            >
              <input
                type="checkbox"
                checked={(settings[key] as boolean | undefined) ?? defaultOn}
                onChange={(e) => onSettingsChange({ [key]: e.target.checked })}
              />
              {label}
            </label>
          ))}
        </div>
        <button
          type="button"
          disabled={phoneNotifyTesting || !(settings.phoneNotifyEnabled ?? false)}
          onClick={() => {
            setPhoneNotifyTesting(true)
            setPhoneNotifyStatus(null)
            const pendingUrl = discordWebhookInput.trim()
            void (async () => {
              if (pendingUrl) {
                onSettingsChange({ phoneNotifyDiscordWebhookUrl: pendingUrl })
              }
              try {
                const res = await testPhoneNotify(
                  pendingUrl ? { phoneNotifyDiscordWebhookUrl: pendingUrl } : undefined,
                )
                if (res.ok) setPhoneNotifyStatus('Test message sent — check Discord on your phone.')
                else setPhoneNotifyStatus(res.error || res.skipped || 'Failed')
              } catch (e) {
                setPhoneNotifyStatus(e instanceof Error ? e.message : 'Test failed')
              } finally {
                setPhoneNotifyTesting(false)
              }
            })()
          }}
          className="text-[10px] px-2.5 py-1 rounded border border-indigo-500/40 text-indigo-200 hover:bg-indigo-950/40 disabled:opacity-40"
        >
          {phoneNotifyTesting ? 'Sending…' : 'Send test'}
        </button>
        {phoneNotifyStatus && (
          <p className="text-[10px] text-violet-300 leading-relaxed">{phoneNotifyStatus}</p>
        )}

        <p className="text-[10px] font-semibold text-cat-subtext pt-2 border-t border-cat-surface1">
          Discord control bot (optional, localhost)
        </p>
        <p className="text-[10px] text-cat-overlay leading-relaxed">
          Gateway outbound on this PC — fixed slash commands only (/ah-status, /ah-pause, /ah-resume,
          /ah-cancel, /ah-backup-dev, /ah-model, /ah-feature). No free-form agent chat. Allowlist
          required. Restart or save settings to apply.
        </p>
        <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
          <input
            type="checkbox"
            checked={settings.discordBotEnabled ?? false}
            onChange={(e) => onSettingsChange({ discordBotEnabled: e.target.checked })}
          />
          Enable Discord control bot
          <SettingHint hint="Runs a local Discord bot (outbound only) so you can use fixed /ah-* slash commands from your phone." />
        </label>
        <p className="text-[10px] text-cat-overlay" data-testid="discord-bot-status">
          Bot status:{' '}
          <span className="text-cat-subtext">
            {!settings.discordBotEnabled
              ? 'off'
              : discordBotStatus?.status === 'ready' && discordBotStatus?.running
                ? `connected${discordBotStatus.readyAt ? ` · ${discordBotStatus.readyAt}` : ''}`
                : discordBotStatus?.status === 'ready' && !discordBotStatus?.running
                  ? 'status ready but task not running — watchdog should restart'
                : discordBotStatus?.status === 'error'
                  ? `error${discordBotStatus.lastError ? `: ${discordBotStatus.lastError}` : ''}`
                  : discordBotStatus?.status === 'connecting'
                    ? `connecting…${discordBotStatus.lastError ? ` (${discordBotStatus.lastError})` : ''}`
                    : settings.discordBotTokenConfigured
                      ? `not connected (${discordBotStatus?.status || 'idle'}${
                          discordBotStatus?.running ? ', task running' : ', task stopped'
                        }) — check allowlist / save settings to reload`
                      : 'enabled — token missing'}
          </span>
        </p>
        <p className="text-[10px] text-cat-overlay leading-relaxed">
          Token saved ≠ connected. Allowlist must include your Discord user ID. Status shows Gateway
          health; a watchdog restarts a dead bot task while enabled.
        </p>
        <label className="text-[11px] text-cat-subtext block">
          <span className="text-[10px] text-cat-overlay block">
            Bot token
            {settings.discordBotTokenConfigured ? ' (saved — leave blank to keep)' : ''}
          </span>
          <input
            type="password"
            autoComplete="off"
            value={discordBotTokenInput}
            onChange={(e) => setDiscordBotTokenInput(e.target.value)}
            onBlur={() => {
              const v = discordBotTokenInput.trim()
              if (v) onSettingsChange({ discordBotToken: v })
            }}
            placeholder={
              settings.discordBotTokenConfigured
                ? '•••••••• (leave blank to keep)'
                : 'Discord application bot token'
            }
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1.5 font-mono text-[11px] text-white focus:outline-none"
          />
        </label>
        <label className="text-[11px] text-cat-subtext block">
          <span className="text-[10px] text-cat-overlay inline-flex items-center">
            Guild ID (optional — faster slash sync)
            <SettingHint hint="Your Discord server ID. Set this so /ah-* commands appear in autocomplete within seconds. Without it, Discord global sync can take a long time." />
          </span>
          <input
            type="text"
            value={settings.discordBotGuildId ?? ''}
            onChange={(e) => onSettingsChange({ discordBotGuildId: e.target.value.trim() })}
            placeholder="123456789012345678"
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1.5 font-mono text-[11px] text-white focus:outline-none"
          />
        </label>
        <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1">
          After save + connected status, type <span className="font-mono">/ah</span> in that server
          to see slash autocomplete.
        </p>
        <label className="text-[11px] text-cat-subtext block">
          <span className="text-[10px] text-cat-overlay block">
            Allowed Discord user IDs (one per line — required)
          </span>
          <textarea
            rows={3}
            value={discordAllowedUsersText}
            onChange={(e) => setDiscordAllowedUsersText(e.target.value)}
            onBlur={() => {
              const ids = discordAllowedUsersText
                .split(/[\n,]+/)
                .map((s) => s.trim())
                .filter(Boolean)
              onSettingsChange({ discordBotAllowedUserIds: ids })
            }}
            placeholder={'123456789012345678\n987654321098765432'}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1.5 font-mono text-[11px] text-white focus:outline-none"
          />
        </label>
        <label className="text-[11px] text-cat-subtext block">
          <span className="text-[10px] text-cat-overlay block">Model preset: fast</span>
          <input
            type="text"
            value={settings.discordModelPresetFast ?? 'qwen2.5-coder:7b'}
            onChange={(e) => onSettingsChange({ discordModelPresetFast: e.target.value })}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1.5 font-mono text-[11px] text-white focus:outline-none"
          />
        </label>
        <label className="text-[11px] text-cat-subtext block">
          <span className="text-[10px] text-cat-overlay block">Model preset: quality</span>
          <input
            type="text"
            value={settings.discordModelPresetQuality ?? 'qwen2.5-coder:14b'}
            onChange={(e) => onSettingsChange({ discordModelPresetQuality: e.target.value })}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1.5 font-mono text-[11px] text-white focus:outline-none"
          />
        </label>
      </details>
      )}

      {(workflowTab === 'autonomy') && (
      <>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.pauseSprintOnNeedsUser ?? false}
          onChange={(e) => onSettingsChange({ pauseSprintOnNeedsUser: e.target.checked })}
        />
        Pause sprint when any card is in Needs User
        <SettingHint hint="When on, the auto-sprint pauses until you answer Needs User cards. When off, other work continues." />
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1 pl-5">
        Off by default — sprint continues other lanes while cards wait for your input.
      </p>

      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.autoStartSprint !== false}
          onChange={(e) => onSettingsChange({ autoStartSprint: e.target.checked })}
        />
        Auto-start sprint after plan (Plan &amp; Run)
        <SettingHint hint="After planning creates backlog cards, automatically begin running the sprint." />
      </label>

      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.autoFormatAfterEdit !== false}
          onChange={(e) => onSettingsChange({ autoFormatAfterEdit: e.target.checked })}
        />
        Auto-format Dart files after edits (dart format)
        <SettingHint hint="Runs dart format on Dart files the agent just changed." />
      </label>

      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.autonomousMode ?? false}
          onChange={(e) => onSettingsChange({ autonomousMode: e.target.checked })}
        />
        Autonomous sprint mode (minimal user input)
        <SettingHint hint="Agents prefer acting over asking. Limits how many Needs User cards can appear per sprint." />
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
          checked={settings.confirmSimulationFallback !== false}
          onChange={(e) => onSettingsChange({ confirmSimulationFallback: e.target.checked })}
        />
        Confirm before offline simulation
        <SettingHint hint="When Ollama is down, show a countdown popup before applying simulated dev/PO/QA results." />
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext">
        <span className="shrink-0">Simulation confirm seconds</span>
        <input
          type="number"
          min={1}
          max={60}
          className="w-16 rounded bg-cat-crust border border-cat-surface1 px-1 py-0.5 text-xs"
          value={settings.simulationConfirmSeconds ?? 10}
          onChange={(e) =>
            onSettingsChange({
              simulationConfirmSeconds: Math.min(60, Math.max(1, Number(e.target.value) || 10)),
            })
          }
        />
        <SettingHint hint="Auto-accept simulated result after this many seconds (1–60) when auto-accept is enabled." />
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.simulationAutoAccept === true}
          onChange={(e) => onSettingsChange({ simulationAutoAccept: e.target.checked })}
        />
        Auto-accept offline simulation after countdown
        <SettingHint hint="When off, the sprint waits until you confirm or choose Continue with new value." />
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.simulationAutoUseExistingFile !== false}
          onChange={(e) => onSettingsChange({ simulationAutoUseExistingFile: e.target.checked })}
        />
        Auto-use existing workspace file when Ollama is offline (dev)
        <SettingHint hint="Skips the popup and advances the card using the file on disk when found." />
      </label>

      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.enableWebSearch ?? false}
          onChange={(e) => onSettingsChange({ enableWebSearch: e.target.checked })}
        />
        Enable web search tool for agents
        <SettingHint hint="Lets agents look things up on the web (DuckDuckGo locally, or Serper if you set an API key)." />
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1 pl-5">
        Uses DuckDuckGo HTML search locally, or set{' '}
        <span className="font-mono">WEB_SEARCH_API_KEY</span> for Serper.
      </p>
      </>
      )}

      {(workflowTab === 'rag') && (
      <>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.enableSemanticSearch ?? true}
          onChange={(e) => onSettingsChange({ enableSemanticSearch: e.target.checked })}
        />
        Enable semantic codebase search (Qdrant)
        <SettingHint hint="Indexes your code so agents can find relevant files by meaning, not just exact text. Needs Qdrant + an embed model." />
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
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer pl-5">
        <input
          type="checkbox"
          checked={settings.enableFocusMicroSteps !== false}
          onChange={(e) => onSettingsChange({ enableFocusMicroSteps: e.target.checked })}
        />
        Dev focus micro-steps (one AC per sprint tick when ≥2 AC)
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer pl-5">
        <input
          type="checkbox"
          checked={settings.enablePromptSectionRotation !== false}
          onChange={(e) => onSettingsChange({ enablePromptSectionRotation: e.target.checked })}
        />
        Rotate prompt section bundles each LLM iteration (within a step)
      </label>
      <div className="grid grid-cols-2 gap-2 pl-5 text-[11px]">
        <label>
          <span className="text-[10px] text-cat-overlay">Max focus steps / card</span>
          <input
            type="number"
            min={1}
            max={32}
            value={settings.maxFocusStepsPerCard ?? 8}
            onChange={(e) =>
              onSettingsChange({ maxFocusStepsPerCard: Number(e.target.value) || 8 })
            }
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white font-mono text-[10px]"
          />
        </label>
        <label>
          <span className="text-[10px] text-cat-overlay">PO split hint when AC &gt;</span>
          <input
            type="number"
            min={3}
            max={20}
            value={settings.splitCardWhenAcOver ?? 5}
            onChange={(e) =>
              onSettingsChange({ splitCardWhenAcOver: Number(e.target.value) || 5 })
            }
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white font-mono text-[10px]"
          />
        </label>
      </div>
      <label className="flex flex-col gap-1 text-[11px] text-cat-subtext pl-5">
        <span className="text-[10px] text-cat-overlay inline-flex items-center gap-1">
          Codebase packer (Repomix / code2prompt)
          <SettingHint hint="Install a CLI on PATH, then enable a packer. Repomix: npm install -g repomix (Node ≥22). code2prompt: cargo install code2prompt or brew install code2prompt. See README § Installing Repomix or code2prompt. On failure, Dev steps still run without the pack section." />
        </span>
        <select
          value={settings.contextPacker ?? 'off'}
          onChange={(e) => onSettingsChange({ contextPacker: e.target.value })}
          className="bg-cat-base border border-cat-surface1 rounded p-1 text-white font-mono text-[10px]"
        >
          <option value="off">off (default)</option>
          <option value="repomix">repomix</option>
          <option value="code2prompt">code2prompt</option>
        </select>
        {settings.contextPacker && settings.contextPacker !== 'off' && (
          <p className="text-[10px] text-cat-overlay leading-snug">
            Install:{' '}
            {settings.contextPacker === 'repomix' ? (
              <>
                <code className="text-cat-subtext">npm install -g repomix</code> —{' '}
                <a
                  href="https://repomix.com/guide/installation"
                  target="_blank"
                  rel="noreferrer"
                  className="text-indigo-300 hover:underline"
                >
                  Repomix docs
                </a>
              </>
            ) : (
              <>
                <code className="text-cat-subtext">cargo install code2prompt</code> or{' '}
                <code className="text-cat-subtext">brew install code2prompt</code> —{' '}
                <a
                  href="https://code2prompt.dev/docs/how_to/install/"
                  target="_blank"
                  rel="noreferrer"
                  className="text-indigo-300 hover:underline"
                >
                  code2prompt install
                </a>
              </>
            )}
            . CLI must be on PATH (or set command override in saved workflow JSON).
          </p>
        )}
      </label>
      {(settings.contextPacker === 'repomix' || settings.contextPacker === 'code2prompt') && (
        <div className="grid grid-cols-1 gap-2 pl-5 text-[11px]">
          {settings.contextPacker === 'repomix' && (
            <label>
              <span className="text-[10px] text-cat-overlay">repomixCommand</span>
              <input
                type="text"
                value={settings.repomixCommand ?? 'repomix'}
                onChange={(e) => onSettingsChange({ repomixCommand: e.target.value || 'repomix' })}
                placeholder="repomix"
                className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white font-mono text-[10px]"
              />
            </label>
          )}
          {settings.contextPacker === 'code2prompt' && (
            <label>
              <span className="text-[10px] text-cat-overlay">code2promptCommand</span>
              <input
                type="text"
                value={settings.code2promptCommand ?? 'code2prompt'}
                onChange={(e) =>
                  onSettingsChange({ code2promptCommand: e.target.value || 'code2prompt' })
                }
                placeholder="code2prompt"
                className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white font-mono text-[10px]"
              />
            </label>
          )}
          <label>
            <span className="text-[10px] text-cat-overlay">contextPackerMaxChars</span>
            <input
              type="number"
              min={2000}
              max={100000}
              value={settings.contextPackerMaxChars ?? 12000}
              onChange={(e) =>
                onSettingsChange({ contextPackerMaxChars: Number(e.target.value) || 12000 })
              }
              className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white font-mono text-[10px]"
            />
          </label>
        </div>
      )}
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer pl-5">
        <span className="text-[10px] text-cat-overlay">Sprint file inject</span>
        <select
          value={settings.sprintFileContextMode === 'full' ? 'full' : 'excerpt'}
          onChange={(e) =>
            onSettingsChange({
              sprintFileContextMode: e.target.value === 'full' ? 'full' : 'excerpt',
            })
          }
          className="bg-cat-base border border-cat-surface1 rounded p-1 text-white font-mono text-[10px]"
        >
          <option value="excerpt">excerpts (default)</option>
          <option value="full">full file bodies</option>
        </select>
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer pl-5">
        <input
          type="checkbox"
          checked={settings.enableHybridSearch !== false}
          onChange={(e) => onSettingsChange({ enableHybridSearch: e.target.checked })}
        />
        Hybrid search (dense + lexical RRF)
      </label>
      <div className="grid grid-cols-2 gap-2 pl-5 text-[11px]">
        <label>
          <span className="text-[10px] text-cat-overlay inline-flex items-center">
            Min dense score (0–1)
            <SettingHint hint="Ignore weak code-search matches below this score. Raise if context feels noisy." />
          </span>
          <input
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={settings.semanticMinScore ?? 0.35}
            onChange={(e) => {
              const v = parseFloat(e.target.value)
              if (!Number.isNaN(v)) onSettingsChange({ semanticMinScore: v })
            }}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
          />
        </label>
        <label>
          <span className="text-[10px] text-cat-overlay block">semanticSprintTopK</span>
          <NumberSettingInput
            value={settings.semanticSprintTopK ?? 5}
            min={1}
            max={20}
            onCommit={(semanticSprintTopK) => onSettingsChange({ semanticSprintTopK })}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
          />
        </label>
      </div>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer pl-5">
        <input
          type="checkbox"
          checked={settings.enableAgentStepRecap !== false}
          onChange={(e) => onSettingsChange({ enableAgentStepRecap: e.target.checked })}
        />
        Step recap for local models (goal, tool intent, dedupe list)
        <SettingHint hint="After each tool batch, injects STEP RECAP with AC reminder, why tools ran, do-not-repeat list, and suggested next tool. Helps weaker Ollama models avoid duplicate calls and onboarding text." />
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer pl-5">
        <input
          type="checkbox"
          checked={settings.enableObservationSummaries !== false}
          onChange={(e) => onSettingsChange({ enableObservationSummaries: e.target.checked })}
        />
        Observation summaries after tool batches
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer pl-5">
        <input
          type="checkbox"
          checked={settings.enableEpisodeSummary !== false}
          onChange={(e) => onSettingsChange({ enableEpisodeSummary: e.target.checked })}
        />
        Episode summary when pruning context
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer pl-5">
        <input
          type="checkbox"
          checked={settings.enableStepLessonMemory !== false}
          onChange={(e) => onSettingsChange({ enableStepLessonMemory: e.target.checked })}
        />
        Save end-of-step lesson to memory
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer pl-5">
        <input
          type="checkbox"
          checked={settings.enableLlmContextCompress === true}
          onChange={(e) => onSettingsChange({ enableLlmContextCompress: e.target.checked })}
        />
        LLM compress bulky sprint context (off by default — extra Ollama call per step)
      </label>
      {settings.enableLlmContextCompress === true && (
        <div className="grid grid-cols-2 gap-2 pl-5 text-[10px] text-cat-overlay">
          <label>
            Compress when inject ≥
            <input
              type="number"
              min={2000}
              max={100000}
              value={settings.contextCompressMinChars ?? 8000}
              onChange={(e) =>
                onSettingsChange({ contextCompressMinChars: Number(e.target.value) || 8000 })
              }
              className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white font-mono"
            />
            chars
          </label>
          <label>
            Target max
            <input
              type="number"
              min={500}
              max={20000}
              value={settings.contextCompressMaxChars ?? 3500}
              onChange={(e) =>
                onSettingsChange({ contextCompressMaxChars: Number(e.target.value) || 3500 })
              }
              className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white font-mono"
            />
            chars
          </label>
          <label className="col-span-2">
            Compress model (empty = the step agent's own model)
            <input
              type="text"
              value={settings.contextCompressModel ?? ''}
              onChange={(e) => onSettingsChange({ contextCompressModel: e.target.value })}
              className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white font-mono"
              placeholder="step agent model"
            />
          </label>
        </div>
      )}
      <p className="text-[10px] text-cat-overlay leading-relaxed pl-5">
        Fine-tuning is not run in AllHands. Export step traces as JSONL for offline SFT.
      </p>
      <div className="flex flex-wrap items-center gap-2 pl-5" data-testid="training-export">
        <label className="text-[10px] text-cat-overlay flex items-center gap-1">
          Limit
          <input
            type="number"
            min={1}
            max={500}
            value={trainingExportLimit}
            onChange={(e) => setTrainingExportLimit(Number(e.target.value) || 50)}
            className="w-16 bg-cat-base border border-cat-surface1 rounded px-1 py-0.5 text-white font-mono"
          />
        </label>
        <button
          type="button"
          disabled={trainingExporting}
          onClick={() => {
            setTrainingExporting(true)
            setTrainingExportStatus(null)
            void (async () => {
              try {
                const res = await exportTrainingJsonl(trainingExportLimit)
                const blob = new Blob([res.jsonl || ''], { type: 'application/x-ndjson' })
                const url = URL.createObjectURL(blob)
                const a = document.createElement('a')
                a.href = url
                a.download = `allhands-training-${res.projectId || 'export'}.jsonl`
                a.click()
                URL.revokeObjectURL(url)
                setTrainingExportStatus(`Downloaded ${res.count} row(s).`)
              } catch (e) {
                setTrainingExportStatus(e instanceof Error ? e.message : 'Export failed')
              } finally {
                setTrainingExporting(false)
              }
            })()
          }}
          className="text-[10px] px-2.5 py-1 rounded border border-indigo-500/40 text-indigo-200 hover:bg-indigo-950/40 disabled:opacity-40"
        >
          {trainingExporting ? 'Exporting…' : 'Download JSONL'}
        </button>
        {trainingExportStatus && (
          <span className="text-[10px] text-violet-300">{trainingExportStatus}</span>
        )}
      </div>
      </>
      )}

      {(workflowTab === 'autonomy') && (
      <>
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
        <span className="text-[10px] text-cat-overlay inline-flex items-center">
          Ollama keep-alive
          <SettingHint hint="How long Ollama keeps a model loaded in memory between calls (for example 30m). Longer = less reload delay." />
        </span>
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

      <p className="text-[10px] text-cat-overlay leading-relaxed">
        When conversation exceeds the prune threshold (top of Workflow), oldest tool messages are
        dropped before each LLM call.
      </p>

      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay inline-flex items-center">
          Ollama context size (num_ctx)
          <SettingHint hint="How many tokens of conversation the model can hold. Higher uses more RAM/VRAM and can be slower." />
        </span>
        <NumberSettingInput
          value={settings.ollamaNumCtx ?? 32768}
          min={4096}
          max={131072}
          onCommit={(ollamaNumCtx) => onSettingsChange({ ollamaNumCtx })}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
        />
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1">
        Global default. Dev uses this; PO/CR/QA default to min(global, 16384) unless overridden below.
        Increase if you see exceed_context_size_error. Higher values use more RAM/VRAM.
      </p>
      {(settings.ollamaNumCtx ?? 32768) > 16384 && (
        <p className="text-[10px] text-amber-300 leading-relaxed -mt-1">
          Warning: num_ctx is high ({settings.ollamaNumCtx}). Large context is a common cause of
          multi-minute Ollama waits.
        </p>
      )}
      <div className="grid grid-cols-2 gap-2 text-[11px] pl-0.5">
        {(['po', 'dev', 'cr', 'qa'] as const).map((role) => (
          <label key={role}>
            <span className="text-[10px] text-cat-overlay block">num_ctx {role}</span>
            <NumberSettingInput
              value={settings.ollamaNumCtxByRole?.[role] ?? 0}
              min={0}
              max={131072}
              onCommit={(v) => {
                const next = { ...(settings.ollamaNumCtxByRole ?? {}) }
                if (!v) {
                  delete next[role]
                } else {
                  next[role] = v
                }
                onSettingsChange({ ollamaNumCtxByRole: next })
              }}
              className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
            />
          </label>
        ))}
      </div>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1">
        Per-role override (0 = use default). Optional.
      </p>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.ollamaNumCtxAuto ?? false}
          onChange={(e) => onSettingsChange({ ollamaNumCtxAuto: e.target.checked })}
        />
        Auto-clamp Dev num_ctx on low/minimal VRAM (halve)
      </label>
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.ollamaNumCtxAdaptive ?? false}
          onChange={(e) => onSettingsChange({ ollamaNumCtxAdaptive: e.target.checked })}
        />
        Adaptive context (start low, auto-increase on overflow)
        <SettingHint hint="Each sprint step begins at the start size below (capped by global/role num_ctx). If Ollama returns exceed_context_size, num_ctx is increased and the call is retried until the ceiling is reached." />
      </label>
      {settings.ollamaNumCtxAdaptive && (
        <div className="grid grid-cols-2 gap-2 text-[11px] pl-0.5">
          <label>
            <span className="text-[10px] text-cat-overlay block">Adaptive start num_ctx</span>
            <NumberSettingInput
              value={settings.ollamaNumCtxAdaptiveStart ?? 8192}
              min={2048}
              max={131072}
              onCommit={(ollamaNumCtxAdaptiveStart) => onSettingsChange({ ollamaNumCtxAdaptiveStart })}
              className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
            />
          </label>
          <label>
            <span className="text-[10px] text-cat-overlay block">Adaptive bump step</span>
            <NumberSettingInput
              value={settings.ollamaNumCtxAdaptiveStep ?? 8192}
              min={1024}
              max={131072}
              onCommit={(ollamaNumCtxAdaptiveStep) => onSettingsChange({ ollamaNumCtxAdaptiveStep })}
              className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
            />
          </label>
        </div>
      )}
      <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
        <input
          type="checkbox"
          checked={settings.enableVramAwareModelSwap ?? true}
          onChange={(e) => onSettingsChange({ enableVramAwareModelSwap: e.target.checked })}
        />
        VRAM-aware model swap (unload primary before backup when GPU &gt;85% full)
      </label>

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
        <span className="text-[10px] text-cat-overlay block">Shell command timeout (seconds)</span>
        <NumberSettingInput
          value={settings.terminalTimeoutSec ?? 600}
          min={30}
          max={1800}
          onCommit={(terminalTimeoutSec) => onSettingsChange({ terminalTimeoutSec })}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
        />
      </label>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1">
        Floor for run_command. Long builds use remaining step time (up to 30 min).
      </p>
      <p className="text-[10px] text-cat-overlay leading-relaxed -mt-1">
        Default for agent <code className="text-cat-subtext">run_command</code>. Long builds
        (build_runner, flutter build, dotnet build, npm run build, …) use at least 600s.
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

      <label className="text-[11px] text-cat-subtext block">
        <span className="text-[10px] text-cat-overlay block">
          Ollama retry delays (seconds, comma-separated)
        </span>
        <input
          type="text"
          value={retryDelayText}
          onChange={(e) => setRetryDelayText(e.target.value)}
          onBlur={() => {
            const parts = retryDelayText
              .split(/[,\s]+/)
              .map((p) => p.trim())
              .filter(Boolean)
              .map((p) => Number(p))
              .filter((n) => Number.isFinite(n) && n >= 0)
            if (parts.length === 0) {
              setRetryDelayText((settings.ollamaRetryDelaySec ?? [0, 2, 5, 10]).join(', '))
              return
            }
            onSettingsChange({ ollamaRetryDelaySec: parts })
            setRetryDelayText(parts.join(', '))
          }}
          className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white font-mono text-[11px]"
          placeholder="0, 2, 5, 10"
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
          <span className="text-[10px] text-cat-overlay inline-flex items-center">
            Max sprint steps
            <SettingHint hint="Safety cap on how many board steps one auto-sprint can run before it stops." />
          </span>
          <NumberSettingInput
            value={settings.maxSprintSteps}
            min={1}
            max={100}
            onCommit={(maxSprintSteps) => onSettingsChange({ maxSprintSteps })}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
          />
        </label>
        <label>
          <span className="text-[10px] text-cat-overlay inline-flex items-center">
            Max LLM iter/step
            <SettingHint hint="How many think-then-tool rounds one agent may run in a single board step." />
          </span>
          <NumberSettingInput
            value={settings.maxLlmIterationsPerStep}
            min={1}
            max={20}
            onCommit={(maxLlmIterationsPerStep) => onSettingsChange({ maxLlmIterationsPerStep })}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
          />
        </label>
        <label className="col-span-3 flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer">
          <input
            type="checkbox"
            checked={settings.autoExtendOnMaxIter ?? true}
            onChange={(e) => onSettingsChange({ autoExtendOnMaxIter: e.target.checked })}
          />
          Auto-extend once on max iterations when progress detected (+
          <NumberSettingInput
            value={settings.autoExtendExtraIterations ?? 4}
            min={1}
            max={16}
            onCommit={(autoExtendExtraIterations) =>
              onSettingsChange({ autoExtendExtraIterations })
            }
            className="w-12 bg-cat-base border border-cat-surface1 rounded px-1 py-0.5 text-cat-text mx-1"
          />
          iters)
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
        <label>
          <span className="text-[10px] text-cat-overlay block">Max stuck steps</span>
          <NumberSettingInput
            value={settings.maxStuckSteps ?? 3}
            min={1}
            max={20}
            onCommit={(maxStuckSteps) => onSettingsChange({ maxStuckSteps })}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
          />
        </label>
        <label>
          <span className="text-[10px] text-cat-overlay block">Max agent step duration (sec)</span>
          <NumberSettingInput
            value={settings.maxAgentStepDurationSec ?? 2700}
            min={60}
            max={28800}
            onCommit={(maxAgentStepDurationSec) => onSettingsChange({ maxAgentStepDurationSec })}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
          />
        </label>
        <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer col-span-2">
          <input
            type="checkbox"
            checked={settings.enableBackupModelOnStuck ?? true}
            onChange={(e) => onSettingsChange({ enableBackupModelOnStuck: e.target.checked })}
          />
          Use backup model when agent is stuck (next N steps)
        </label>
        <label>
          <span className="text-[10px] text-cat-overlay block">Backup model stuck steps</span>
          <NumberSettingInput
            value={settings.backupModelStuckSteps ?? 2}
            min={1}
            max={5}
            onCommit={(backupModelStuckSteps) => onSettingsChange({ backupModelStuckSteps })}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
          />
        </label>
        <label className="flex items-center gap-2 text-[11px] text-cat-subtext cursor-pointer col-span-3">
          <input
            type="checkbox"
            checked={settings.enableSplitOnStuck !== false}
            onChange={(e) => onSettingsChange({ enableSplitOnStuck: e.target.checked })}
          />
          Auto-split card before Needs PO (after backup attempts fail)
        </label>
        <label>
          <span className="text-[10px] text-cat-overlay block">Max tool failures/step</span>
          <NumberSettingInput
            value={settings.maxToolFailuresPerStep ?? 5}
            min={1}
            max={50}
            onCommit={(maxToolFailuresPerStep) => onSettingsChange({ maxToolFailuresPerStep })}
            className="w-full bg-cat-base border border-cat-surface1 rounded p-1 text-white"
          />
        </label>
      </div>
      </>
      )}

      {(workflowTab === 'gates') && (
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
      )}

      {(workflowTab === 'rag') && (
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
      )}

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
