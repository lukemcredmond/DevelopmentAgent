import type { Board, BoardLane, Task } from '../types'

export type CardTreeTab = 'hierarchy' | 'dependencies'

export interface CardTreeNode {
  task: Task
  lane: BoardLane | string
  children: CardTreeNode[]
}

export interface CardTreeForest {
  roots: CardTreeNode[]
  unlinked: CardTreeNode[]
}

export interface DependencyEdge {
  fromId: string
  toId: string
  outcomeStatus?: string
}

export interface DependencyGraph {
  roots: CardTreeNode[]
  blockedLane: CardTreeNode[]
  edges: DependencyEdge[]
  missingBlockers: string[]
}

function collectTasks(board: Board): Map<string, { task: Task; lane: BoardLane | string }> {
  const byId = new Map<string, { task: Task; lane: BoardLane | string }>()
  for (const [lane, tasks] of Object.entries(board) as [BoardLane, Task[]][]) {
    for (const task of tasks ?? []) {
      if (!task?.id) continue
      byId.set(task.id, { task, lane })
    }
  }
  return byId
}

function childIdsFor(
  task: Task,
  byId: Map<string, { task: Task; lane: BoardLane | string }>,
): string[] {
  const ids = new Set<string>()
  for (const id of task.childTaskIds ?? []) {
    if (id) ids.add(id)
  }
  for (const id of task.subtaskIds ?? []) {
    if (id) ids.add(id)
  }
  for (const { task: other } of byId.values()) {
    if (other.id === task.id) continue
    if (other.parentTaskId === task.id) ids.add(other.id)
    const fid = other.featureId
    if (fid && fid === task.id) ids.add(other.id)
  }
  return [...ids]
}

function hasHierarchyParent(
  task: Task,
  byId: Map<string, { task: Task; lane: BoardLane | string }>,
): boolean {
  if (task.parentTaskId && byId.has(task.parentTaskId)) return true
  const fid = task.featureId
  if (fid && fid !== task.id && byId.has(fid)) return true
  for (const { task: other } of byId.values()) {
    if (other.id === task.id) continue
    if ((other.childTaskIds ?? []).includes(task.id)) return true
    if ((other.subtaskIds ?? []).includes(task.id)) return true
  }
  return false
}

function makeNode(
  task: Task,
  lane: BoardLane | string,
  byId: Map<string, { task: Task; lane: BoardLane | string }>,
  visiting: Set<string>,
): CardTreeNode {
  const children: CardTreeNode[] = []
  for (const cid of childIdsFor(task, byId)) {
    if (!cid || cid === task.id || visiting.has(cid)) continue
    const child = byId.get(cid)
    if (!child) continue
    visiting.add(cid)
    children.push(makeNode(child.task, child.lane, byId, visiting))
    visiting.delete(cid)
  }
  return { task, lane, children }
}

export function buildHierarchyForest(board: Board): CardTreeForest {
  const byId = collectTasks(board)
  const roots: CardTreeNode[] = []
  const unlinked: CardTreeNode[] = []
  const placed = new Set<string>()

  for (const { task, lane } of byId.values()) {
    const inFeatures = lane === 'Features' || task.workType === 'feature'
    if (inFeatures) {
      placed.add(task.id)
      roots.push(makeNode(task, lane, byId, new Set([task.id])))
    }
  }

  for (const { task, lane } of byId.values()) {
    if (placed.has(task.id)) continue
    if (hasHierarchyParent(task, byId)) continue
    if (task.workType === 'planning' || task.workType === 'feature') continue
    unlinked.push(makeNode(task, lane, byId, new Set([task.id])))
    placed.add(task.id)
  }

  roots.sort((a, b) => a.task.title.localeCompare(b.task.title))
  unlinked.sort((a, b) => a.task.title.localeCompare(b.task.title))
  return { roots, unlinked }
}

function dependencyChildren(
  taskId: string,
  byId: Map<string, { task: Task; lane: BoardLane | string }>,
): string[] {
  const blocked: string[] = []
  for (const { task } of byId.values()) {
    for (const b of task.blockedBy ?? []) {
      if (b === taskId) blocked.push(task.id)
    }
  }
  return blocked
}

export function buildDependencyGraph(board: Board): DependencyGraph {
  const byId = collectTasks(board)
  const edges: DependencyEdge[] = []
  const hasIncoming = new Set<string>()

  for (const { task } of byId.values()) {
    for (const blockerId of task.blockedBy ?? []) {
      if (!blockerId) continue
      hasIncoming.add(task.id)
      const outcome = (task.dependencyOutcomes ?? []).find((d) => d.taskId === blockerId)
      edges.push({
        fromId: blockerId,
        toId: task.id,
        outcomeStatus: outcome?.completedAt ? 'completed' : undefined,
      })
    }
  }

  const missingBlockers = [
    ...new Set(
      edges.map((e) => e.fromId).filter((id) => !byId.has(id)),
    ),
  ]

  const roots: CardTreeNode[] = []
  for (const { task, lane } of byId.values()) {
    if (hasIncoming.has(task.id)) continue
    roots.push(makeDependencyNode(task.id, byId, new Set()))
  }

  const blockedLane: CardTreeNode[] = []
  for (const task of board.Blocked ?? []) {
      if (!task?.id) continue
      const lane = byId.get(task.id)?.lane ?? 'Blocked'
      blockedLane.push(makeNode(task, lane, byId, new Set([task.id])))
  }

  roots.sort((a, b) => a.task.title.localeCompare(b.task.title))
  return { roots, blockedLane, edges, missingBlockers }
}

function makeDependencyNode(
  taskId: string,
  byId: Map<string, { task: Task; lane: BoardLane | string }>,
  visiting: Set<string>,
): CardTreeNode {
  const entry = byId.get(taskId)
  if (!entry) {
    return {
      task: { id: taskId, title: taskId, description: '', status: 'Unknown' },
      lane: 'Unknown',
      children: [],
    }
  }
  if (visiting.has(taskId)) {
    return { task: entry.task, lane: entry.lane, children: [] }
  }
  visiting.add(taskId)
  const childIds = dependencyChildren(taskId, byId)
  const children = childIds.map((cid) => makeDependencyNode(cid, byId, visiting))
  visiting.delete(taskId)
  return { task: entry.task, lane: entry.lane, children }
}

export function stallChips(task: Task): string[] {
  const chips: string[] = []
  if (task.phaseCycleCapReached) chips.push('phaseCap')
  if (task.poAutoSkip) chips.push('poSkip')
  if ((task as Task & { forcePatchNextDevStep?: boolean }).forcePatchNextDevStep) {
    chips.push('forcePatch')
  }
  const until = (task as Task & { devDeferredUntilStep?: number }).devDeferredUntilStep
  if (until != null) chips.push(`defer→${until}`)
  return chips
}
