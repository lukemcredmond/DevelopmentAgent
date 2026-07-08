import type { ActivityEvent, Board, Task, TaskDecision, TaskTranscriptEntry } from '../types'
import { formatTaskText } from './taskFormat'

const MAX_ACTIVITY = 200
/** Tail scan per task when hydrating activity from board (full history stays in task modal). */
export const MAX_TRANSCRIPT_TAIL = 30
export const MAX_DECISION_TAIL = 10

function activityKey(event: ActivityEvent): string {
  return `${event.timestamp}|${event.taskId}|${event.kind}|${event.content.slice(0, 80)}`
}

function transcriptToEvent(task: Task, entry: TaskTranscriptEntry): ActivityEvent {
  return {
    taskId: String(task.id),
    taskTitle: formatTaskText(task.title),
    kind: entry.role === 'tool' ? 'tool' : 'transcript',
    role: formatTaskText(entry.role),
    agent: formatTaskText(entry.agent ?? entry.role),
    content: formatTaskText(entry.content),
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
    taskTitle: formatTaskText(task.title),
    kind: 'decision',
    role: 'decision',
    agent: formatTaskText(decision.agent),
    content: formatTaskText(content),
    lane: typeof task.status === 'string' ? task.status : undefined,
    timestamp: decision.timestamp,
  }
}

export function hydrateActivityFromBoard(board: Board): ActivityEvent[] {
  const events: ActivityEvent[] = []
  for (const lane of Object.values(board)) {
    for (const task of lane ?? []) {
      const transcript = task.transcript ?? []
      const transcriptTail =
        transcript.length > MAX_TRANSCRIPT_TAIL
          ? transcript.slice(-MAX_TRANSCRIPT_TAIL)
          : transcript
      for (const entry of transcriptTail) {
        events.push(transcriptToEvent(task, entry))
      }
      const decisions = task.decisions ?? []
      const decisionTail =
        decisions.length > MAX_DECISION_TAIL ? decisions.slice(-MAX_DECISION_TAIL) : decisions
      for (const decision of decisionTail) {
        events.push(decisionToEvent(task, decision))
      }
    }
  }
  events.sort((a, b) => b.timestamp.localeCompare(a.timestamp))
  return events.slice(0, MAX_ACTIVITY)
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
  merged.sort((a, b) => b.timestamp.localeCompare(a.timestamp))
  return merged.slice(0, MAX_ACTIVITY)
}

export function filterActivityAfterClear(
  events: ActivityEvent[],
  clearedAt: string | null,
): ActivityEvent[] {
  if (!clearedAt) return events
  return events.filter((e) => e.timestamp > clearedAt)
}

export function activityTimestampNow(): string {
  return new Date().toISOString().slice(0, 19).replace('T', ' ')
}

export { MAX_ACTIVITY }
