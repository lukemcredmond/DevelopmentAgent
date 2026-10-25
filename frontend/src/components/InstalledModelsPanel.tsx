import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  cancelModelBenchmark,
  checkOllamaHealth,
  exportModelBenchmark,
  fetchModelBenchmarkBackups,
  fetchModelBenchmarkStatus,
  importModelBenchmark,
  restoreModelBenchmarkBackup,
  startModelBenchmark,
} from '../api/client'
import type { ModelBenchmarkBackupFile, ModelBenchmarkJob, ModelBenchmarkRow } from '../types'
import { CodeAbilityIcon, ToolAbilityIcon } from './ModelAbilityIcons'

interface InstalledModelsPanelProps {
  ollamaUrl: string
  onPickModel: (model: string) => void
  focusedRole?: string
  provider?: string
  sprintRunning?: boolean
  onApplyDevModel?: (model: string) => void
  /** Configured Dev primary — used for best-model mismatch banner. */
  currentDevModel?: string
}

function phaseLabel(job: ModelBenchmarkJob): string {
  const n = (job.completed ?? 0) + (job.currentModel ? 1 : 0)
  const total = job.total ?? 0
  const model = job.currentModel ?? '…'
  if (job.phase === 'loading') return `Model ${n}/${total} · ${model} · Loading`
  if (job.phase === 'tools') return `Model ${n}/${total} · ${model} · Tools`
  if (job.phase === 'coding') {
    const i = job.codingTaskIndex ?? 0
    const t = job.codingTaskTotal ?? 8
    return `Model ${n}/${total} · ${model} · Coding (${i}/${t})`
  }
  if (job.status === 'running') return `Model ${job.completed ?? 0}/${total} · starting…`
  return ''
}

function rowTooltip(row: ModelBenchmarkRow): string {
  const ts = row.toolSummary
  const tools = ts
    ? `Tools ${row.toolScore ?? '—'}/100 (${ts.pass ?? 0}/${(ts.pass ?? 0) + (ts.fail ?? 0)} probes)`
    : `Tools ${row.toolScore ?? '—'}`
  const code = `Code ${row.codeScore ?? '—'}/100 (${Math.round((row.codePassRate ?? 0) * 8)}/8 tasks, native ${(row.nativeToolCallRate ?? 0).toFixed(2)})`
  return `${tools} · ${code}`
}

