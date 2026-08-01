import type { AgentId, BoardLane } from '../types'

/** Match agent chat role to the card's current lane when using Discuss with agent. */
export function chatAgentForLane(lane: BoardLane | null): AgentId {
  switch (lane) {
    case 'QA':
      return 'qa'
    case 'Code Review':
      return 'cr'
    case 'In Progress':
      return 'dev'
    case 'Refinement':
    case 'Backlog':
    case 'Needs PO':
    case 'Needs User':
    case 'Pending Approval':
    case 'Blocked':
      return 'po'
    default:
      return 'dev'
  }
}
