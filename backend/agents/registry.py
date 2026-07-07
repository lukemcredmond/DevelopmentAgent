from typing import Optional

from backend import state
from backend.agents.scrum_agent import ScrumAgent
from backend.agents.task_context import record_task_git_commit
from backend.agents.tools import Tool
from backend.services.brief_service import PO_SMALLEST_TASKS_GUIDANCE
from backend.services.board_service import append_backlog_tasks, move_board_stage
from backend.services.subtask_service import append_subtasks, escape_subtask_loop
from backend.services.git_service import git_commit, git_diff, git_init, git_status
from backend.workspace.files import (
    apply_workspace_patch,
    delete_workspace_file,
    glob_workspace,
    grep_workspace,
    list_workspace_dir,
    read_workspace_file,
    run_agent_command,
    run_tests_on_workspace,
    search_files,
    write_workspace_file,
)
from backend.storage.code_index import format_semantic_search_results
from backend.workspace.web_search import web_search

agent_po = ScrumAgent(
    role="Product Owner",
    model="llama3:8b",
    system_prompt=(
        "You are the Product Owner. You decompose project briefs into backlog features (user stories) "
        "as JSON arrays. When developers ask questions, you clarify requirements and acceptance criteria. "
        "When the user adds features, refine them into clear developer-ready stories. "
        "Use update_board to move tasks from 'Needs PO' back to 'In Progress' when clarification is done. "
        "For cards in 'Refinement', answer developer questions, update AC/description, use add_backlog_tasks "
        "to split scope, then move to 'Backlog' when refinementComplete. "
        "Use add_backlog_tasks to add new stories to the Backlog; when splitting a large or stuck card, "
        "pass split_from_task_id so the original moves to Done with a split note. "
        "Use add_subtasks for ordered child todos under a parent card (during refinement set executionOrder). "
        "Invoke add_backlog_tasks yourself — never instruct the user to call it. "
        "Prefer acting (split, move board) over asking clarifying questions when acceptance criteria exist. "
        "Use grep and glob_file_search to explore the codebase; prefer grep over search_code for patterns. "
        f"{PO_SMALLEST_TASKS_GUIDANCE}"
    ),
)

agent_dev = ScrumAgent(
    role="Developer",
    model="qwen2.5-coder:14b",
    system_prompt=(
        "You implement features from the backlog. Use apply_patch for edits to existing files "
        "and write_file for new files. Use grep and glob_file_search to find symbols and files. "
        "If requirements are unclear, escalate to the Product Owner by moving the task to 'Needs PO'. "
        "When a discrete tool step is needed (lint, test, file edit), use add_subtasks to spawn ordered "
        "child todos that must complete before this card advances. "
        "When implementation is complete, move the task to 'QA' for validation. "
        "Continue iterating on test failures without asking the user unless blocked repeatedly."
    ),
)

agent_cr = ScrumAgent(
    role="Code Reviewer",
    model="qwen2.5-coder:7b",
    system_prompt=(
        "You sit between Developer and QA. Audit the newly written files for logical bugs, layout problems, "
        "styling issues, or security flaws. Use grep to locate relevant code. "
        "On success, advance the task to QA. On failure, return to Developer."
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

def _invoke_read_file(path: str, start_line=None, end_line=None) -> str:
    return read_workspace_file(path, start_line=start_line, end_line=end_line)


tool_read = Tool(
    name="read_file",
    description=(
        "Reads plain text source code from a workspace path. "
        "Use start_line/end_line (1-based) to read a slice of large files."
    ),
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
        },
        "required": ["path"],
    },
    func=_invoke_read_file,
)

tool_list_dir = Tool(
    name="list_dir",
    description="List files and subdirectories in a workspace path (default: workspace root).",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": [],
    },
    func=lambda path=".", limit=200: list_workspace_dir(path, limit=int(limit or 200)),
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

