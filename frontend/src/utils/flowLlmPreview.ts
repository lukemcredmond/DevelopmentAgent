import type { TaskFlowNode } from '../types'

const PREVIEW_MAX = 140

function toolCallName(call: unknown): string {
  if (!call || typeof call !== 'object') return '?'
  const c = call as Record<string, unknown>
  if (typeof c.name === 'string' && c.name) return c.name
  const fn = c.function
  if (fn && typeof fn === 'object' && typeof (fn as Record<string, unknown>).name === 'string') {
    return (fn as Record<string, unknown>).name as string
  }
  return '?'
}

function toolCallArgHint(call: unknown): string | null {
  if (!call || typeof call !== 'object') return null
  const c = call as Record<string, unknown>
  let raw = c.arguments
  if (raw == null && c.function && typeof c.function === 'object') {
    raw = (c.function as Record<string, unknown>).arguments
  }
  if (typeof raw !== 'string' || !raw.trim()) return null
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>
    const path = parsed.path ?? parsed.file_path ?? parsed.command ?? parsed.query
    if (typeof path === 'string' && path) return path.length > 48 ? `${path.slice(0, 45)}…` : path
    const keys = Object.keys(parsed)
    if (keys.length === 1) {
      const v = parsed[keys[0]!]
      const s = typeof v === 'string' ? v : JSON.stringify(v)
      return s.length > 48 ? `${s.slice(0, 45)}…` : s
    }
  } catch {
    return raw.length > 48 ? `${raw.slice(0, 45)}…` : raw
  }
  return null
}

/** One-line summary for a single tool call, e.g. `read_file(src/foo.ts)`. */
export function formatToolCallLine(call: unknown): string {
  const name = toolCallName(call)
  const hint = toolCallArgHint(call)
  return hint ? `${name}(${hint})` : name
}

/** Collapsed-row preview for an LLM flow node (text, tools, or error). */
export function llmCollapsedPreview(node: Pick<TaskFlowNode, 'responseContent' | 'toolCalls' | 'error'>): string {
  if (node.error?.trim()) {
    const e = node.error.trim()
    return e.length > PREVIEW_MAX ? `${e.slice(0, PREVIEW_MAX - 1)}…` : e
  }
  const text = node.responseContent?.trim()
  if (text) return text.length > PREVIEW_MAX ? `${text.slice(0, PREVIEW_MAX - 1)}…` : text
  const calls = node.toolCalls ?? []
  if (calls.length === 0) return '(empty model turn)'
  if (calls.length === 1) return `→ ${formatToolCallLine(calls[0])}`
  const head = calls.slice(0, 2).map((c) => formatToolCallLine(c)).join(', ')
  const extra = calls.length > 2 ? ` +${calls.length - 2} more` : ''
  const line = `→ ${head}${extra}`
  return line.length > PREVIEW_MAX ? `${line.slice(0, PREVIEW_MAX - 1)}…` : line
}

export function llmToolCallCount(node: Pick<TaskFlowNode, 'toolCalls'>): number {
  return node.toolCalls?.length ?? 0
}
