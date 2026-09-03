import type { AppState, Board, PendingSimulation } from '../types'

function countBoardTasks(board: Board | undefined | null): number {
  if (!board) return 0
  return Object.values(board).reduce((sum, tasks) => sum + (tasks?.length ?? 0), 0)
}

/** Keep project identity when a partial/stale snapshot omits or empties the list. */
export function mergeAppStateIdentity(
  prev: Pick<AppState, 'projectId' | 'projectsList' | 'board'>,
  incoming: AppState,
  options?: { preserveEmptyBoard?: boolean },
): Pick<AppState, 'projectId' | 'projectsList' | 'board'> {
  const incomingList = incoming.projectsList
  const projectsList =
    Array.isArray(incomingList) && incomingList.length > 0
      ? incomingList
      : prev.projectsList?.length
        ? prev.projectsList
        : incomingList ?? prev.projectsList ?? []
  const projectId = String(incoming.projectId || prev.projectId || '')
  let board = incoming.board ?? prev.board
  if (
    options?.preserveEmptyBoard &&
    countBoardTasks(board) === 0 &&
    countBoardTasks(prev.board) > 0
  ) {
    board = prev.board
  }
  return { projectId, projectsList, board }
}

/** Drop pendingSimulation when missing, and ignore a snapshot that revives a dismissed id. */
export function mergePendingSimulation(
  prev: { pendingSimulation?: PendingSimulation | null },
  incoming: { pendingSimulation?: PendingSimulation | null },
  dismissedId?: string | null,
): PendingSimulation | null {
  const next = incoming.pendingSimulation ?? null
  if (!next?.id) return null
  if (dismissedId && next.id === dismissedId) return null
  return next
}

export function dismissedSimulationId(
  prev: { pendingSimulation?: PendingSimulation | null },
  next: PendingSimulation | null,
  currentDismissed?: string | null,
): string | null {
  if (next?.id) return null
  return prev.pendingSimulation?.id || currentDismissed || null
}
