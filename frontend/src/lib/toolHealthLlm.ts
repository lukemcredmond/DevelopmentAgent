import { probeAllToolsLlm } from '../api/client'
import type { AgentId, ToolProbeResult } from '../types'

export const TOOL_HEALTH_STORAGE_PREFIX = 'allhands-tool-health:'
export const AUTO_LLM_ON_PICK_KEY = 'allhands-auto-llm-tool-health'

export function toolHealthStorageKey(projectId: string, agentId: string): string {
  return `${TOOL_HEALTH_STORAGE_PREFIX}${projectId || 'default'}:${agentId}`
}

export function isAutoLlmToolHealthOnPick(): boolean {
  try {
    const v = localStorage.getItem(AUTO_LLM_ON_PICK_KEY)
    if (v === null) return true
    return v === '1' || v === 'true'
  } catch {
    return true
  }
}

export function setAutoLlmToolHealthOnPick(on: boolean): void {
  try {
    localStorage.setItem(AUTO_LLM_ON_PICK_KEY, on ? '1' : '0')
  } catch {
    /* ignore */
  }
}

export type LlmProbeAllSummary = {
  pass: number
  fail: number
  skip: number
  total: number
}

/** Run LLM probe-all and persist results for Tool Health sessionStorage. */
export async function runAndPersistLlmProbeAll(
  agent: AgentId | string,
  model: string,
  projectId: string,
): Promise<{ summary: LlmProbeAllSummary; results: ToolProbeResult[] }> {
  const data = await probeAllToolsLlm({
    agent,
    model,
    includeDestructive: false,
  })
  const next: Record<string, ToolProbeResult> = {}
  for (const r of data.results) {
    next[r.toolName] = r
  }
  try {
    sessionStorage.setItem(toolHealthStorageKey(projectId, agent), JSON.stringify(next))
  } catch {
    /* ignore quota */
  }
  return { summary: data.summary, results: data.results }
}