def _format_grep_results(
    pattern: str,
    path=None,
    glob=None,
    case_insensitive=False,
    context_lines=0,
    limit=100,
) -> str:
    results = grep_workspace(
        pattern,
        path=path,
        glob=glob,
        case_insensitive=bool(case_insensitive),
        context_lines=int(context_lines or 0),
        limit=int(limit or 100),
    )
    if not results:
        return f"No matches for pattern '{pattern}'."
    lines = [f"Grep '{pattern}' ({len(results)} match(es)):"]
    for r in results:
        loc = f"{r['path']}:{r.get('line', 0)}"
        lines.append(f"- {loc}: {r.get('content', '')[:200]}")
    return "\n".join(lines)


def _format_glob_results(pattern: str, limit: int = 200) -> str:
    paths = glob_workspace(pattern, limit=int(limit or 200))
    if not paths:
        return f"No files match glob '{pattern}'."
    lines = [f"Glob '{pattern}' ({len(paths)} file(s)):"]
    lines.extend(f"- {p}" for p in paths)
    return "\n".join(lines)


def _format_search_results(query: str, limit: int = 20) -> str:
    results = search_files(query, limit=limit)
    if not results:
        return f"No matches for '{query}'."
    lines = [f"Search results for '{query}' ({len(results)} match(es)):"]
    for r in results:
        lines.append(f"- {r['path']}: {r.get('snippet', '')[:120]}")
    return "\n".join(lines)


def _guarded_update_board(task_id: str, target_lane: str, user_question: Optional[str] = None) -> str:
    from backend.agents.task_context import find_task_by_id, get_task_lane, normalize_task
    from backend.services.needs_user_guard import should_escalate_to_needs_user
    from backend.services.sprint_service import (
        _redirect_to_needs_po,
        _try_move_to_needs_user,
        dev_gate_blocks_advance,
        qa_gate_blocks_done,
    )

    task = find_task_by_id(task_id)
    if task:
        normalize_task(task)
        lane = get_task_lane(task_id) or ""
        target_stripped = target_lane.strip()
        if target_stripped == "Needs User":
            question = (user_question or task.get("userQuestion") or "").strip()
            if not question:
                return (
                    "Error: Needs User requires a specific user_question argument or task.userQuestion. "
                    "Use Needs PO for requirement clarification."
                )
            allowed, block_reason = should_escalate_to_needs_user(task, question)
            if not allowed:
                if block_reason == "clarification_use_po":
                    if _redirect_to_needs_po(task_id, task, question, kind="board_clarification"):
                        return f"Task {task_id} routed to Needs PO (clarification, not Needs User)."
                    return "Error: Could not route to Needs PO — PO round-trip limit reached."
                if block_reason in (
                    "duplicate_question",
                    "cooldown_active",
                    "same_reason_hash",
                    "already_in_needs_user",
                ):
                    return (
                        f"Error: Needs User blocked ({block_reason}). "
                        "Prior user answer applies — continue implementation."
                    )
                return "Error: Needs User escalation not allowed for this question."
            if _try_move_to_needs_user(task_id, task, question, kind="dev_board_move"):
                return f"Task {task_id} moved to 'Needs User'."
            return "Error: Needs User move blocked (cap or policy)."
        if target_lane == "Done" and state.ACTIVE_SPRINT_AGENT == "QA Tester" and lane == "QA":
            blocked, reason = qa_gate_blocks_done(task)
            if blocked:
                return f"Error: {reason}"
        if state.ACTIVE_SPRINT_AGENT == "Developer" and lane == "Refinement":
            blocked_targets = {"In Progress", "Code Review", "QA", "Done", "Backlog"}
            if target_lane.strip() in blocked_targets:
                return (
                    "Error: During refinement, Developer may only move to 'Needs PO' "
                    "when blocked — do not advance to implementation lanes."
                )
        if (
            state.ACTIVE_SPRINT_AGENT == "Product Owner"
            and lane == "Refinement"
            and target_lane.strip() == "Backlog"
        ):
            task["refinementComplete"] = True
            task["refinementStatus"] = "ready"
        if state.ACTIVE_SPRINT_AGENT == "Developer" and lane == "In Progress":
            target_upper = target_lane.strip()
            if target_upper in ("Code Review", "QA", "Done"):
                from backend.services.subtask_service import subtask_gate_blocks_advance

                blocked, reason = subtask_gate_blocks_advance(task)
                if blocked:
                    return f"Error: {reason}"
                blocked, reason = dev_gate_blocks_advance(task)
                if blocked:
                    return f"Error: {reason}"
    return move_board_stage(task_id, target_lane)


