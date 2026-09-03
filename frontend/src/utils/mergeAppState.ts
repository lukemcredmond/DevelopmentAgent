import type { AppState, Board } from '../types'

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
