import type { ActivityEvent, Board, Task, TaskDecision, TaskTranscriptEntry } from '../types'

const MAX_ACTIVITY = 200

function activityKey(event: ActivityEvent): string {
  return `${event.timestamp}|${event.taskId}|${event.kind}|${event.content.slice(0, 80)}`
}

function transcriptToEvent(task: Task, entry: TaskTranscriptEntry): ActivityEvent {
  return {
    taskId: String(task.id),
    taskTitle: task.title,
    kind: entry.role === 'tool' ? 'tool' : 'transcript',
    role: entry.role,
    agent: entry.agent ?? entry.role,
    content: entry.content,
    lane: typeof task.status === 'string' ? task.status : undefined,
    timestamp: entry.timestamp,
  }
}

function decisionToEvent(task: Task, decision: TaskDecision): ActivityEvent {
  const content = decision.detail
    ? `${decision.summary}\n${decision.detail}`
    : decision.summary
  return {
    taskId: String(task.id),
    taskTitle: task.title,
    kind: 'decision',
    role: 'decision',
    agent: decision.agent,
    content,
    lane: typeof task.status === 'string' ? task.status : undefined,
    timestamp: decision.timestamp,
  }
}

export function hydrateActivityFromBoard(board: Board): ActivityEvent[] {
  const events: ActivityEvent[] = []
  for (const lane of Object.values(board)) {
    for (const task of lane ?? []) {
      for (const entry of task.transcript ?? []) {
        events.push(transcriptToEvent(task, entry))
      }
      for (const decision of task.decisions ?? []) {
        events.push(decisionToEvent(task, decision))
      }
    }
  }
  events.sort((a, b) => a.timestamp.localeCompare(b.timestamp))
  return events.slice(-MAX_ACTIVITY)
}

export function mergeActivityEvents(
  hydrated: ActivityEvent[],
  live: ActivityEvent[],
): ActivityEvent[] {
  const seen = new Set<string>()
  const merged: ActivityEvent[] = []
  for (const event of [...hydrated, ...live]) {
    const key = activityKey(event)
    if (seen.has(key)) continue
    seen.add(key)
    merged.push(event)
  }
  merged.sort((a, b) => a.timestamp.localeCompare(b.timestamp))
  return merged.slice(-MAX_ACTIVITY)
}

export { MAX_ACTIVITY }
