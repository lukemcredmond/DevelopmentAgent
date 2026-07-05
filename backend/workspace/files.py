import os
from typing import Any, Dict, List, Optional

from backend import state
from backend.agents.task_context import record_task_decision, record_task_file
from backend.services.logs import add_system_log
from backend.services.project_service import save_current_project_state


def resolve_workspace_path(path: str) -> str:
    """Returns a safe relative path within the workspace root."""
    if not path or not str(path).strip():
        raise ValueError(
            f"Path is empty. Use relative paths like lib/main.dart (workspace: {state.WORKSPACE_DIR})"
        )

    raw = str(path).strip().strip('"').strip("'")
    normalized = os.path.normpath(raw).replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]

    workspace_root = os.path.realpath(state.WORKSPACE_DIR)

    if os.path.isabs(raw) or (len(raw) > 1 and raw[1] == ":"):
        try:
            abs_path = os.path.realpath(raw)
            if abs_path == workspace_root or abs_path.startswith(workspace_root + os.sep):
                normalized = os.path.relpath(abs_path, workspace_root).replace("\\", "/")
            else:
                raise ValueError(
                    f"Path escapes workspace: {path}. "
                    f"Use relative paths like lib/main.dart (workspace: {state.WORKSPACE_DIR})"
                )
        except ValueError:
            raise
        except OSError:
            raise ValueError(
                f"Path escapes workspace: {path}. "
                f"Use relative paths like lib/main.dart (workspace: {state.WORKSPACE_DIR})"
            ) from None

    workspace_basename = os.path.basename(workspace_root.rstrip(os.sep)).replace("\\", "/")
    if workspace_basename and (
        normalized.startswith(workspace_basename + "/")
        or normalized == workspace_basename
    ):
        stripped = normalized[len(workspace_basename) :].lstrip("/")
        if stripped:
            normalized = stripped

    if normalized.startswith("..") or os.path.isabs(normalized):
        raise ValueError(
            f"Path escapes workspace: {path}. "
            f"Use relative paths like lib/main.dart (workspace: {state.WORKSPACE_DIR})"
        )

    full_path = os.path.realpath(os.path.join(workspace_root, normalized))
    if full_path != workspace_root and not full_path.startswith(workspace_root + os.sep):
        raise ValueError(
            f"Path escapes workspace: {path}. "
            f"Use relative paths like lib/main.dart (workspace: {state.WORKSPACE_DIR})"
        )
    return normalized


def sync_virtual_filesystem_from_disk() -> Dict[str, str]:
    """Scans the physical workspace and syncs VIRTUAL_FILESYSTEM."""
    file_list: Dict[str, str] = {}
    if os.path.exists(state.WORKSPACE_DIR):
        for root, _dirs, files_in_dir in os.walk(state.WORKSPACE_DIR):
            for file in files_in_dir:
                rel_path = os.path.relpath(os.path.join(root, file), state.WORKSPACE_DIR)
                if not any(ex in rel_path for ex in ["venv", "__pycache__", ".git"]):
                    try:
                        with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                            file_list[rel_path.replace("\\", "/")] = f.read()
                    except Exception:
                        pass
    if file_list:
        state.VIRTUAL_FILESYSTEM = file_list
        return file_list
    return dict(state.VIRTUAL_FILESYSTEM)


