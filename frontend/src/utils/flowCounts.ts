import type { TaskFlowWorkItemIndexEntry } from '../types'

/** "2 LLM · 5 tools · 1 failed" for an Agent progress row. */
export function formatWorkItemCounts(entry?: TaskFlowWorkItemIndexEntry | null): string {
  if (!entry) return ''
  const llm = entry.llmCalls ?? 0
  const tools = entry.toolCalls ?? 0
  if (!llm && !tools) return ''
  const parts = [`${llm} LLM`, `${tools} tool${tools === 1 ? '' : 's'}`]
  if (entry.failedToolCalls) parts.push(`${entry.failedToolCalls} failed`)
  if (entry.duplicateSkips) parts.push(`${entry.duplicateSkips} duplicate`)
  return parts.join(' · ')
}

/** "read_file ×4, apply_patch ×2" for the tooltip. */
export function formatToolBreakdown(entry?: TaskFlowWorkItemIndexEntry | null): string {
  const counts = entry?.toolCounts
  if (!counts) return ''
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([name, count]) => `${name} \u00d7${count}`)
    .join(', ')
}

export function formatMs(ms?: number | null): string {
  if (!ms) return '0ms'
  if (ms < 1000) return `${Math.round(ms)}ms`
  return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`
}

/** "LLM 12.4s · tools 3.1s" time split. */
export function formatTimeSplit(entry?: { llmMs?: number; toolMs?: number } | null): string {
  if (!entry) return ''
  const llmMs = entry.llmMs ?? 0
  const toolMs = entry.toolMs ?? 0
  if (!llmMs && !toolMs) return ''
  return `LLM ${formatMs(llmMs)} · tools ${formatMs(toolMs)}`
}
