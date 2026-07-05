from backend import state
from backend.agents.scrum_agent import ScrumAgent
from backend.agents.task_context import record_task_git_commit
from backend.agents.tools import Tool
from backend.services.brief_service import PO_SMALLEST_TASKS_GUIDANCE
from backend.services.board_service import append_backlog_tasks, move_board_stage
from backend.services.git_service import git_commit, git_diff, git_init, git_status
from backend.workspace.files import (
    apply_workspace_patch,
    read_workspace_file,
    run_agent_command,
    run_tests_on_workspace,
    search_files,
    write_workspace_file,
)

agent_po = ScrumAgent(
    role="Product Owner",
    model="llama3:8b",
    system_prompt=(
        "You are the Product Owner. You decompose project briefs into backlog features (user stories) "
        "as JSON arrays. When developers ask questions, you clarify requirements and acceptance criteria. "
        "When the user adds features, refine them into clear developer-ready stories. "
        "Use update_board to move tasks from 'Needs PO' back to 'In Progress' when clarification is done. "
        "Use add_backlog_tasks to add new stories to the Backlog; when splitting a large or stuck card, "
        "pass split_from_task_id so the original moves to Done with a split note. "
        f"{PO_SMALLEST_TASKS_GUIDANCE}"
    ),
)

agent_dev = ScrumAgent(
    role="Developer",
    model="qwen2.5-coder:14b",
    system_prompt=(
        "You implement features from the backlog. Use apply_patch for edits to existing files "
        "and write_file for new files. "
        "If requirements are unclear, escalate to the Product Owner by moving the task to 'Needs PO'. "
        "When implementation is complete, move the task to 'QA' for validation."
    ),
)

agent_cr = ScrumAgent(
    role="Code Reviewer",
    model="qwen2.5-coder:7b",
    system_prompt=(
        "You sit between Developer and QA. Audit the newly written files for logical bugs, layout problems, "
        "styling issues, or security flaws. On success, advance the task to QA. On failure, return to Developer."
    ),
)

agent_qa = ScrumAgent(
    role="QA Tester",
    model="qwen2.5-coder:7b",
    system_prompt=(
        "You validate completed features against the project brief. "
        "Use read_file and run_test. Approve to 'Done' or return failures to 'In Progress'."
    ),
)

tool_write = Tool(
    name="write_file",
    description="Creates or modifies code and configurations inside workspace directories.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    },
    func=write_workspace_file,
)

tool_apply_patch = Tool(
    name="apply_patch",
    description=(
        "Replace a unique old_text snippet with new_text in an existing workspace file. "
        "You must call read_file on the same path in this step first; copy old_text verbatim "
        "from that read_file output. Prefer for small edits; use write_file for new files."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
        },
        "required": ["path", "old_text", "new_text"],
    },
    func=apply_workspace_patch,
)

tool_read = Tool(
    name="read_file",
    description="Reads plain text source code contents from any workspace path.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    func=read_workspace_file,
)

tool_test = Tool(
    name="run_test",
    description="Runs automated testing validations on codebase paths.",
    parameters={
        "type": "object",
        "properties": {"test_script_path": {"type": "string"}},
        "required": ["test_script_path"],
    },
    func=run_tests_on_workspace,
)

def _format_search_results(query: str, limit: int = 20) -> str:
    results = search_files(query, limit=limit)
    if not results:
        return f"No matches for '{query}'."
    lines = [f"Search results for '{query}' ({len(results)} match(es)):"]
    for r in results:
        lines.append(f"- {r['path']}: {r.get('snippet', '')[:120]}")
    return "\n".join(lines)


def _guarded_update_board(task_id: str, target_lane: str) -> str:
    from backend.agents.task_context import find_task_by_id, get_task_lane, normalize_task
    from backend.services.sprint_service import qa_gate_blocks_done

    if target_lane == "Done" and state.ACTIVE_SPRINT_AGENT == "QA Tester":
        task = find_task_by_id(task_id)
        if task and get_task_lane(task_id) == "QA":
            normalize_task(task)
            blocked, reason = qa_gate_blocks_done(task)
            if blocked:
                return f"Error: {reason}"
    return move_board_stage(task_id, target_lane)


