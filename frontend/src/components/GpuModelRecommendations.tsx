import { useEffect, useState } from 'react'
import { fetchModelRecommendations, fetchSystemCapacity } from '../api/client'

interface GpuModelRecommendationsProps {
  ollamaUrl: string
  poModel: string
  devModel: string
  crModel: string
  qaModel: string
  onPoModelChange: (v: string) => void
  onDevModelChange: (v: string) => void
  onCrModelChange: (v: string) => void
  onQaModelChange: (v: string) => void
}

export default function GpuModelRecommendations({
  ollamaUrl,
  poModel,
  devModel,
  crModel,
  qaModel,
  onPoModelChange,
  onDevModelChange,
  onCrModelChange,
  onQaModelChange,
}: GpuModelRecommendationsProps) {
  const [capacity, setCapacity] = useState<Record<string, unknown> | null>(null)
  const [roles, setRoles] = useState<Record<string, { model: string; status: string }> | null>(null)
  const [tier, setTier] = useState<string>('')

  useEffect(() => {
    void fetchSystemCapacity()
      .then((data) => setCapacity(data as Record<string, unknown>))
      .catch(() => setCapacity(null))
    void fetchModelRecommendations(ollamaUrl)
      .then((data) => {
        setRoles(data.roles ?? null)
        setTier(data.tier ?? '')
      })
      .catch(() => {
        setRoles(null)
        setTier('')
      })
  }, [ollamaUrl])

  if (!roles) return null

  const vramMb = capacity?.vramMb as number | null | undefined
  const label =
    vramMb != null
      ? `${Math.round(vramMb / 1024)} GB VRAM · ${tier} tier`
      : `CPU / RAM tier · ${tier}`

  const applyAll = () => {
    if (roles.po?.model) onPoModelChange(roles.po.model)
    if (roles.dev?.model) onDevModelChange(roles.dev.model)
    if (roles.cr?.model) onCrModelChange(roles.cr.model)
    if (roles.qa?.model) onQaModelChange(roles.qa.model)
  }

  const roleSetters: Record<string, (v: string) => void> = {
    po: onPoModelChange,
    dev: onDevModelChange,
    cr: onCrModelChange,
    qa: onQaModelChange,
  }
  const roleModels: Record<string, string> = { po: poModel, dev: devModel, cr: crModel, qa: qaModel }

  return (
    <div className="pt-2 border-t border-cat-surface1/50 space-y-1.5">
      <p className="text-[9px] text-cat-subtext font-bold uppercase">Recommended for your GPU</p>
      <p className="text-[10px] text-cat-overlay">{label}</p>
      <div className="space-y-1">
        {Object.entries(roles).map(([role, info]) => (
          <div key={role} className="flex items-center justify-between gap-2 text-[10px]">
            <span className="text-cat-subtext uppercase font-bold w-8">{role}</span>
            <span className="font-mono text-cat-text truncate flex-1">{info.model}</span>
            <span
              className={`text-[9px] px-1 rounded ${
                info.status === 'installed'
                  ? 'text-emerald-300 bg-emerald-950/40'
                  : info.status === 'partial'
                    ? 'text-amber-300 bg-amber-950/40'
                    : 'text-cat-overlay bg-cat-surface1'
              }`}
            >
              {info.status === 'installed' ? 'pulled' : info.status === 'partial' ? 'similar' : 'pull'}
            </span>
            <button
              type="button"
              onClick={() => roleSetters[role]?.(info.model)}
              disabled={roleModels[role] === info.model}
              className="text-indigo-400 hover:text-indigo-300 disabled:opacity-40 shrink-0"
            >
              Apply
            </button>
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={applyAll}
        className="w-full text-[10px] py-1 rounded border border-indigo-500/30 text-indigo-300 hover:bg-indigo-950/30"
      >
        Apply all recommendations
      </button>
    </div>
  )
}
