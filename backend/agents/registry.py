from backend.agents.scrum_agent import ScrumAgent
from backend.agents.tools import Tool
from backend.services.board_service import move_board_stage
from backend.services.git_service import git_commit, git_diff, git_init, git_status
from backend.workspace.files import (
    read_workspace_file,
    run_agent_command,
    run_tests_on_workspace,
    write_workspace_file,
)

agent_po = ScrumAgent(
    role="Product Owner",
    model="llama3:8b",
    system_prompt=(
        "You are the Product Owner. You decompose project briefs into backlog features (user stories) "
        "as JSON arrays. When developers ask questions, you clarify requirements and acceptance criteria. "
        "When the user adds features, refine them into clear developer-ready stories. "
        "Use update_board to move tasks from 'Needs PO' back to 'In Progress' when clarification is done."
    ),
)

agent_dev = ScrumAgent(
    role="Developer",
    model="qwen2.5-coder:14b",
    system_prompt=(
        "You implement features from the backlog. Write code with write_file. "
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
    func=move_board_stage,
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

tool_git_commit = Tool(
    name="git_commit",
    description="Stages all changes and commits with the given message.",
    parameters={
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    },
    func=lambda message: git_commit(message),
)

tool_git_init = Tool(
    name="git_init",
    description="Initializes a git repository in the workspace.",
    parameters={"type": "object", "properties": {}, "required": []},
    func=lambda: git_init(),
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

agent_dev.register_tool(tool_read)
agent_dev.register_tool(tool_write)
agent_dev.register_tool(tool_board)
agent_dev.register_tool(tool_run_command)
agent_dev.register_tool(tool_git_status)
agent_dev.register_tool(tool_git_diff)
agent_dev.register_tool(tool_git_commit)

agent_cr.register_tool(tool_read)
agent_cr.register_tool(tool_board)
agent_cr.register_tool(tool_git_diff)

agent_qa.register_tool(tool_read)
agent_qa.register_tool(tool_test)
agent_qa.register_tool(tool_run_command)
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
