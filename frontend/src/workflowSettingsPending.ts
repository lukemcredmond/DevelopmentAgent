import type { WorkflowSettings } from './types'

const LLM_KEYS = ['llmProvider', 'llmProviderPreset', 'llmBaseUrl'] as const
const COMMITTED_KEYS = [...LLM_KEYS, 'executionProfile'] as const

/** Unsaved workflow partials (debounced POST in App). Merged over SSE/refresh snapshots. */
let pendingPatch: Partial<WorkflowSettings> = {}
let saveTimerActive = false
/** Snapshot sent with in-flight POST — kept until save succeeds so SSE cannot stomp edits. */
let inFlightPatch: Partial<WorkflowSettings> | null = null
let workflowSaveInFlight = false
/** Last committed fields we successfully saved (or the user selected). Survives stale SSE. */
let lastSavedCommitted: Partial<WorkflowSettings> = {}

function pickLlm(partial: Partial<WorkflowSettings> | undefined): Partial<WorkflowSettings> {
  if (!partial) return {}
  const out: Partial<WorkflowSettings> = {}
  for (const key of LLM_KEYS) {
    const value = partial[key]
    if (value != null && String(value).trim() !== '') {
      out[key] = value as never
    }
  }
  return out
}

function pickCommitted(partial: Partial<WorkflowSettings> | undefined): Partial<WorkflowSettings> {
  if (!partial) return {}
  const out = pickLlm(partial)
  const profile = partial.executionProfile
  if (profile === 'implementer' || profile === 'scrum') {
    out.executionProfile = profile
  }
  return out
}

export function inferLlmFieldsFromUrl(url: string | undefined): Partial<WorkflowSettings> {
  const raw = String(url || '')
    .trim()
    .toLowerCase()
  if (!raw) return {}
  if (raw.includes(':1234')) {
    return {
      llmProvider: 'openai_compat',
      llmProviderPreset: 'lmstudio',
      llmBaseUrl: String(url).trim(),
    }
  }
  if (raw.includes('/v1') && !raw.includes(':11434')) {
    return {
      llmProvider: 'openai_compat',
      llmProviderPreset: 'custom',
      llmBaseUrl: String(url).trim(),
    }
  }
  return {}
}

function applyLlmInference(settings: WorkflowSettings): WorkflowSettings {
  const preset = String(settings.llmProviderPreset || 'ollama').toLowerCase()
  if (preset !== 'ollama' && preset !== '') {
    return settings
  }
  const inferred = inferLlmFieldsFromUrl(settings.llmBaseUrl)
  if (!inferred.llmProviderPreset) return settings
  return { ...settings, ...inferred }
}

export function mergePendingWorkflowSettings(
  incoming: WorkflowSettings | undefined,
): WorkflowSettings | undefined {
  const overlay = {
    ...lastSavedCommitted,
    ...(inFlightPatch ?? {}),
    ...pendingPatch,
  }
  if (!incoming && Object.keys(overlay).length === 0) {
    return incoming
  }
  const merged = {
    ...(incoming ?? {}),
    ...overlay,
  } as WorkflowSettings
  return applyLlmInference(merged)
}

export function queuePendingWorkflowSettings(partial: Partial<WorkflowSettings>): void {
  pendingPatch = { ...pendingPatch, ...partial }
  const committed = pickCommitted(partial)
  if (Object.keys(committed).length) {
    lastSavedCommitted = { ...lastSavedCommitted, ...committed }
  }
}

export function peekPendingWorkflowSettings(): Partial<WorkflowSettings> {
  return { ...lastSavedCommitted, ...(inFlightPatch ?? {}), ...pendingPatch }
}

/** Begin an immediate POST for committed keys (e.g. executionProfile). */
export function beginImmediateWorkflowSave(
  partial: Partial<WorkflowSettings>,
): Partial<WorkflowSettings> {
  const payload = pickCommitted(partial)
  if (Object.keys(payload).length === 0) {
    return {}
  }
  inFlightPatch = { ...(inFlightPatch ?? {}), ...payload }
  workflowSaveInFlight = true
  const nextPending = { ...pendingPatch }
  for (const key of COMMITTED_KEYS) {
    if (key in payload) {
      delete nextPending[key]
    }
  }
  pendingPatch = nextPending
  return payload
}

/** Copy payload for POST; pending stays until markWorkflowSaveSucceeded / Failed. */
export function snapshotPendingWorkflowPayloadForSave(): Partial<WorkflowSettings> {
  const payload = { ...pendingPatch }
  inFlightPatch = { ...(inFlightPatch ?? {}), ...payload }
  pendingPatch = {}
  workflowSaveInFlight = true
  return payload
}

export function markWorkflowSaveSucceeded(
  savedFromServer?: WorkflowSettings,
  sentPayload?: Partial<WorkflowSettings>,
): void {
  const sentCommitted = pickCommitted(sentPayload)
  if (Object.keys(sentCommitted).length) {
    lastSavedCommitted = {
      ...lastSavedCommitted,
      ...pickCommitted(savedFromServer),
      ...sentCommitted,
    }
  }
  inFlightPatch = null
  workflowSaveInFlight = false
}

export function markWorkflowSaveFailed(payload: Partial<WorkflowSettings>): void {
  pendingPatch = { ...payload, ...pendingPatch }
  inFlightPatch = null
  workflowSaveInFlight = false
}

export function clearCommittedLlmSettings(): void {
  lastSavedCommitted = {}
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

export function queuedWorkflowPatchPending(): boolean {
  return Object.keys(pendingPatch).length > 0
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