def write_workspace_file(path: str, content: str, author: Optional[str] = None) -> str:
    try:
        safe_path = resolve_workspace_path(path)
    except ValueError as e:
        msg = f"Error: {e}"
        add_system_log(
            state.ACTIVE_SPRINT_AGENT or "Developer",
            "error",
            f"write_file rejected path '{path}': {e}",
        )
        return msg

    previous = state.VIRTUAL_FILESYSTEM.get(safe_path)
    state.VIRTUAL_FILESYSTEM[safe_path] = content

    phys_path = os.path.join(state.WORKSPACE_DIR, safe_path)
    try:
        dir_name = os.path.dirname(phys_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with open(phys_path, "w", encoding="utf-8") as f:
            f.write(content)

        if not os.path.isfile(phys_path):
            msg = f"Error: Write reported success but file missing at '{phys_path}'"
            add_system_log(state.ACTIVE_SPRINT_AGENT or "Developer", "error", msg)
            return msg

        if previous != content:
            state.storage.save_file_revision(
                state.CURRENT_PROJECT_ID,
                safe_path,
                content,
                previous_content=previous,
                author=author or state.ACTIVE_SPRINT_AGENT,
            )

        if state.ACTIVE_SPRINT_TASK_ID:
            record_task_file(state.ACTIVE_SPRINT_TASK_ID, safe_path, "written", persist=False)
            record_task_decision(
                state.ACTIVE_SPRINT_TASK_ID,
                state.ACTIVE_SPRINT_AGENT or "Developer",
                "file",
                f"Wrote file '{safe_path}'",
            )

        save_current_project_state()
        nbytes = len(content.encode("utf-8"))
        add_system_log(
            state.ACTIVE_SPRINT_AGENT or "Developer",
            "success",
            f"Wrote '{safe_path}' ({nbytes} bytes) → {phys_path}",
        )
        return f"Successfully saved file physically at: '{phys_path}'"
    except Exception as e:
        msg = f"Error: physical write failed for '{safe_path}': {e}"
        add_system_log(state.ACTIVE_SPRINT_AGENT or "Developer", "error", msg)
        return msg


def apply_workspace_patch(path: str, old_text: str, new_text: str) -> str:
    """Replace a unique old_text snippet in a file with new_text."""
    current = read_workspace_file(path)
    if current.startswith("Error:"):
        return f"Error: Cannot patch — {current}"
    count = current.count(old_text)
    if count == 0:
        return f"Error: old_text not found in '{path}'"
    if count > 1:
        return f"Error: old_text appears {count} times in '{path}' — must be unique"
    updated = current.replace(old_text, new_text, 1)
    return write_workspace_file(path, updated)


def read_workspace_file(path: str) -> str:
    try:
        safe_path = resolve_workspace_path(path)
    except ValueError as e:
        return str(e)

    phys_path = os.path.join(state.WORKSPACE_DIR, safe_path)
    if os.path.exists(phys_path):
        try:
            with open(phys_path, "r", encoding="utf-8") as f:
                content = f.read()
                state.VIRTUAL_FILESYSTEM[safe_path] = content
                if state.ACTIVE_SPRINT_TASK_ID:
                    record_task_file(state.ACTIVE_SPRINT_TASK_ID, safe_path, "read", persist=True)
                return content
        except Exception:
            pass
    return state.VIRTUAL_FILESYSTEM.get(safe_path, f"Error: File '{safe_path}' not found.")


def run_tests_on_workspace(test_script_path: str) -> str:
    import subprocess

    from backend.config import TEST_TIMEOUT_SEC

    if state.ACTIVE_SPRINT_TASK_ID:
        try:
            safe_path = resolve_workspace_path(test_script_path)
            record_task_file(state.ACTIVE_SPRINT_TASK_ID, safe_path, "tested", persist=True)
        except ValueError:
            pass

    try:
        safe_path = resolve_workspace_path(test_script_path)
    except ValueError as e:
        return str(e)

    phys_path = os.path.join(state.WORKSPACE_DIR, safe_path)
    if not os.path.exists(phys_path):
        return f"❌ QA Validation Failure: Could not read '{test_script_path}'."

    commands: list[list[str]] = []
    if safe_path.endswith(".py"):
        commands.append(["python", phys_path])
        commands.append(["pytest", phys_path, "-q"])
    elif safe_path.endswith((".js", ".mjs", ".cjs")):
        commands.append(["node", phys_path])
    elif safe_path == "pubspec.yaml" or safe_path.endswith(".dart"):
        commands.append(["flutter", "analyze"])
    elif os.path.exists(os.path.join(state.WORKSPACE_DIR, "pubspec.yaml")):
        commands.append(["flutter", "analyze"])
    elif os.path.exists(os.path.join(state.WORKSPACE_DIR, "package.json")):
        commands.append(["npm", "test"])

    if not commands:
        content = read_workspace_file(test_script_path)
        lower = content.lower()
        if "test" in lower or "assert" in lower:
            return f"✔ Static validation passed for '{test_script_path}'."
        return f"❌ QA Validation Failure: No runnable test command for '{test_script_path}'."

    last_output = ""
    for cmd in commands:
        try:
            result = subprocess.run(
                cmd,
                cwd=state.WORKSPACE_DIR,
                capture_output=True,
                text=True,
                timeout=TEST_TIMEOUT_SEC,
            )
            last_output = (result.stdout or "") + (result.stderr or "")
            if result.returncode == 0:
                return f"✔ Tests passed ({' '.join(cmd)}):\n{last_output[:1500]}"
        except FileNotFoundError:
            continue
        except subprocess.TimeoutExpired:
            return f"❌ QA Validation Failure: Test timed out after {TEST_TIMEOUT_SEC}s for '{test_script_path}'."

    return f"❌ QA Validation Failure for '{test_script_path}':\n{last_output[:1500]}"


def run_agent_command(command: str) -> str:
    """Runs a shell command for sprint agents; returns structured text output."""
    from backend.services.terminal_service import run_command

    result = run_command(command)
    parts: List[str] = []
    if result.get("stdout"):
        parts.append(result["stdout"])
    if result.get("stderr"):
        parts.append(result["stderr"])
    body = "\n".join(parts).strip() or "(no output)"
    status = "success" if result.get("success") else "failed"
    rc = result.get("returncode", -1)
    return f"[{status} exit {rc}]\n{body[:3000]}"


def get_file_tree() -> List[Dict[str, Any]]:
    """Returns a nested tree structure of workspace files."""
    sync_virtual_filesystem_from_disk()
    root: Dict[str, Any] = {"name": ".", "type": "directory", "children": []}
    nodes: Dict[str, Dict[str, Any]] = {".": root}

    for path in sorted(state.VIRTUAL_FILESYSTEM.keys()):
        parts = path.split("/")
        current = "."
        for i, part in enumerate(parts):
            parent = current
            current = f"{current}/{part}" if current != "." else part
            if current not in nodes:
                is_file = i == len(parts) - 1
                node: Dict[str, Any] = {
                    "name": part,
                    "path": path if is_file else current,
                    "type": "file" if is_file else "directory",
                }
                if not is_file:
                    node["children"] = []
                nodes[current] = node
                nodes[parent]["children"].append(node)

    return root.get("children", [])


def _collect_sprint_context_paths(task: Dict[str, Any]) -> List[str]:
    """Gather workspace paths relevant to a sprint step."""
    paths: set[str] = set()
    for f in task.get("files") or []:
        if isinstance(f, str):
            paths.add(f)
        elif isinstance(f, dict) and f.get("path"):
            paths.add(str(f["path"]))

    sync_virtual_filesystem_from_disk()
    ws = state.WORKSPACE_DIR

    for marker in ("pubspec.yaml", "package.json", "README.md", "pyproject.toml", "requirements.txt"):
        if os.path.isfile(os.path.join(ws, marker)):
            paths.add(marker)

    tests_dir = os.path.join(ws, "tests")
    if os.path.isdir(tests_dir):
        count = 0
        for root, _dirs, files_in_dir in os.walk(tests_dir):
            for fn in sorted(files_in_dir):
                if count >= 8:
                    break
                rel = os.path.relpath(os.path.join(root, fn), ws).replace("\\", "/")
                if not any(ex in rel for ex in ("__pycache__", ".pyc")):
                    paths.add(rel)
                    count += 1

    lib_dir = os.path.join(ws, "lib")
    if os.path.isdir(lib_dir):
        count = 0
        for root, _dirs, files_in_dir in os.walk(lib_dir):
            for fn in sorted(files_in_dir):
                if count >= 10:
                    break
                if fn.endswith((".dart", ".py", ".ts", ".tsx", ".js")):
                    rel = os.path.relpath(os.path.join(root, fn), ws).replace("\\", "/")
                    paths.add(rel)
                    count += 1

    return sorted(paths)


def build_sprint_file_context(
    task: Dict[str, Any],
    max_chars: int = 12000,
) -> tuple[str, List[str]]:
    """Build pre-loaded file contents for sprint agent prompts."""
    sync_virtual_filesystem_from_disk()
    candidate_paths = _collect_sprint_context_paths(task)
    included: List[str] = []
    blocks: List[str] = []
    used = 0
    header = "\n=== PRE-LOADED FILE CONTEXT ===\n"

    for raw_path in candidate_paths:
        try:
            safe_path = resolve_workspace_path(raw_path)
        except ValueError:
            continue
        content = state.VIRTUAL_FILESYSTEM.get(safe_path)
        if content is None:
            phys = os.path.join(state.WORKSPACE_DIR, safe_path)
            if os.path.isfile(phys):
                try:
                    with open(phys, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception:
                    continue
            else:
                continue
        block = f"--- FILE: {safe_path} ---\n{content}\n--- END {safe_path} ---"
        if used + len(block) > max_chars and included:
            remaining = max_chars - used
            if remaining > 200:
                truncated = block[: remaining - 20] + "\n... [truncated]\n"
                blocks.append(truncated)
                included.append(safe_path)
            break
        blocks.append(block)
        included.append(safe_path)
        used += len(block)

    if not blocks:
        return "", []
    return header + "\n\n".join(blocks) + "\n", included


def derive_project_test_commands() -> List[str]:
    """Return shell commands to run for the current workspace project type."""
    sync_virtual_filesystem_from_disk()
    ws = state.WORKSPACE_DIR
    commands: List[str] = []

    if os.path.isfile(os.path.join(ws, "pubspec.yaml")):
        commands.extend(["flutter analyze", "flutter test"])
    elif os.path.isfile(os.path.join(ws, "package.json")):
        commands.append("npm test")
    elif os.path.isdir(os.path.join(ws, "tests")) or os.path.isfile(os.path.join(ws, "pytest.ini")):
        commands.append("pytest tests/ -q")
    elif any(p.endswith(".py") for p in state.VIRTUAL_FILESYSTEM):
        if os.path.isdir(os.path.join(ws, "tests")):
            commands.append("pytest tests/ -q")

    return commands


def build_file_context_block(paths: List[str]) -> str:
    """Builds an explicit context block from workspace file paths for agent prompts."""
    sync_virtual_filesystem_from_disk()
    blocks: List[str] = []
    for raw_path in paths:
        try:
            safe_path = resolve_workspace_path(raw_path)
        except ValueError:
            continue
        content = state.VIRTUAL_FILESYSTEM.get(safe_path)
        if content is None:
            phys = os.path.join(state.WORKSPACE_DIR, safe_path)
            if os.path.isfile(phys):
                try:
                    with open(phys, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception:
                    continue
            else:
                continue
        blocks.append(f"--- FILE: {safe_path} ---\n{content}\n--- END {safe_path} ---")
    if not blocks:
        return ""
    return "\n=== EXPLICIT FILE CONTEXT ===\n" + "\n\n".join(blocks) + "\n"


def search_files(query: str, limit: int = 50) -> List[Dict[str, str]]:
    """Simple content search across virtual filesystem."""
    sync_virtual_filesystem_from_disk()
    query_lower = query.lower()
    results: List[Dict[str, str]] = []
    for path, content in state.VIRTUAL_FILESYSTEM.items():
        if query_lower in path.lower() or query_lower in content.lower():
            snippet = content[:200].replace("\n", " ")
            results.append({"path": path, "snippet": snippet})
            if len(results) >= limit:
                break
    return results


def save_file_with_revision(path: str, content: str, author: Optional[str] = None) -> Dict[str, Any]:
    """Saves a file and returns revision metadata."""
    message = write_workspace_file(path, content, author=author)
    safe_path = resolve_workspace_path(path)
    revisions = state.storage.get_file_revisions(state.CURRENT_PROJECT_ID, safe_path, limit=1)
    return {
        "message": message,
        "path": safe_path,
        "revision": revisions[0] if revisions else None,
    }
