/** Format agent wall/Ollama time and token counts for cards. */

import type { AgentUsageEntry } from '../types'

export function formatDurationMs(ms: number | undefined | null): string {
  if (ms == null || Number.isNaN(ms) || ms <= 0) return '0s'
  if (ms < 1000) return `${Math.round(ms)}ms`
  const sec = Math.floor(ms / 1000)
  if (sec < 60) return `${sec}s`
  const min = Math.floor(sec / 60)
  const remSec = sec % 60
  if (min < 60) return remSec > 0 ? `${min}m ${remSec}s` : `${min}m`
  const hr = Math.floor(min / 60)
  const remMin = min % 60
  return remMin > 0 ? `${hr}h ${remMin}m` : `${hr}h`
}

export function formatTokenCount(n: number | undefined | null): string {
  if (n == null || Number.isNaN(n) || n <= 0) return '0'
  if (n < 1000) return String(Math.round(n))
  if (n < 1_000_000) {
    const k = n / 1000
    return k >= 100 ? `${Math.round(k)}K` : `${k.toFixed(k >= 10 ? 0 : 1)}K`
  }
  const m = n / 1_000_000
  return m >= 100 ? `${Math.round(m)}M` : `${m.toFixed(m >= 10 ? 1 : 2)}M`
}

export function formatTokensLine(entry: AgentUsageEntry | null | undefined): string {
  if (!entry) return '—'
  const reported = entry.tokensReported
  const total = entry.totalTokens ?? (entry.promptTokens ?? 0) + (entry.evalTokens ?? 0)
  if (!reported && total <= 0) return '—'
  const prompt = entry.promptTokens ?? 0
  const evalTok = entry.evalTokens ?? 0
  if (prompt > 0 || evalTok > 0) {
    return `${formatTokenCount(prompt)} in / ${formatTokenCount(evalTok)} out`
  }
  return `${formatTokenCount(total)} tok`
}

const ROLE_SHORT: Record<string, string> = {
  Developer: 'Dev',
  PO: 'PO',
  'Product Owner': 'PO',
  QA: 'QA',
  'Code Reviewer': 'CR',
  CR: 'CR',
}

export function shortAgentRole(role: string): string {
  return ROLE_SHORT[role] || role.slice(0, 6)
}

/** Compact one-liner for TaskCard, e.g. "Dev 2h · 1.3M tok". */
export function formatAgentUsageBrief(
  usage: Record<string, AgentUsageEntry> | null | undefined,
): string | null {
  if (!usage) return null
  const parts: string[] = []
  let totalTok = 0
  let anyReported = false
  for (const [role, entry] of Object.entries(usage)) {
    if (!entry) continue
    const dur = entry.durationMs ?? entry.ollamaMs ?? 0
    if (dur <= 0 && (entry.callCount ?? 0) <= 0) continue
    parts.push(`${shortAgentRole(role)} ${formatDurationMs(dur)}`)
    totalTok += entry.totalTokens ?? (entry.promptTokens ?? 0) + (entry.evalTokens ?? 0)
    if (entry.tokensReported) anyReported = true
  }
  if (!parts.length) return null
  const tokBit =
    anyReported || totalTok > 0 ? ` · ${formatTokenCount(totalTok)} tok` : ''
  // Prefer showing the heaviest agent first if many
  return parts.length <= 2 ? `${parts.join(' · ')}${tokBit}` : `${parts[0]} (+${parts.length - 1})${tokBit}`
}
