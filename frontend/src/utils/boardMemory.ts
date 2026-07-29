/** Client-side board memory caps to avoid multi-hour React OOM. */

import type { Board, BoardLane, Task, TaskFile } from '../types'

export const CLIENT_TRANSCRIPT_CAP = 50
export const CLIENT_DECISIONS_CAP = 40
export const CLIENT_TASK_FILES_CAP = 40

export function trimTaskFiles(files: Task['files'] | undefined): Task['files'] {
  if (!Array.isArray(files) || files.length <= CLIENT_TASK_FILES_CAP) return files
  return files.slice(-CLIENT_TASK_FILES_CAP) as Task['files']
}

export function trimTaskHistory(task: Task): Task {
  const next = { ...task }
  if (Array.isArray(task.transcript) && task.transcript.length > CLIENT_TRANSCRIPT_CAP) {
    next.transcript = task.transcript.slice(-CLIENT_TRANSCRIPT_CAP)
    next.transcriptTruncated = true
  }
  if (Array.isArray(task.decisions) && task.decisions.length > CLIENT_DECISIONS_CAP) {
    next.decisions = task.decisions.slice(-CLIENT_DECISIONS_CAP)
    next.decisionsTruncated = true
  }
  if (Array.isArray(task.files) && task.files.length > CLIENT_TASK_FILES_CAP) {
    next.files = trimTaskFiles(task.files) as TaskFile[]
  }
  return next
}

export function trimBoardHistory(board: Board | undefined | null): Board {
  if (!board) return board as Board
  const next: Board = { ...board }
  for (const lane of Object.keys(next) as BoardLane[]) {
    next[lane] = (next[lane] ?? []).map(trimTaskHistory)
  }
  return next
}

/**
 * Prefer incoming (often SSE-slimmed) over a longer client-held transcript.
 * Always enforce client caps so heap cannot grow unboundedly across hours.
 */
export function mergeTaskHistory(incoming: Task, existing: Task | undefined): Task {
  if (!existing) return trimTaskHistory(incoming)
  // Do NOT keep a longer client transcript when the server marked truncation —
  // that was the multi-hour OOM path. Incoming slim wins; then cap.
  return trimTaskHistory({ ...incoming })
}