export default function InstalledModelsPanel({
  ollamaUrl,
  onPickModel,
  focusedRole,
  provider = 'ollama',
  sprintRunning = false,
  onApplyDevModel,
  currentDevModel = '',
}: InstalledModelsPanelProps) {
  const [models, setModels] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [benchmark, setBenchmark] = useState<ModelBenchmarkJob | null>(null)
  const [benchmarkError, setBenchmarkError] = useState<string | null>(null)
  const [backups, setBackups] = useState<ModelBenchmarkBackupFile[]>([])
  const [selectedBackup, setSelectedBackup] = useState('')
  const importInputRef = useRef<HTMLInputElement>(null)

  const refreshBenchmark = useCallback(() => {
    void fetchModelBenchmarkStatus()
      .then((data) => setBenchmark(data))
      .catch(() => {})
  }, [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    void checkOllamaHealth(ollamaUrl)
      .then((res) => {
        if (cancelled) return
        if (res.ok) {
          setModels(Array.isArray(res.models) ? res.models.filter(Boolean) : [])
          setError(null)
        } else {
          setModels([])
          setError(res.error || 'LLM server unreachable')
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setModels([])
        setError(err instanceof Error ? err.message : 'Failed to list models')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [ollamaUrl])

  useEffect(() => {
    refreshBenchmark()
  }, [refreshBenchmark])

  const refreshBackups = useCallback(() => {
    void fetchModelBenchmarkBackups(ollamaUrl)
      .then((data) => {
        const list = data.backups ?? []
        setBackups(list)
        setSelectedBackup((prev) => prev || list[0]?.filename || '')
      })
      .catch(() => setBackups([]))
  }, [ollamaUrl])

  useEffect(() => {
    if (benchmark?.status === 'running') return
    refreshBackups()
  }, [benchmark?.status, refreshBackups])

  useEffect(() => {
    if (benchmark?.status !== 'running') return
    const id = window.setInterval(() => refreshBenchmark(), 2000)
    return () => window.clearInterval(id)
  }, [benchmark?.status, refreshBenchmark])

  const sortedRows = useMemo(() => {
    const rows = [...(benchmark?.results ?? [])]
    rows.sort((a, b) => (b.overallScore ?? -1) - (a.overallScore ?? -1))
    return rows
  }, [benchmark?.results])

  const benchmarkRunning = benchmark?.status === 'running'
  const applyDev = onApplyDevModel ?? ((name: string) => onPickModel(name))
  const devFocus = focusedRole?.toLowerCase() === 'dev'
  const bestDev = benchmark?.bestModel?.trim() || ''
  const configuredDev = currentDevModel.trim()
  const showBestDevBanner =
    devFocus && bestDev && configuredDev && bestDev !== configuredDev && !benchmarkRunning

  const runBenchmark = () => {
    if (
      benchmark?.canResume &&
      !window.confirm(
        'Run full benchmark re-tests every installed model, including ones already scored. Use Resume to continue a partial run.',
      )
    ) {
      return
    }
    setBenchmarkError(null)
    void startModelBenchmark({ url: ollamaUrl, agent: 'dev', resume: false })
      .then((data) => setBenchmark(data))
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : 'Benchmark failed to start'
        setBenchmarkError(msg)
      })
  }

  const resumeBenchmark = () => {
    setBenchmarkError(null)
    void startModelBenchmark({ url: ollamaUrl, agent: 'dev', resume: true })
      .then((data) => setBenchmark(data))
      .catch((e: unknown) => {
        const msg = e instanceof Error ? e.message : 'Benchmark failed to resume'
        setBenchmarkError(msg)
      })
  }

  const exportBenchmark = () => {
    setBenchmarkError(null)
    void exportModelBenchmark(ollamaUrl)
      .then((blob) => {
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `benchmark-export-${new Date().toISOString().slice(0, 10)}.json`
        a.click()
        URL.revokeObjectURL(url)
      })
      .catch((e: unknown) => {
        setBenchmarkError(e instanceof Error ? e.message : 'Export failed')
      })
  }

  const onImportFile = (file: File | undefined) => {
    if (!file) return
    setBenchmarkError(null)
    void file
      .text()
      .then((text) => {
        const snapshot = JSON.parse(text) as Record<string, unknown>
        return importModelBenchmark({ url: ollamaUrl, snapshot })
      })
      .then((data) => {
        setBenchmark(data)
        refreshBackups()
      })
      .catch((e: unknown) => {
        setBenchmarkError(e instanceof Error ? e.message : 'Import failed')
      })
      .finally(() => {
        if (importInputRef.current) importInputRef.current.value = ''
      })
  }

  const restoreBackup = () => {
    if (!selectedBackup) return
    if (!window.confirm(`Restore auto-backup ${selectedBackup}? This replaces saved scores.`)) return
    setBenchmarkError(null)
    void restoreModelBenchmarkBackup({ url: ollamaUrl, filename: selectedBackup })
      .then((data) => {
        setBenchmark(data)
        refreshBackups()
      })
      .catch((e: unknown) => {
        setBenchmarkError(e instanceof Error ? e.message : 'Restore failed')
      })
  }

  return (
    <div className="space-y-2 border border-cat-surface1 rounded-lg p-2.5 bg-cat-base/40">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[10px] font-bold uppercase tracking-wider text-cat-subtext">
          {provider === 'ollama' ? 'Installed models' : 'Loaded models'}
        </h3>
        {focusedRole && (
          <span className="text-[9px] text-indigo-300">Click assigns → {focusedRole}</span>
        )}
      </div>

      <div className="flex flex-wrap gap-1.5">
        {benchmark?.canResume && !benchmarkRunning && (
          <button
            type="button"
            disabled={loading || sprintRunning || models.length === 0}
            onClick={resumeBenchmark}
            className="text-[10px] px-2 py-1 rounded border border-emerald-500/40 text-emerald-200 hover:bg-emerald-950/40 disabled:opacity-40"
            title="Continue partial benchmark; skips models already marked done"
          >
            Resume benchmark ({benchmark.remainingCount ?? 0} left)
          </button>
        )}
        <button
          type="button"
          disabled={loading || benchmarkRunning || sprintRunning || models.length === 0}
          onClick={runBenchmark}
          className="text-[10px] px-2 py-1 rounded border border-indigo-500/40 text-indigo-200 hover:bg-indigo-950/40 disabled:opacity-40"
          title="Load each model sequentially; full tool probes + 8 coding eval tasks (long run)"
        >
          {benchmarkRunning ? 'Benchmark running…' : 'Run full benchmark'}
        </button>
        {benchmarkRunning && (
          <button
            type="button"
            onClick={() => void cancelModelBenchmark().then(refreshBenchmark)}
            className="text-[10px] px-2 py-1 rounded border border-rose-500/30 text-rose-200 hover:bg-rose-950/30"
          >
            Cancel
          </button>
        )}
        <button
          type="button"
          disabled={benchmarkRunning}
          onClick={exportBenchmark}
          className="text-[10px] px-2 py-1 rounded border border-cat-surface1 text-cat-subtext hover:bg-cat-surface1/40 disabled:opacity-40"
          title="Download JSON backup of benchmark scores"
        >
          Export backup
        </button>
        <label className="text-[10px] px-2 py-1 rounded border border-cat-surface1 text-cat-subtext hover:bg-cat-surface1/40 cursor-pointer">
          Import backup
          <input
            ref={importInputRef}
            type="file"
            accept="application/json,.json"
            className="hidden"
            disabled={benchmarkRunning}
            onChange={(e) => onImportFile(e.target.files?.[0])}
          />
        </label>
      </div>
      {backups.length > 0 && !benchmarkRunning && (
        <div className="flex flex-wrap items-center gap-1.5">
          <select
            value={selectedBackup}
            onChange={(e) => setSelectedBackup(e.target.value)}
            className="text-[10px] max-w-[220px] rounded border border-cat-surface1 bg-cat-base text-cat-text px-1 py-0.5"
          >
            {backups.map((b) => (
              <option key={b.filename} value={b.filename}>
                {b.filename}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={restoreBackup}
            className="text-[10px] px-2 py-1 rounded border border-amber-500/40 text-amber-200 hover:bg-amber-950/30"
          >
            Restore auto-backup
          </button>
        </div>
      )}
      <p className="text-[10px] text-cat-overlay leading-relaxed">
        Scores persist per LLM URL in ~/.allhands and auto-backup to ~/.allhands/benchmark-backups/.
        Export after long runs.
      </p>
      {(sprintRunning && !benchmarkRunning) && (
        <p className="text-[10px] text-amber-300">Stop the sprint before starting a benchmark.</p>
      )}
      {benchmark?.canResume && !benchmarkRunning && (
        <p className="text-[10px] text-cat-overlay">
          Resume skips models already marked done
          {benchmark.doneCount != null && benchmark.total != null
            ? ` (e.g. ${benchmark.doneCount}/${benchmark.total}).`
            : '.'}
        </p>
      )}
      {benchmarkError && <p className="text-[10px] text-rose-300">{benchmarkError}</p>}
      {benchmarkRunning && benchmark && (
        <p className="text-[10px] font-mono text-cat-subtext">{phaseLabel(benchmark)}</p>
      )}
      {!benchmarkRunning && benchmark?.fromPersisted && benchmark.bestModel && (
        <p className="text-[10px] text-cat-overlay">
          Last run · best for dev: <span className="text-emerald-300 font-mono">{benchmark.bestModel}</span>
        </p>
      )}
      {showBestDevBanner && (
        <div className="text-[10px] rounded border border-amber-500/40 bg-amber-950/25 p-2 text-amber-100 leading-relaxed">
          Dev primary <span className="font-mono text-amber-200">{configuredDev}</span> differs from
          benchmark best <span className="font-mono text-emerald-300">{bestDev}</span>.
          <button
            type="button"
            className="ml-2 text-indigo-300 hover:underline"
            onClick={() => applyDev(bestDev)}
          >
            Apply best Dev
          </button>
        </div>
      )}

      {sortedRows.length > 0 && (
        <div className="overflow-x-auto max-h-40 overflow-y-auto border border-cat-surface1/60 rounded">
          <table className="w-full text-[10px]">
            <thead className="text-cat-overlay uppercase text-[9px] sticky top-0 bg-cat-base">
              <tr>
                <th className="text-left p-1 font-semibold">Model</th>
                <th className="p-1">Tools</th>
                <th className="p-1">Code</th>
                <th className="p-1">Native</th>
                <th className="p-1">Overall</th>
                <th className="p-1">Sec</th>
                <th className="p-1" />
              </tr>
            </thead>
            <tbody>
              {sortedRows.map((row) => {
                const isBest = row.model === benchmark?.bestModel && row.status === 'done'
                return (
                  <tr key={row.model} className="border-t border-cat-surface1/40 font-mono">
                    <td className="p-1 truncate max-w-[120px]" title={row.model}>
                      {isBest ? <span className="text-amber-300 mr-0.5">★</span> : null}
                      {row.model}
                    </td>
                    <td className="p-1 text-center">
                      <ToolAbilityIcon
                        score={row.toolScore}
                        tier={row.toolTier}
                        title={rowTooltip(row)}
                      />
                    </td>
                    <td className="p-1 text-center">
                      <CodeAbilityIcon
                        score={row.codeScore}
                        tier={row.codeTier}
                        title={rowTooltip(row)}
                      />
                    </td>
                    <td
                      className="p-1 text-center tabular-nums text-cat-overlay"
                      title="Native tool-call rate (coding eval)"
                    >
                      {row.nativeToolCallRate != null
                        ? `${Math.round(row.nativeToolCallRate * 100)}%`
                        : '—'}
                    </td>
                    <td className="p-1 text-center tabular-nums text-cat-text">
                      {row.overallScore ?? '—'}
                    </td>
                    <td className="p-1 text-center tabular-nums text-cat-overlay">
                      {row.wallClockSec ?? '—'}
                    </td>
                    <td className="p-1 text-right">
                      {row.status === 'done' && (
                        <button
                          type="button"
                          className="text-indigo-400 hover:underline text-[9px]"
                          onClick={() => applyDev(row.model)}
                        >
                          Dev
                        </button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {loading && <p className="text-[10px] text-cat-overlay">Loading from LLM server…</p>}
      {!loading && error && (
        <p className="text-[10px] text-rose-300 leading-relaxed">
          Could not list models: {error}. Check the server URL and that the LLM server is running.
        </p>
      )}
      {!loading && !error && models.length === 0 && (
        <p className="text-[10px] text-cat-overlay">
          {provider === 'ollama'
            ? 'No models installed yet. Use ollama pull …'
            : 'No model IDs returned by /v1/models. Load a model in LM Studio, then refresh settings.'}
        </p>
      )}
      {!loading && models.length > 0 && (
        <div className="flex flex-wrap gap-1 max-h-28 overflow-y-auto">
          {models.map((name) => (
            <button
              key={name}
              type="button"
              onClick={() => onPickModel(name)}
              className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-indigo-500/40 text-indigo-200 hover:bg-indigo-950/50 truncate max-w-full"
              title={`Assign ${name}`}
            >
              {name}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
