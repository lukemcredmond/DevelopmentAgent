import { useEffect, useState } from 'react'
import { checkOllamaHealth } from '../api/client'

interface InstalledModelsPanelProps {
  ollamaUrl: string
  onPickModel: (model: string) => void
  focusedRole?: string
  provider?: string
}

export default function InstalledModelsPanel({
  ollamaUrl,
  onPickModel,
  focusedRole,
  provider = 'ollama',
}: InstalledModelsPanelProps) {
  const [models, setModels] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

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

  return (
    <div className="space-y-1.5 border border-cat-surface1 rounded-lg p-2.5 bg-cat-base/40">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[10px] font-bold uppercase tracking-wider text-cat-subtext">
          {provider === 'ollama' ? 'Installed models' : 'Loaded models'}
        </h3>
        {focusedRole && (
          <span className="text-[9px] text-indigo-300">Click assigns → {focusedRole}</span>
        )}
      </div>
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
