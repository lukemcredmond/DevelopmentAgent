import type {
  AppEvent,
  AppState,
  BriefPayload,
  ChatPayload,
  ChatResponse,
  ConfigPayload,
  CreateProjectPayload,
  FileDiffResponse,
  FileSearchResult,
  FileTreeNode,
  GitStatusResponse,
  ManualTaskPayload,
  MoveTaskPayload,
  OllamaHealthResponse,
  ProjectSummary,
  RecoveryContext,
  SkillsResponse,
  SkillPayload,
  BulkSkillPayload,
  SprintRunPayload,
  TerminalRunPayload,
  TerminalRunResponse,
  UpdateTaskPayload,
  ToolExecutePayload,
  ToolExecuteResult,
  ToolRegistryResponse,
  ToolReplayPayload,
  TranscriptToolEntry,
  WorkflowSettingsPayload,
} from '../types'

class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.status = status
    this.detail = detail
  }
}

async function request<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  })

  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: string }
      detail = body.detail ?? detail
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail)
  }

  if (res.status === 204) {
    return undefined as T
  }

  return (await res.json()) as T
}

export async function fetchState(options?: { includeFiles?: boolean }): Promise<AppState> {
  const includeFiles = options?.includeFiles !== false
  const qs = includeFiles ? '' : '?includeFiles=false'
  return request<AppState>(`/api/state${qs}`)
}

export async function clearLogs(): Promise<{ ok: boolean; logs: [] }> {
  return request<{ ok: boolean; logs: [] }>('/api/logs/clear', { method: 'POST' })
}

export async function updateConfig(payload: ConfigPayload): Promise<AppState> {
  return request<AppState>('/api/config', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function createProject(
  payload: CreateProjectPayload,
): Promise<AppState> {
  return request<AppState>('/api/projects/create', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function loadProject(projectId: string): Promise<AppState> {
  return request<AppState>(`/api/projects/load/${encodeURIComponent(projectId)}`, {
    method: 'POST',
  })
}

export async function deleteProject(
  projectId: string,
): Promise<{ ok: boolean; projectsList: ProjectSummary[] }> {
  return request<{ ok: boolean; projectsList: ProjectSummary[] }>(
    `/api/projects/${encodeURIComponent(projectId)}`,
    { method: 'DELETE' },
  )
}

export function exportProject(projectId: string): void {
  window.location.assign(
    `/api/projects/${encodeURIComponent(projectId)}/export`,
  )
}

export async function importProject(file: File): Promise<AppState> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch('/api/projects/import', {
    method: 'POST',
    body: form,
  })
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: string }
      detail = body.detail ?? detail
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail)
  }
  return (await res.json()) as AppState
}

export async function fetchSkills(): Promise<SkillsResponse> {
  return request<SkillsResponse>('/api/skills')
}

export async function fetchSkillSuggestions(
  agent: import('../types').AgentId,
  limit = 5,
): Promise<import('../types').SkillSuggestionsResponse> {
  return request<import('../types').SkillSuggestionsResponse>(
    `/api/skills/suggestions?agent=${encodeURIComponent(agent)}&limit=${limit}`,
  )
}

