import type { ToolExecutionEvent } from '../types'

/** Merge polled tool history with live SSE events without dropping in-flight runs. */
export function mergeToolHistory(
  prev: ToolExecutionEvent[],
  incoming: ToolExecutionEvent[],
): ToolExecutionEvent[] {
  const byId = new Map<string, ToolExecutionEvent>()
  for (const event of prev) {
    byId.set(event.id, event)
  }
  for (const event of incoming) {
    const existing = byId.get(event.id)
    if (!existing) {
      byId.set(event.id, event)
      continue
    }
    if (existing.status === 'running' && event.status !== 'running') {
      byId.set(event.id, { ...existing, ...event })
    } else if (String(event.timestamp).localeCompare(String(existing.timestamp)) >= 0) {
      byId.set(event.id, { ...existing, ...event })
    }
  }
  // Chronological (oldest last) so VirtualScrollList newestFirst shows newest on top.
  return Array.from(byId.values())
    .sort((a, b) => String(a.timestamp).localeCompare(String(b.timestamp)))
    .slice(-200)
}
