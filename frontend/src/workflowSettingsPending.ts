import type { WorkflowSettings } from './types'

/** Unsaved workflow partials (debounced POST in App). Merged over SSE/refresh snapshots. */
let pendingPatch: Partial<WorkflowSettings> = {}
let saveTimerActive = false
/** Snapshot sent with in-flight POST — kept until save succeeds so SSE cannot stomp edits. */
let inFlightPatch: Partial<WorkflowSettings> | null = null
let workflowSaveInFlight = false

export function mergePendingWorkflowSettings(
  incoming: WorkflowSettings | undefined,
): WorkflowSettings | undefined {
  const overlay = {
    ...(inFlightPatch ?? {}),
    ...pendingPatch,
  }
  if (!incoming && Object.keys(overlay).length === 0) {
    return incoming
  }
  return {
    ...(incoming ?? {}),
    ...overlay,
  } as WorkflowSettings
}

export function queuePendingWorkflowSettings(partial: Partial<WorkflowSettings>): void {
  pendingPatch = { ...pendingPatch, ...partial }
}

export function peekPendingWorkflowSettings(): Partial<WorkflowSettings> {
  return { ...inFlightPatch, ...pendingPatch }
}

/** Copy payload for POST; pending stays until markWorkflowSaveSucceeded / Failed. */
export function snapshotPendingWorkflowPayloadForSave(): Partial<WorkflowSettings> {
  const payload = { ...pendingPatch }
  inFlightPatch = { ...(inFlightPatch ?? {}), ...payload }
  pendingPatch = {}
  workflowSaveInFlight = true
  return payload
}

export function markWorkflowSaveSucceeded(): void {
  inFlightPatch = null
  workflowSaveInFlight = false
}

export function markWorkflowSaveFailed(payload: Partial<WorkflowSettings>): void {
  pendingPatch = { ...payload, ...pendingPatch }
  inFlightPatch = null
  workflowSaveInFlight = false
}

/** @deprecated use snapshotPendingWorkflowPayloadForSave */
export function takePendingWorkflowPayload(): Partial<WorkflowSettings> {
  return snapshotPendingWorkflowPayloadForSave()
}

export function requeuePendingWorkflowPayload(payload: Partial<WorkflowSettings>): void {
  pendingPatch = { ...payload, ...pendingPatch }
  inFlightPatch = null
  workflowSaveInFlight = false
}

export function hasPendingWorkflowSettings(): boolean {
  return (
    saveTimerActive ||
    workflowSaveInFlight ||
    Object.keys(pendingPatch).length > 0 ||
    (inFlightPatch != null && Object.keys(inFlightPatch).length > 0)
  )
}

export function isWorkflowSaveInFlight(): boolean {
  return workflowSaveInFlight
}

export function setWorkflowSaveTimerActive(active: boolean): void {
  saveTimerActive = active
}