export async function assignSkill(payload: SkillPayload): Promise<AppState> {
  return request<AppState>('/api/assign-skill', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function assignSkills(payload: BulkSkillPayload): Promise<AppState> {
  return request<AppState>('/api/assign-skills', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function removeSkill(payload: SkillPayload): Promise<AppState> {
  return request<AppState>('/api/remove-skill', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function addManualTask(
  payload: ManualTaskPayload,
): Promise<AppState> {
  return request<AppState>('/api/tasks/manual', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function updateTask(
  taskId: string,
  payload: UpdateTaskPayload,
): Promise<AppState> {
  return request<AppState>(`/api/tasks/${encodeURIComponent(taskId)}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  })
}

export async function deleteTask(taskId: string): Promise<AppState> {
  return request<AppState>(`/api/tasks/${encodeURIComponent(taskId)}`, {
    method: 'DELETE',
  })
}

export async function clearTaskTranscript(taskId: string): Promise<AppState> {
  return request<AppState>(
    `/api/tasks/${encodeURIComponent(taskId)}/transcript`,
    { method: 'DELETE' },
  )
}

export async function moveTask(payload: MoveTaskPayload): Promise<AppState> {
  return request<AppState>('/api/tasks/move', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function claimReadyBacklogCards(
  limit = 5,
): Promise<AppState & { claimedTaskIds: string[]; readyCount: number }> {
  return request('/api/board/claim-ready', {
    method: 'POST',
    body: JSON.stringify({ limit }),
  })
}

export async function resetWorkspace(): Promise<AppState> {
  return request<AppState>('/api/reset', { method: 'POST' })
}

export async function clearAllTasks(): Promise<AppState> {
  return request<AppState>('/api/board/clear-tasks', { method: 'POST' })
}

export async function triggerPlan(payload: BriefPayload): Promise<AppState> {
  return request<AppState>('/api/plan', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function triggerPlanOutline(payload: BriefPayload): Promise<AppState> {
  return request<AppState>('/api/plan/outline', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function triggerPlanBacklog(
  payload: BriefPayload & { outline?: string },
): Promise<AppState> {
  return request<AppState>('/api/plan/backlog', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function triggerStep(payload: BriefPayload): Promise<AppState> {
  return request<AppState>('/api/step', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function runInProgressStep(
  payload: BriefPayload & { taskId?: string },
): Promise<AppState> {
  return request<AppState>('/api/sprint/run-in-progress', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function getSprintRecovery(): Promise<{ recovery: RecoveryContext | null }> {
  return request<{ recovery: RecoveryContext | null }>('/api/sprint/recovery')
}

export async function dismissSprintRecovery(): Promise<AppState> {
  return request<AppState>('/api/sprint/recovery/dismiss', { method: 'POST' })
}

export async function sendChat(
  payload: ChatPayload,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  return request<ChatResponse>('/api/chat', {
    method: 'POST',
    body: JSON.stringify(payload),
    signal,
  })
}

export async function clearChatHistory(): Promise<{ ok: boolean; deleted: number; chatMessages: [] }> {
  return request<{ ok: boolean; deleted: number; chatMessages: [] }>('/api/chat/clear', {
    method: 'POST',
  })
}

export async function* streamChat(
  payload: ChatPayload,
  signal?: AbortSignal,
): AsyncGenerator<string, void, unknown> {
  const res = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    signal,
  })

  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = (await res.json()) as { detail?: string }
      detail = body.detail ?? detail
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail)
  }

  const reader = res.body?.getReader()
  if (!reader) return

  const decoder = new TextDecoder()
  let buffer = ''

  while (true) {
    const { done, value } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = line.slice(6)
        if (data === '[DONE]') return
        try {
          const parsed = JSON.parse(data) as {
            chunk?: string
            token?: string
            content?: string
            done?: boolean
          }
          if (parsed.done) return
          yield parsed.chunk ?? parsed.token ?? parsed.content ?? ''
        } catch {
          yield data
        }
      }
    }
  }
}

export async function fetchFileTree(): Promise<FileTreeNode[]> {
  const res = await request<{ tree?: FileTreeNode[] } | FileTreeNode[]>(
    '/api/files/tree',
  )
  return Array.isArray(res) ? res : (res.tree ?? [])
}

export async function saveFile(
  path: string,
  content: string,
): Promise<AppState | { ok: boolean }> {
  return request<AppState | { ok: boolean }>('/api/files/save', {
    method: 'POST',
    body: JSON.stringify({ path, content }),
  })
}

export async function searchFiles(query: string): Promise<FileSearchResult[]> {
  const res = await request<{ results?: FileSearchResult[] } | FileSearchResult[]>(
    `/api/files/search?q=${encodeURIComponent(query)}`,
  )
  return Array.isArray(res) ? res : (res.results ?? [])
}

export async function fetchFileDiff(path: string): Promise<FileDiffResponse> {
  const raw = await request<{
    path: string
    previous_content?: string
    content?: string
  }>(`/api/files/diff?path=${encodeURIComponent(path)}`)
  return {
    path: raw.path,
    oldValue: raw.previous_content ?? '',
    newValue: raw.content ?? '',
  }
}

export async function checkOllamaHealth(
  url?: string,
): Promise<OllamaHealthResponse> {
  const qs = url ? `?url=${encodeURIComponent(url)}` : ''
  return request<OllamaHealthResponse>(`/api/ollama/health${qs}`)
}

export async function runTerminal(
  payload: TerminalRunPayload,
): Promise<TerminalRunResponse> {
  return request<TerminalRunResponse>('/api/terminal/run', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function startBackgroundTerminal(
  command: string,
): Promise<{ sessionId: string; command: string }> {
  return request<{ sessionId: string; command: string }>('/api/terminal/background', {
    method: 'POST',
    body: JSON.stringify({ command }),
  })
}

export async function fetchBackgroundTerminals(): Promise<{
  sessions: Array<{
    id: string
    command: string
    startedAt: string
    done: boolean
    exitCode?: number | null
    outputLength: number
  }>
}> {
  return request('/api/terminal/background')
}

export async function fetchBackgroundTerminalOutput(
  sessionId: string,
  offset = 0,
): Promise<{
  sessionId: string
  chunk: string
  outputLength: number
  done: boolean
  exitCode?: number | null
}> {
  return request(
    `/api/terminal/background/${encodeURIComponent(sessionId)}?offset=${offset}`,
  )
}

export async function stopBackgroundTerminal(sessionId: string): Promise<{ ok: boolean }> {
  return request(`/api/terminal/background/${encodeURIComponent(sessionId)}`, {
    method: 'DELETE',
  })
}

export async function runSprint(payload: SprintRunPayload): Promise<AppState> {
  return request<AppState>('/api/sprint/run', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function planAndRun(payload: SprintRunPayload): Promise<AppState> {
  return request<AppState>('/api/sprint/plan-and-run', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function approveTask(taskId: string): Promise<AppState> {
  return request<AppState>(`/api/tasks/${encodeURIComponent(taskId)}/approve`, {
    method: 'POST',
  })
}

export async function resolveUserQuestion(
  taskId: string,
  answer: string,
  target: 'dev' | 'refinement' | 'po' = 'dev',
): Promise<AppState> {
  return request<AppState>(
    `/api/tasks/${encodeURIComponent(taskId)}/resolve-user`,
    { method: 'POST', body: JSON.stringify({ answer, target }) },
  )
}

export async function escalateNeedsUserToPo(): Promise<AppState & { movedTaskIds?: string[] }> {
  return request('/api/board/escalate-needs-user-to-po', { method: 'POST' })
}

export async function reindexCodebase(
  ollamaUrl = 'http://localhost:11434',
): Promise<{
  ok: boolean
  chunks?: number
  filesScanned?: number
  filesSkipped?: number
  embedFailures?: number
  error?: string
}> {
  return request('/api/search/reindex', {
    method: 'POST',
    body: JSON.stringify({ ollama_url: ollamaUrl }),
  })
}

export async function splitTask(
  taskId: string,
  payload: { ollamaUrl?: string; guidance?: string } = {},
): Promise<AppState & { splitResult?: { added: number; taskId: string; taskIds: string[] } }> {
  return request<AppState & { splitResult?: { added: number; taskId: string; taskIds: string[] } }>(
    `/api/tasks/${encodeURIComponent(taskId)}/split`,
    {
      method: 'POST',
      body: JSON.stringify({
        ollama_url: payload.ollamaUrl ?? 'http://localhost:11434',
        guidance: payload.guidance ?? '',
      }),
    },
  )
}

export async function injectToolEvidence(
  taskId: string,
  payload: {
    toolName?: string
    toolArgs?: Record<string, unknown>
    toolOutput: string
    note?: string
  },
): Promise<AppState & { injectResult?: Record<string, unknown> }> {
  return request<AppState & { injectResult?: Record<string, unknown> }>(
    `/api/tasks/${encodeURIComponent(taskId)}/inject-tool-evidence`,
    {
      method: 'POST',
      body: JSON.stringify({
        toolName: payload.toolName ?? 'run_command',
        toolArgs: payload.toolArgs ?? {},
        toolOutput: payload.toolOutput,
        note: payload.note ?? '',
      }),
    },
  )
}

export async function reorderTasks(
  lane: string,
  taskIds: string[],
): Promise<AppState> {
  return request<AppState>('/api/tasks/reorder', {
    method: 'POST',
    body: JSON.stringify({ lane, taskIds }),
  })
}

export async function escapeSubtaskLoop(
  taskId: string,
  mode: 'needs_po' | 'skip_pending' | 'flatten' = 'needs_po',
): Promise<AppState & { message?: string }> {
  return request<AppState & { message?: string }>(`/api/tasks/${taskId}/escape-subtasks`, {
    method: 'POST',
    body: JSON.stringify({ mode }),
  })
}

export async function updateWorkflowSettings(
  payload: WorkflowSettingsPayload,
): Promise<AppState> {
  return request<AppState>('/api/workflow/settings', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function cancelSprint(): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>('/api/sprint/cancel', { method: 'POST' })
}

export async function fetchPendingTools(): Promise<{ pending: import('../types').PendingToolRequest[] }> {
  return request<{ pending: import('../types').PendingToolRequest[] }>('/api/tools/pending')
}

export async function resolvePendingTool(
  requestId: string,
  payload: import('../types').ResolvePendingToolPayload,
): Promise<{ ok: boolean }> {
  return request<{ ok: boolean }>(`/api/tools/pending/${encodeURIComponent(requestId)}/resolve`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function dismissPendingTool(
  requestId: string,
): Promise<{ ok: boolean; pending: import('../types').PendingToolRequest[] }> {
  return request<{ ok: boolean; pending: import('../types').PendingToolRequest[] }>(
    `/api/tools/pending/${encodeURIComponent(requestId)}/dismiss`,
    { method: 'POST' },
  )
}

export async function dismissAllPendingTools(options?: {
  cancelSprint?: boolean
}): Promise<{
  ok: boolean
  dismissed: number
  sprintCancel: boolean
  pending: import('../types').PendingToolRequest[]
}> {
  return request('/api/tools/pending/dismiss-all', {
    method: 'POST',
    body: JSON.stringify({ cancelSprint: options?.cancelSprint ?? false }),
  })
}

export async function fetchPendingApprovals(): Promise<{
  pending: import('../types').PendingToolApproval[]
}> {
  return request<{ pending: import('../types').PendingToolApproval[] }>(
    '/api/tools/pending-approvals',
  )
}

export async function resolveToolApproval(
  approvalId: string,
  approved: boolean,
): Promise<import('../types').AppState & { ok: boolean; pending: import('../types').PendingToolApproval[] }> {
  return request(
    `/api/tools/approvals/${encodeURIComponent(approvalId)}`,
    {
      method: 'POST',
      body: JSON.stringify({ approved }),
    },
  )
}

export async function fetchToolHistory(): Promise<{ events: Record<string, unknown>[] }> {
  return request<{ events: Record<string, unknown>[] }>('/api/tools/history')
}

export async function clearToolHistory(): Promise<{ ok: boolean; events: [] }> {
  return request<{ ok: boolean; events: [] }>('/api/tools/history/clear', { method: 'POST' })
}

export async function diagnoseTask(
  taskId: string,
  ollamaUrl: string,
): Promise<{ diagnosis: import('../types').TaskDiagnosis; state: import('../types').AppState }> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/diagnose`, {
    method: 'POST',
    body: JSON.stringify({ ollamaUrl }),
  })
}

export async function fetchLlmLogs(params?: {
  limit?: number
  agent?: string
  taskId?: string
}): Promise<{ entries: import('../types').LlmDebugEntry[] }> {
  const q = new URLSearchParams()
  if (params?.limit) q.set('limit', String(params.limit))
  if (params?.agent) q.set('agent', params.agent)
  if (params?.taskId) q.set('taskId', params.taskId)
  const qs = q.toString()
  return request(`/api/ollama/logs${qs ? `?${qs}` : ''}`)
}

export async function clearLlmLogs(): Promise<{ ok: boolean; entries: [] }> {
  return request('/api/ollama/logs/clear', { method: 'POST' })
}

export interface OllamaServiceLogSnapshot {
  available: boolean
  source: string
  path?: string | null
  note?: string | null
  lines: string[]
  text: string
  error?: string | null
}

export async function fetchOllamaServiceLogs(
  lines = 50,
): Promise<OllamaServiceLogSnapshot> {
  return request(`/api/ollama/service-logs?lines=${lines}`)
}

export async function fetchModelTimeline(params?: {
  taskId?: string
  limit?: number
}): Promise<import('../types').ModelTimelineResponse> {
  const q = new URLSearchParams()
  if (params?.taskId) q.set('taskId', params.taskId)
  if (params?.limit) q.set('limit', String(params.limit))
  const qs = q.toString()
  return request(`/api/llm-logs/timeline${qs ? `?${qs}` : ''}`)
}

export async function fetchStackCatalog(
  useBrief = true,
): Promise<import('../types').StackCatalogResponse> {
  return request(`/api/tools/stack-catalog?brief=${useBrief ? '1' : '0'}`)
}

export async function checkQdrantHealth(
  url = 'http://localhost:6333',
  apiKey?: string,
): Promise<{ ok: boolean; collections?: string[]; error?: string; apiKeyConfigured?: boolean }> {
  const q = new URLSearchParams({ url })
  if (apiKey?.trim()) q.set('apiKey', apiKey.trim())
  return request(`/api/ollama/qdrant-health?${q.toString()}`)
}

export async function fetchSystemCapacity(): Promise<{
  gpuAvailable: boolean
  vramMb?: number | null
  vramUsedMb?: number | null
  gpuUtilPct?: number | null
  ramGb?: number | null
  platform?: string
  tier: string
}> {
  return request('/api/ollama/system-capacity')
}

export async function fetchModelRecommendations(
  ollamaUrl = 'http://localhost:11434',
): Promise<{
  capacity: Record<string, unknown>
  tier: string
  roles: Record<string, { model: string; status: string }>
  note?: string
}> {
  return request(
    `/api/ollama/model-recommendations?ollamaUrl=${encodeURIComponent(ollamaUrl)}`,
  )
}

export async function retryAgentStep(payload: {
  taskId: string
  agentId: string
  mode: 'same' | 'optimized' | 'fix_and_verify'
  ollamaUrl: string
}): Promise<{
  ok: boolean
  output?: string
  verification?: {
    status?: string
    diagnosticsCount?: number
    summary?: string
    command?: string | null
  }
  state?: import('../types').AppState
}> {
  return request('/api/agents/retry-step', {
    method: 'POST',
    body: JSON.stringify({
      taskId: payload.taskId,
      agentId: payload.agentId,
      mode: payload.mode,
      ollamaUrl: payload.ollamaUrl,
    }),
  })
}

export async function fetchFileRevisions(
  path: string,
  limit = 20,
): Promise<{ path: string; revisions: Record<string, unknown>[] }> {
  return request(
    `/api/files/revisions?path=${encodeURIComponent(path)}&limit=${limit}`,
  )
}

export async function fetchIndexStatus(): Promise<{
  ok: boolean
  available?: boolean
  chunks?: number
  qdrantUrl?: string
  apiKeyConfigured?: boolean
}> {
  return request('/api/search/index-status')
}

export async function fetchProjectMemories(
  ollamaUrl = 'http://localhost:11434',
  limit = 30,
  options: {
    agent?: string
    category?: string
    q?: string
    dedupe?: boolean
  } = {},
): Promise<{ entries: import('../types').ProjectMemoryEntry[]; count: number }> {
  const params = new URLSearchParams({
    limit: String(limit),
    ollamaUrl,
    dedupe: String(options.dedupe !== false),
  })
  if (options.agent) params.set('agent', options.agent)
  if (options.category) params.set('category', options.category)
  if (options.q) params.set('q', options.q)
  return request(`/api/memory?${params.toString()}`)
}

export async function createProjectMemory(
  content: string,
  ollamaUrl = 'http://localhost:11434',
  agent = 'System',
): Promise<{ ok: boolean }> {
  return request(`/api/memory?ollamaUrl=${encodeURIComponent(ollamaUrl)}`, {
    method: 'POST',
    body: JSON.stringify({ content, agent, category: 'user_note' }),
  })
}

export async function updateProjectMemory(
  memoryId: string,
  content: string,
  category = 'user_note',
  ollamaUrl = 'http://localhost:11434',
): Promise<{ ok: boolean; entry?: import('../types').ProjectMemoryEntry }> {
  return request(`/api/memory/${encodeURIComponent(memoryId)}?ollamaUrl=${encodeURIComponent(ollamaUrl)}`, {
    method: 'PATCH',
    body: JSON.stringify({ content, category }),
  })
}

export async function deleteProjectMemory(memoryId: string): Promise<{ ok: boolean }> {
  return request(`/api/memory/${encodeURIComponent(memoryId)}`, { method: 'DELETE' })
}

export async function fetchToolRegistry(agent: string): Promise<ToolRegistryResponse> {
  return request<ToolRegistryResponse>(`/api/tools/registry?agent=${encodeURIComponent(agent)}`)
}

export async function fetchToolsCatalog(): Promise<import('../types').ToolsCatalogResponse> {
  return request<import('../types').ToolsCatalogResponse>('/api/tools/catalog')
}

export async function executeTool(
  payload: ToolExecutePayload,
): Promise<{ ok: boolean; result: ToolExecuteResult }> {
  return request<{ ok: boolean; result: ToolExecuteResult }>('/api/tools/execute', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function fetchTaskToolCalls(
  taskId: string,
): Promise<{ taskId: string; entries: TranscriptToolEntry[] }> {
  return request<{ taskId: string; entries: TranscriptToolEntry[] }>(
    `/api/tools/transcript/${encodeURIComponent(taskId)}`,
  )
}

export async function replayTools(
  payload: ToolReplayPayload,
): Promise<{ ok: boolean; executed: number; results: ToolExecuteResult[] }> {
  return request<{ ok: boolean; executed: number; results: ToolExecuteResult[] }>(
    '/api/tools/replay',
    {
      method: 'POST',
      body: JSON.stringify(payload),
    },
  )
}

export async function fetchGitStatus(): Promise<GitStatusResponse> {
  return request<GitStatusResponse>('/api/git/status')
}

export function subscribeEvents(
  onEvent: (event: AppEvent) => void,
  onError?: (error: Event) => void,
): EventSource {
  const source = new EventSource('/api/events')

  source.addEventListener('message', (e) => {
    try {
      const event = JSON.parse(e.data) as AppEvent
      onEvent(event)
    } catch {
      onEvent({ type: 'connected', data: e.data })
    }
  })

  source.addEventListener('state', (e) => {
    onEvent({ type: 'state', data: JSON.parse(e.data) })
  })

  source.addEventListener('board', (e) => {
    onEvent({ type: 'board', data: JSON.parse(e.data) })
  })

  source.addEventListener('files', (e) => {
    onEvent({ type: 'files', data: JSON.parse(e.data) })
  })

  source.addEventListener('log', (e) => {
    onEvent({ type: 'log', data: JSON.parse(e.data) })
  })

  source.addEventListener('sprint', (e) => {
    onEvent({ type: 'sprint', data: JSON.parse(e.data) })
  })

  source.addEventListener('activity', (e) => {
    onEvent({ type: 'activity', data: JSON.parse(e.data) })
  })

  source.addEventListener('pending_tool', (e) => {
    onEvent({ type: 'pending_tool', data: JSON.parse(e.data) })
  })

  source.addEventListener('sprint_progress', (e) => {
    onEvent({ type: 'sprint_progress', data: JSON.parse(e.data) })
  })

  source.addEventListener('index_progress', (e) => {
    onEvent({ type: 'index_progress', data: JSON.parse(e.data) })
  })

  source.addEventListener('agent_run', (e) => {
    onEvent({ type: 'agent_run', data: JSON.parse(e.data) })
  })

  source.addEventListener('tool_start', (e) => {
    onEvent({ type: 'tool_start', data: JSON.parse(e.data) })
  })

  source.addEventListener('tool_end', (e) => {
    onEvent({ type: 'tool_end', data: JSON.parse(e.data) })
  })

  source.addEventListener('tool_approval_required', (e) => {
    onEvent({ type: 'tool_approval_required', data: JSON.parse(e.data) })
  })

  source.onerror = (err) => {
    onError?.(err)
  }

  return source
}

export { ApiError }
