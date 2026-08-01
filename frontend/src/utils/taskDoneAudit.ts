import type { AgentWorkItem, Task } from '../types'

const DEV_PROGRESS_IDS = new Set(['read:files', 'write:implement', 'verify:command'])

/** Client-side hint: Done card may lack dev/AC evidence (matches server audit heuristics). */
export function taskLooksIncompleteOnDone(task: Task): boolean {
  if (task.status !== 'Done') return false
  const items = task.agentWorkItems ?? []
  for (const item of items) {
    if (!item?.id || !DEV_PROGRESS_IDS.has(item.id)) continue
    if (item.status === 'pending' || item.status === 'blocked') return true
  }
  const acs = (task.acceptanceCriteria ?? []).map((c) => String(c).trim()).filter(Boolean)
  if (acs.length === 0) return false
  const checks = task.acChecklist ?? []
  for (let i = 0; i < acs.length; i++) {
    if (!checks[i]) return true
  }
  return false
}

export function incompleteDevProgressLabels(task: Task): string[] {
  return (task.agentWorkItems ?? [])
    .filter(
      (i: AgentWorkItem) =>
        i.id && DEV_PROGRESS_IDS.has(i.id) && (i.status === 'pending' || i.status === 'blocked'),
    )
    .map((i) => i.label || i.id)
}
