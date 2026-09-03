import { describe, expect, it, beforeEach } from 'vitest'
import {
  clearCommittedLlmSettings,
  inferLlmFieldsFromUrl,
  markWorkflowSaveSucceeded,
  mergePendingWorkflowSettings,
  queuePendingWorkflowSettings,
  snapshotPendingWorkflowPayloadForSave,
} from './workflowSettingsPending'
import type { WorkflowSettings } from './types'

function base(partial: Partial<WorkflowSettings> = {}): WorkflowSettings {
  return {
    llmProvider: 'ollama',
    llmProviderPreset: 'ollama',
    llmBaseUrl: 'http://localhost:11434',
    ...partial,
  } as WorkflowSettings
}

describe('workflowSettingsPending LLM overlay', () => {
  beforeEach(() => {
    clearCommittedLlmSettings()
    snapshotPendingWorkflowPayloadForSave()
    markWorkflowSaveSucceeded()
    clearCommittedLlmSettings()
  })

  it('infers LM Studio from :1234 URLs', () => {
    const inferred = inferLlmFieldsFromUrl('http://localhost:1234/v1')
    expect(inferred.llmProviderPreset).toBe('lmstudio')
    expect(inferred.llmProvider).toBe('openai_compat')
  })

  it('keeps LM Studio after a stale SSE snapshot that still says Ollama', () => {
    queuePendingWorkflowSettings({
      llmProviderPreset: 'lmstudio',
      llmProvider: 'openai_compat',
      llmBaseUrl: 'http://localhost:1234/v1',
    })
    const payload = snapshotPendingWorkflowPayloadForSave()
    markWorkflowSaveSucceeded(
      {
        ...base(),
        llmProviderPreset: 'lmstudio',
        llmProvider: 'openai_compat',
        llmBaseUrl: 'http://localhost:1234/v1',
      },
      payload,
    )

    const stomped = mergePendingWorkflowSettings(base())
    expect(stomped?.llmProviderPreset).toBe('lmstudio')
    expect(stomped?.llmProvider).toBe('openai_compat')
    expect(stomped?.llmBaseUrl).toBe('http://localhost:1234/v1')
  })

  it('does not let a later non-LLM save response rewrite the provider to Ollama', () => {
    queuePendingWorkflowSettings({
      llmProviderPreset: 'lmstudio',
      llmProvider: 'openai_compat',
      llmBaseUrl: 'http://localhost:1234/v1',
    })
    markWorkflowSaveSucceeded(undefined, snapshotPendingWorkflowPayloadForSave())

    queuePendingWorkflowSettings({ ollamaNumCtx: 8192 })
    const ctxPayload = snapshotPendingWorkflowPayloadForSave()
    markWorkflowSaveSucceeded(base({ ollamaNumCtx: 8192 }), ctxPayload)

    const merged = mergePendingWorkflowSettings(base({ ollamaNumCtx: 8192 }))
    expect(merged?.llmProviderPreset).toBe('lmstudio')
    expect(merged?.ollamaNumCtx).toBe(8192)
  })

  it('infers LM Studio when only llmBaseUrl is set on an Ollama preset', () => {
    const merged = mergePendingWorkflowSettings(
      base({ llmBaseUrl: 'http://127.0.0.1:1234/v1' }),
    )
    expect(merged?.llmProviderPreset).toBe('lmstudio')
  })
})