tool_search = Tool(
    name="search_code",
    description="Search workspace file paths and contents for a query string.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    },
    func=lambda query, limit=20: _format_search_results(query, limit=int(limit) if limit else 20),
)

tool_board = Tool(
    name="update_board",
    description="Updates Kanban Scrum board column positions.",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "target_lane": {"type": "string"},
        },
        "required": ["task_id", "target_lane"],
    },
    func=_guarded_update_board,
)

tool_add_backlog_tasks = Tool(
    name="add_backlog_tasks",
    description=(
        "Add one or more new tasks to the Backlog. When breaking a large card into subtasks, "
        "set split_from_task_id to the source task ID — the source moves to Done with a split note."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "acceptanceCriteria": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "blockedBy": {"type": "array", "items": {"type": "string"}},
                        "priority": {"type": "number"},
                    },
                    "required": ["title", "description"],
                },
            },
            "split_from_task_id": {"type": "string"},
        },
        "required": ["tasks"],
    },
    func=lambda tasks, split_from_task_id=None: append_backlog_tasks(
        tasks, split_from_task_id=split_from_task_id
    ),
)

tool_git_status = Tool(
    name="git_status",
    description="Returns git status for the workspace repository.",
    parameters={"type": "object", "properties": {}, "required": []},
    func=lambda: git_status(),
)

tool_git_diff = Tool(
    name="git_diff",
    description="Returns git diff for the workspace, optionally for a specific path.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": [],
    },
    func=lambda path=None: git_diff(path),
)


def _invoke_git_commit(message: str):
    result = git_commit(message)
    if result.get("success") and result.get("hash") and state.ACTIVE_SPRINT_TASK_ID:
        record_task_git_commit(
            state.ACTIVE_SPRINT_TASK_ID,
            {
                "hash": result["hash"],
                "message": message,
                "remoteUrl": result.get("remoteUrl"),
            },
        )
    return result


tool_git_commit = Tool(
    name="git_commit",
    description="Stages all changes and commits with the given message.",
    parameters={
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    },
    func=_invoke_git_commit,
)

tool_git_init = Tool(
    name="git_init",
    description="Initializes a git repository in the workspace.",
    parameters={"type": "object", "properties": {}, "required": []},
    func=lambda: git_init(),
)

tool_run_command = Tool(
    name="run_command",
    description=(
        "Run a single shell command in the workspace root "
        "(e.g. flutter analyze, dart fix --apply, npm test)."
    ),
    parameters={
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
    func=lambda command: run_agent_command(command),
)

agent_po.register_tool(tool_read)
agent_po.register_tool(tool_board)
agent_po.register_tool(tool_add_backlog_tasks)

agent_dev.register_tool(tool_read)
agent_dev.register_tool(tool_write)
agent_dev.register_tool(tool_apply_patch)
agent_dev.register_tool(tool_board)
agent_dev.register_tool(tool_run_command)
agent_dev.register_tool(tool_search)
agent_dev.register_tool(tool_git_status)
agent_dev.register_tool(tool_git_diff)
agent_dev.register_tool(tool_git_commit)

agent_cr.register_tool(tool_read)
agent_cr.register_tool(tool_apply_patch)
agent_cr.register_tool(tool_board)
agent_cr.register_tool(tool_search)
agent_cr.register_tool(tool_git_diff)

agent_qa.register_tool(tool_read)
agent_qa.register_tool(tool_test)
agent_qa.register_tool(tool_run_command)
agent_qa.register_tool(tool_search)
agent_qa.register_tool(tool_board)

AGENT_MAP = {
    "po": agent_po,
    "dev": agent_dev,
    "cr": agent_cr,
    "qa": agent_qa,
}

AGENT_LABELS = {
    "po": "Product Owner",
    "dev": "Developer",
    "cr": "Code Reviewer",
    "qa": "QA Tester",
}