tool_search = Tool(
    name="search_code",
    description="Legacy substring search. Prefer grep for patterns and glob_file_search for file discovery.",
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

tool_grep = Tool(
    name="grep",
    description=(
        "Search file contents with regex pattern (ripgrep when available). "
        "Returns path, line number, and matching line."
    ),
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
            "glob": {"type": "string"},
            "case_insensitive": {"type": "boolean"},
            "context_lines": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "required": ["pattern"],
    },
    func=lambda pattern, path=None, glob=None, case_insensitive=False, context_lines=0, limit=100: _format_grep_results(
        pattern, path, glob, case_insensitive, context_lines, limit
    ),
)

tool_glob = Tool(
    name="glob_file_search",
    description="Find workspace files matching a glob pattern (e.g. **/*.dart, lib/**/*.py).",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["pattern"],
    },
    func=lambda pattern, limit=200: _format_glob_results(pattern, limit),
)

tool_semantic = Tool(
    name="semantic_search",
    description=(
        "Semantic codebase search via Qdrant embeddings. "
        "Find code by meaning when grep patterns are unknown."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer"},
        },
        "required": ["query"],
    },
    func=lambda query, limit=8: format_semantic_search_results(query, limit=int(limit or 8)),
)

tool_delete = Tool(
    name="delete_file",
    description="Delete a file from the workspace. Requires tool approval when enabled.",
    parameters={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    func=delete_workspace_file,
)

tool_web_search = Tool(
    name="web_search",
    description=(
        "Search the public web for documentation, APIs, or examples. "
        "Requires enableWebSearch in workflow settings."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "max_results": {"type": "integer"},
        },
        "required": ["query"],
    },
    func=lambda query, max_results=5: web_search(query, max_results=int(max_results) if max_results else 5),
)

tool_board = Tool(
    name="update_board",
    description="Updates Kanban Scrum board column positions.",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "target_lane": {"type": "string"},
            "user_question": {
                "type": "string",
                "description": "Required when target_lane is Needs User — specific question for the user.",
            },
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
                        "workType": {
                            "type": "string",
                            "enum": ["planning", "implementation", "review", "qa", "user_action"],
                        },
                        "requiresDev": {"type": "boolean"},
                        "requiresQa": {"type": "boolean"},
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

tool_add_subtasks = Tool(
    name="add_subtasks",
    description=(
        "Add ordered child todos under the current parent task. Each subtask must reach Done "
        "before the parent can complete. Use when a step needs its own tool execution cycle "
        "(run_command, write_file, etc.). Subtasks run in executionOrder; nested subtasks are allowed "
        "up to maxSubtaskDepth. Prefer add_subtasks over add_backlog_tasks when work belongs to this card."
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
                        "executionOrder": {"type": "number"},
                        "order": {"type": "number"},
                    },
                    "required": ["title", "description"],
                },
            },
            "parent_task_id": {
                "type": "string",
                "description": "Parent todo ID (defaults to active sprint task)",
            },
        },
        "required": ["tasks"],
    },
    func=lambda tasks, parent_task_id=None: append_subtasks(
        parent_task_id or state.ACTIVE_SPRINT_TASK_ID or "",
        tasks,
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
        "Run shell command(s) in the workspace root. "
        "Use background=true for long-running servers. "
        "When allowChainedCommands is enabled, use && or ; to chain steps."
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "background": {"type": "boolean"},
        },
        "required": ["command"],
    },
    func=lambda command, background=False: run_agent_command(command, background=bool(background)),
)

