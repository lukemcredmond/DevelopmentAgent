import type { WorkflowSettings } from './types'

/** Unsaved workflow partials (debounced POST in App). Merged over SSE/refresh snapshots. */
let pendingPatch: Partial<WorkflowSettings> = {}
let saveTimerActive = false

export function mergePendingWorkflowSettings(
  incoming: WorkflowSettings | undefined,
): WorkflowSettings | undefined {
  if (!incoming && Object.keys(pendingPatch).length === 0) {
    return incoming
  }
  return {
    ...(incoming ?? {}),
    ...pendingPatch,
  } as WorkflowSettings
}

export function queuePendingWorkflowSettings(partial: Partial<WorkflowSettings>): void {
  pendingPatch = { ...pendingPatch, ...partial }
}

export function peekPendingWorkflowSettings(): Partial<WorkflowSettings> {
  return { ...pendingPatch }
}

export function takePendingWorkflowPayload(): Partial<WorkflowSettings> {
  const payload = pendingPatch
  pendingPatch = {}
  return payload
}

export function requeuePendingWorkflowPayload(payload: Partial<WorkflowSettings>): void {
  pendingPatch = { ...payload, ...pendingPatch }
}

export function hasPendingWorkflowSettings(): boolean {
  return saveTimerActive || Object.keys(pendingPatch).length > 0
}

export function setWorkflowSaveTimerActive(active: boolean): void {
  saveTimerActive = active
}
