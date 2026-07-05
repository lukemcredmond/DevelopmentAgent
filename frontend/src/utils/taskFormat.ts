/** Safely format task fields that may be objects from LLM JSON. */
export function formatTaskText(value: unknown): string {
  if (value == null) return ''
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (typeof value === 'object') {
    const obj = value as Record<string, unknown>
    for (const key of ['description', 'text', 'criteria', 'title', 'summary']) {
      const nested = obj[key]
      if (nested != null && nested !== '') return formatTaskText(nested)
    }
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}

export function formatAcceptanceCriteria(items: unknown[] | undefined): string[] {
  return (items ?? []).map((item) => formatTaskText(item).trim()).filter(Boolean)
}

/** Clone and sanitize a task for safe React rendering. */
export function sanitizeTaskForUi(task: import('../types').Task): import('../types').Task {
  return {
    ...task,
    id: String(task.id),
    title: formatTaskText(task.title),
    description: formatTaskText(task.description),
    status: formatTaskText(task.status),
    acceptanceCriteria: formatAcceptanceCriteria(task.acceptanceCriteria as unknown[] | undefined),
    userQuestion: task.userQuestion != null ? formatTaskText(task.userQuestion) : task.userQuestion,
    blockedBy: (task.blockedBy ?? []).map((b) => formatTaskText(b)),
    qaFailure: task.qaFailure
      ? {
          reason: formatTaskText(task.qaFailure.reason),
          output: task.qaFailure.output ? formatTaskText(task.qaFailure.output) : undefined,
          timestamp: formatTaskText(task.qaFailure.timestamp),
        }
      : task.qaFailure,
    decisions: (task.decisions ?? []).map((d) => ({
      ...d,
      summary: formatTaskText(d.summary),
      detail: d.detail ? formatTaskText(d.detail) : d.detail,
      agent: formatTaskText(d.agent),
    })),
    transcript: (task.transcript ?? []).map((e) => ({
      ...e,
      content: formatTaskText(e.content),
      role: formatTaskText(e.role),
      agent: e.agent ? formatTaskText(e.agent) : e.agent,
    })),
  }
}

export function findTaskOnBoard(
  board: import('../types').Board,
  taskId: string,
): import('../types').Task | null {
  const needle = String(taskId)
  for (const lane of Object.keys(board)) {
    const task = (board[lane as keyof typeof board] ?? []).find((t) => String(t.id) === needle)
    if (task) return sanitizeTaskForUi(task)
  }
  return null
}