def configure_agent_tools(ws: dict | None = None) -> None:
    """Register agent tools based on workflow settings (reduces schema bloat)."""
    from backend.services.workflow_settings import get_workflow_settings

    settings = ws or get_workflow_settings()
    enable_web = bool(settings.get("enableWebSearch"))
    enable_semantic = bool(settings.get("enableSemanticSearch", True))
    refinement_mode = bool(state.REFINEMENT_MODE)

    for agent in (agent_po, agent_dev, agent_cr, agent_qa):
        agent.registry.clear()

    agent_po.registry.register(tool_read)
    agent_po.registry.register(tool_list_dir)
    agent_po.registry.register(tool_board)
    agent_po.registry.register(tool_add_backlog_tasks)
    agent_po.registry.register(tool_add_subtasks)
    agent_po.registry.register(tool_grep)
    agent_po.registry.register(tool_glob)
    if enable_semantic:
        agent_po.registry.register(tool_semantic)
    if enable_web:
        agent_po.registry.register(tool_web_search)

    agent_dev.registry.register(tool_read)
    agent_dev.registry.register(tool_list_dir)
    if refinement_mode:
        agent_dev.registry.register(tool_board)
        agent_dev.registry.register(tool_add_subtasks)
        agent_dev.registry.register(tool_grep)
        agent_dev.registry.register(tool_glob)
        agent_dev.registry.register(tool_search)
        agent_dev.registry.register(tool_git_status)
        agent_dev.registry.register(tool_git_diff)
        if enable_semantic:
            agent_dev.registry.register(tool_semantic)
        if enable_web:
            agent_dev.registry.register(tool_web_search)
    else:
        agent_dev.registry.register(tool_write)
        agent_dev.registry.register(tool_apply_patch)
        agent_dev.registry.register(tool_delete)
        agent_dev.registry.register(tool_board)
        agent_dev.registry.register(tool_add_subtasks)
        agent_dev.registry.register(tool_run_command)
        agent_dev.registry.register(tool_grep)
        agent_dev.registry.register(tool_glob)
        agent_dev.registry.register(tool_search)
        agent_dev.registry.register(tool_git_status)
        agent_dev.registry.register(tool_git_diff)
        agent_dev.registry.register(tool_git_commit)
        if enable_semantic:
            agent_dev.registry.register(tool_semantic)
        if enable_web:
            agent_dev.registry.register(tool_web_search)

    agent_cr.registry.register(tool_read)
    agent_cr.registry.register(tool_list_dir)
    agent_cr.registry.register(tool_apply_patch)
    agent_cr.registry.register(tool_board)
    agent_cr.registry.register(tool_grep)
    agent_cr.registry.register(tool_glob)
    agent_cr.registry.register(tool_search)
    if enable_semantic:
        agent_cr.registry.register(tool_semantic)
    if enable_web:
        agent_cr.registry.register(tool_web_search)

    agent_qa.registry.register(tool_read)
    agent_qa.registry.register(tool_list_dir)
    agent_qa.registry.register(tool_test)
    agent_qa.registry.register(tool_run_command)
    agent_qa.registry.register(tool_grep)
    agent_qa.registry.register(tool_glob)
    agent_qa.registry.register(tool_search)
    agent_qa.registry.register(tool_board)
    if enable_semantic:
        agent_qa.registry.register(tool_semantic)
    if enable_web:
        agent_qa.registry.register(tool_web_search)

    from backend.services.mcp_tools import reregister_mcp_tools_on_agents
    from backend.services.logs import add_system_log

    mcp_count = reregister_mcp_tools_on_agents()
    for agent in (agent_po, agent_dev, agent_cr, agent_qa):
        if not agent.registry.tool_names():
            add_system_log(
                "System",
                "error",
                f"Agent {agent.role} has no tools registered after configure_agent_tools",
            )
    if mcp_count:
        add_system_log("System", "info", f"Re-attached {mcp_count} MCP tool(s) to agents")


configure_agent_tools()

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
