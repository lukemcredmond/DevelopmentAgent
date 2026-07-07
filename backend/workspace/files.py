import fnmatch
import json
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from backend import state
from backend.agents.task_context import record_task_decision, record_task_file
from backend.services.logs import add_system_log
from backend.services.project_service import save_current_project_state
from backend.services.workflow_settings import get_workflow_settings


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


INDEX_SKIP_DIR_PARTS = {
    "venv",
    "__pycache__",
    ".git",
    "node_modules",
    ".dart_tool",
    "build",
    "dist",
    ".pub-cache",
    ".idea",
    ".vscode",
    "coverage",
    ".pytest_cache",
}

INDEXABLE_EXTENSIONS = {
    ".dart",
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".html",
    ".css",
    ".scss",
    ".sql",
    ".sh",
    ".ps1",
    ".cs",
    ".java",
    ".go",
    ".rs",
    ".xml",
    ".gradle",
    ".properties",
    ".env.example",
}


def _path_should_skip_index(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    return any(part in INDEX_SKIP_DIR_PARTS for part in parts)


def _is_indexable_file(rel_path: str) -> bool:
    lower = rel_path.lower()
    _, ext = os.path.splitext(lower)
    if ext in INDEXABLE_EXTENSIONS:
        return True
    return lower.endswith("dockerfile") or lower.endswith("makefile")


def scan_indexable_workspace_files() -> tuple[Dict[str, str], int]:
    """Return indexable text files and count of skipped non-indexable paths."""
    file_list: Dict[str, str] = {}
    skipped = 0
    if not os.path.exists(state.WORKSPACE_DIR):
        return file_list, skipped
    for root, _dirs, files_in_dir in os.walk(state.WORKSPACE_DIR):
        for file in files_in_dir:
            rel_path = os.path.relpath(os.path.join(root, file), state.WORKSPACE_DIR).replace("\\", "/")
            if _path_should_skip_index(rel_path):
                skipped += 1
                continue
            if not _is_indexable_file(rel_path):
                skipped += 1
                continue
            try:
                with open(os.path.join(root, file), "r", encoding="utf-8") as f:
                    file_list[rel_path] = f.read()
            except Exception:
                skipped += 1
    return file_list, skipped


def sync_virtual_filesystem_from_disk() -> Dict[str, str]:
    """Scans the physical workspace and syncs VIRTUAL_FILESYSTEM."""
    file_list: Dict[str, str] = {}
    if os.path.exists(state.WORKSPACE_DIR):
        for root, _dirs, files_in_dir in os.walk(state.WORKSPACE_DIR):
            for file in files_in_dir:
                rel_path = os.path.relpath(os.path.join(root, file), state.WORKSPACE_DIR)
                if not _path_should_skip_index(rel_path.replace("\\", "/")):
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
        try:
            from backend.storage.code_index import CodeIndexEngine

            CodeIndexEngine().upsert_file(safe_path, content)
        except Exception:
            pass
        nbytes = len(content.encode("utf-8"))
        add_system_log(
            state.ACTIVE_SPRINT_AGENT or "Developer",
            "success",
            f"Wrote '{safe_path}' ({nbytes} bytes) → {phys_path}",
        )
        invalidate_step_file_read(safe_path)
        state.STEP_PATCH_FAILURES.pop(safe_path, None)
        from backend.services.tool_cache import invalidate_fingerprint, register_touched_path

        register_touched_path(safe_path)
        invalidate_fingerprint()
        format_note = maybe_auto_format_after_edit(safe_path)
        msg = f"Successfully saved file physically at: '{phys_path}'"
        if format_note:
            msg += f"\n{format_note}"
        return msg
    except Exception as e:
        msg = f"Error: physical write failed for '{safe_path}': {e}"
        add_system_log(state.ACTIVE_SPRINT_AGENT or "Developer", "error", msg)
        return msg


def clear_step_file_reads() -> None:
    state.STEP_FILE_READS.clear()
    state.STEP_PATCH_FAILURES.clear()


def maybe_auto_format_after_edit(safe_path: str) -> Optional[str]:
    """Run dart format after Dart edits when autoFormatAfterEdit is enabled."""
    ws = get_workflow_settings()
    if ws.get("autoFormatAfterEdit") is False:
        return None
    if not safe_path.endswith(".dart"):
        return None
    if not os.path.exists(os.path.join(state.WORKSPACE_DIR, "pubspec.yaml")):
        return None
    from backend.services.command_result import run_workspace_command

    result = run_workspace_command(f'dart format "{safe_path}"', timeout=30)
    if result.success:
        return f"Auto-formatted with dart format: {safe_path}"
    return None


def record_step_file_read(path: str, content: str) -> None:
    try:
        safe_path = resolve_workspace_path(path)
    except ValueError:
        return
    if not content or content.startswith("Error:"):
        return
    state.STEP_FILE_READS[safe_path] = content


def invalidate_step_file_read(path: str) -> None:
    try:
        safe_path = resolve_workspace_path(path)
    except ValueError:
        return
    state.STEP_FILE_READS.pop(safe_path, None)


def _normalize_for_patch_match(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _patch_match_and_replace(current: str, old_text: str, new_text: str) -> tuple[Optional[str], int]:
    """Return (updated_content, match_count). match_count 0 = not found, >1 = ambiguous."""
    count = current.count(old_text)
    if count == 1:
        return current.replace(old_text, new_text, 1), 1
    if count > 1:
        return None, count

    norm_current = _normalize_for_patch_match(current)
    norm_old = _normalize_for_patch_match(old_text)
    norm_new = _normalize_for_patch_match(new_text)
    norm_count = norm_current.count(norm_old)
    if norm_count == 1:
        updated_norm = norm_current.replace(norm_old, norm_new, 1)
        if "\r\n" in current:
            updated = updated_norm.replace("\n", "\r\n")
        else:
            updated = updated_norm
        return updated, 1
    return None, norm_count


def _patch_not_found_message(path: str, current: str, old_text: str) -> str:
    lines = current.splitlines()
    excerpt = "\n".join(f"  {i + 1}| {line}" for i, line in enumerate(lines[:30]))
    first_line = next((ln.strip() for ln in old_text.splitlines() if ln.strip()), "")
    hint = ""
    if first_line and first_line not in current:
        preview = first_line[:80] + ("…" if len(first_line) > 80 else "")
        hint = f"\nFirst line of old_text not present in file: {preview!r}"
    return (
        f"Error: old_text not found in '{path}' (file has {len(lines)} lines).{hint}\n"
        f"First lines of current file:\n{excerpt}\n"
        f"You must read_file('{path}') in this step and copy old_text exactly from that output. "
        f"Do not use pre-loaded context or analyze output."
    )


def apply_workspace_patch(path: str, old_text: str, new_text: str) -> str:
    """Replace a unique old_text snippet in a file with new_text."""
    sync_virtual_filesystem_from_disk()
    try:
        safe_path = resolve_workspace_path(path)
    except ValueError as e:
        return f"Error: {e}"

    if state.ACTIVE_SPRINT_TASK_ID and safe_path not in state.STEP_FILE_READS:
        return (
            f"Error: apply_patch requires read_file on '{path}' in this step first. "
            f"Call read_file, then retry with old_text copied exactly from that output."
        )

    current = read_workspace_file(path)
    if current.startswith("Error:"):
        return f"Error: Cannot patch — {current}"

    updated, count = _patch_match_and_replace(current, old_text, new_text)
    if count == 0:
        state.STEP_PATCH_FAILURES[safe_path] = state.STEP_PATCH_FAILURES.get(safe_path, 0) + 1
        fails = state.STEP_PATCH_FAILURES[safe_path]
        base = _patch_not_found_message(path, current, old_text)
        if fails >= 2:
            return (
                f"{base}\n\nPatch failed {fails} times on '{path}'. "
                "Use read_file for the full file, then write_file with the complete corrected content "
                "instead of apply_patch."
            )
        return base
    if count > 1:
        return f"Error: old_text appears {count} times in '{path}' — must be unique"
    assert updated is not None
    return write_workspace_file(path, updated)


def read_workspace_file(
    path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> str:
    try:
        safe_path = resolve_workspace_path(path)
    except ValueError as e:
        return str(e)

    phys_path = os.path.join(state.WORKSPACE_DIR, safe_path)
    content: Optional[str] = None
    if os.path.exists(phys_path):
        try:
            with open(phys_path, "r", encoding="utf-8") as f:
                content = f.read()
                state.VIRTUAL_FILESYSTEM[safe_path] = content
        except Exception:
            pass
    if content is None:
        content = state.VIRTUAL_FILESYSTEM.get(safe_path)
    if content is None:
        return f"Error: File '{safe_path}' not found."

    if state.ACTIVE_SPRINT_TASK_ID:
        record_task_file(state.ACTIVE_SPRINT_TASK_ID, safe_path, "read", persist=True)

    lines = content.splitlines()
    total = len(lines)
    start = max(1, int(start_line)) if start_line else 1
    end = min(total, int(end_line)) if end_line else total
    if start_line or end_line:
        if start > total:
            return f"Error: start_line {start} exceeds file length ({total} lines)."
        if end < start:
            end = start
        sliced = lines[start - 1 : end]
        numbered = "\n".join(f"{i}|{line}" for i, line in enumerate(sliced, start=start))
        return f"File: {safe_path} (lines {start}-{end} of {total})\n{numbered}"
    return content


def list_workspace_dir(path: str = ".", limit: int = 200) -> str:
    """List directory entries relative to workspace root."""
    try:
        safe_path = resolve_workspace_path(path or ".")
    except ValueError as e:
        return str(e)
    root = os.path.join(state.WORKSPACE_DIR, safe_path)
    if not os.path.isdir(root):
        return f"Error: '{safe_path}' is not a directory."
    entries: List[str] = []
    try:
        for name in sorted(os.listdir(root)):
            if name.startswith(".") and name not in (".env.example",):
                continue
            full = os.path.join(root, name)
            rel = os.path.join(safe_path, name).replace("\\", "/")
            if safe_path in (".", ""):
                rel = name
            kind = "dir" if os.path.isdir(full) else "file"
            entries.append(f"{kind}\t{rel}")
            if len(entries) >= int(limit or 200):
                entries.append(f"... truncated at {limit} entries")
                break
    except OSError as exc:
        return f"Error listing '{safe_path}': {exc}"
    header = f"Directory: {safe_path or '.'} ({len(entries)} entries)"
    return header + "\n" + "\n".join(entries)


def _workspace_has_dotnet_project(ws: str) -> bool:
    """True when workspace root contains a .sln or .csproj file."""
    if not os.path.isdir(ws):
        return False
    try:
        for name in os.listdir(ws):
            if name.endswith(".sln") or name.endswith(".csproj"):
                return True
    except OSError:
        return False
    return False


def _dotnet_test_commands(ws: str, safe_path: str) -> list[list[str]]:
    """Build dotnet test command candidates for the workspace or explicit project path."""
    commands: list[list[str]] = []
    if safe_path.endswith((".csproj", ".sln")):
        phys = os.path.normpath(os.path.join(ws, safe_path.replace("/", os.sep)))
        if os.path.isfile(phys):
            return [["dotnet", "test", phys]]

    if not os.path.isdir(ws):
        return commands

    try:
        names = sorted(os.listdir(ws))
    except OSError:
        return commands

    for name in names:
        if name.endswith(".sln"):
            return [["dotnet", "test", os.path.join(ws, name)]]

    csprojs = [n for n in names if n.endswith(".csproj")]
    if csprojs:
        commands.append(["dotnet", "test", os.path.join(ws, csprojs[0])])
    return commands


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
    elif safe_path.endswith((".csproj", ".sln")) or _workspace_has_dotnet_project(state.WORKSPACE_DIR):
        commands.extend(_dotnet_test_commands(state.WORKSPACE_DIR, safe_path))

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


def run_agent_command(command: str, background: bool = False) -> str:
    """Runs a shell command for sprint agents; returns structured text output."""
    if background:
        from backend.services.background_terminal import start_background_command

        ok, msg, session_id = start_background_command(command)
        if not ok:
            return f"Error: {msg}"
        return (
            f"Background terminal session {session_id} started.\n"
            f"Command: {command}\n"
            "Output streams to Tools panel; use read_background_output or wait for completion."
        )

    from backend.services.command_result import format_command_result_for_agent, run_workspace_command

    result = run_workspace_command(command)
    return format_command_result_for_agent(result)


def derive_project_lint_command() -> Optional[str]:
    """Return one recommended lint/analyze command for the current workspace, if detectable."""
    sync_virtual_filesystem_from_disk()
    ws = state.WORKSPACE_DIR

    if os.path.isfile(os.path.join(ws, "pubspec.yaml")):
        return "flutter analyze"

    if _workspace_has_dotnet_project(ws):
        return "dotnet build"

    package_json = os.path.join(ws, "package.json")
    if os.path.isfile(package_json):
        try:
            with open(package_json, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            scripts = data.get("scripts") or {}
            if "lint" in scripts:
                return "npm run lint"
        except (OSError, json.JSONDecodeError):
            pass
        return None

    if any(path.endswith(".py") for path in state.VIRTUAL_FILESYSTEM):
        import shutil

        if shutil.which("ruff"):
            return "ruff check ."

    return None


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

    task_file_count = sum(
        1 for f in (task.get("files") or []) if f
    )
    if task_file_count == 0:
        tests_dir = os.path.join(ws, "tests")
        if os.path.isdir(tests_dir):
            count = 0
            for root, _dirs, files_in_dir in os.walk(tests_dir):
                for fn in sorted(files_in_dir):
                    if count >= 2:
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
                    if count >= 3:
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


def grep_workspace(
    pattern: str,
    path: Optional[str] = None,
    glob: Optional[str] = None,
    case_insensitive: bool = False,
    context_lines: int = 0,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Search workspace with ripgrep when available, else regex scan over virtual FS."""
    if not pattern or not str(pattern).strip():
        return []
    sync_virtual_filesystem_from_disk()
    limit = max(1, min(int(limit or 100), 500))
    context_lines = max(0, min(int(context_lines or 0), 5))
    ws = state.WORKSPACE_DIR
    rg = shutil.which("rg")

    if rg and os.path.isdir(ws):
        cmd = [rg, "-n", "--no-heading", f"--max-count={limit}"]
        if case_insensitive:
            cmd.append("-i")
        if context_lines:
            cmd.extend(["-C", str(context_lines)])
        if glob:
            cmd.extend(["--glob", glob])
        search_path = ws
        if path:
            try:
                search_path = os.path.join(ws, resolve_workspace_path(path))
            except ValueError:
                search_path = ws
        cmd.extend([pattern, search_path])
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=ws,
            )
            if proc.returncode in (0, 1):
                parsed = _parse_rg_output(proc.stdout, ws, limit)
                if parsed:
                    return parsed
        except (subprocess.TimeoutExpired, OSError):
            pass

    return _grep_fallback(pattern, path, glob, case_insensitive, context_lines, limit)


def _parse_rg_output(stdout: str, workspace_root: str, limit: int) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    ws_norm = os.path.normpath(workspace_root)
    for line in stdout.splitlines():
        if len(results) >= limit:
            break
        if line.startswith("--") and len(results) > 0:
            continue
        if ":" not in line:
            continue
        file_part, rest = line.split(":", 1)
        if not rest:
            continue
        rel = os.path.relpath(file_part, ws_norm).replace("\\", "/")
        if ":" in rest:
            line_num_str, content = rest.split(":", 1)
            try:
                line_num = int(line_num_str)
            except ValueError:
                line_num = 0
                content = rest
        else:
            line_num = 0
            content = rest
        results.append({"path": rel, "line": line_num, "content": content.strip()})
    return results


def _grep_fallback(
    pattern: str,
    path: Optional[str],
    glob_pattern: Optional[str],
    case_insensitive: bool,
    context_lines: int,
    limit: int,
) -> List[Dict[str, Any]]:
    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error:
        regex = re.compile(re.escape(pattern), flags)

    results: List[Dict[str, Any]] = []
    prefix = ""
    if path:
        try:
            prefix = resolve_workspace_path(path).rstrip("/")
        except ValueError:
            prefix = ""

    for file_path, content in state.VIRTUAL_FILESYSTEM.items():
        if len(results) >= limit:
            break
        if prefix and not (file_path == prefix or file_path.startswith(prefix + "/")):
            continue
        if glob_pattern and not fnmatch.fnmatch(file_path, glob_pattern.replace("**/", "*")):
            if not fnmatch.fnmatch(os.path.basename(file_path), glob_pattern.lstrip("**/")):
                continue
        lines = content.splitlines()
        for i, line in enumerate(lines, start=1):
            if len(results) >= limit:
                break
            if regex.search(line):
                entry: Dict[str, Any] = {"path": file_path, "line": i, "content": line.strip()}
                if context_lines:
                    start = max(0, i - 1 - context_lines)
                    end = min(len(lines), i + context_lines)
                    entry["contextBefore"] = lines[start : i - 1]
                    entry["contextAfter"] = lines[i:end]
                results.append(entry)
    return results


def glob_workspace(pattern: str, limit: int = 200) -> List[str]:
    """Find workspace files matching a glob pattern (e.g. **/*.dart)."""
    if not pattern or not str(pattern).strip():
        return []
    sync_virtual_filesystem_from_disk()
    limit = max(1, min(int(limit or 200), 1000))
    norm = pattern.replace("\\", "/").lstrip("./")
    matches: List[str] = []
    for file_path in sorted(state.VIRTUAL_FILESYSTEM.keys()):
        if fnmatch.fnmatch(file_path, norm) or fnmatch.fnmatch(file_path, norm.lstrip("**/")):
            matches.append(file_path)
            if len(matches) >= limit:
                break
    return matches


def delete_workspace_file(path: str) -> str:
    """Delete a file from the workspace (requires approval when gated)."""
    try:
        safe_path = resolve_workspace_path(path)
    except ValueError as e:
        return f"Error: {e}"

    phys_path = os.path.join(state.WORKSPACE_DIR, safe_path)
    if safe_path in state.VIRTUAL_FILESYSTEM:
        del state.VIRTUAL_FILESYSTEM[safe_path]
    if os.path.isfile(phys_path):
        try:
            os.remove(phys_path)
        except OSError as e:
            return f"Error: could not delete '{safe_path}': {e}"

    if state.ACTIVE_SPRINT_TASK_ID:
        record_task_file(state.ACTIVE_SPRINT_TASK_ID, safe_path, "deleted", persist=False)
        record_task_decision(
            state.ACTIVE_SPRINT_TASK_ID,
            state.ACTIVE_SPRINT_AGENT or "Developer",
            "file",
            f"Deleted file '{safe_path}'",
        )
    save_current_project_state()
    return f"Deleted '{safe_path}'."


def expand_chat_mentions(message: str, max_file_chars: int = 4000, max_folder_files: int = 5) -> str:
    """Expand @path and @folder/ tokens with inline file context for chat."""
    import re

    if "@" not in message:
        return message

    sync_virtual_filesystem_from_disk()
    pattern = re.compile(r"@([\w./\-]+/?)")
    expanded_parts: List[str] = [message]

    for match in pattern.finditer(message):
        token = match.group(1).rstrip("/")
        if not token:
            continue
        if token.endswith("/") or match.group(1).endswith("/"):
            folder = token.rstrip("/")
            paths = glob_workspace(f"{folder}/**/*", limit=max_folder_files + 20)
            paths = [p for p in paths if not p.endswith("/")][:max_folder_files]
            block = [f"=== @folder {folder}/ ({len(paths)} file(s)) ==="]
            for p in paths:
                content = read_workspace_file(p)
                if content.startswith("Error:"):
                    continue
                block.append(f"--- {p} ---\n{content[:max_file_chars // max_folder_files]}")
            expanded_parts.append("\n".join(block))
        else:
            content = read_workspace_file(token)
            if not content.startswith("Error:"):
                expanded_parts.append(
                    f"=== @file {token} ===\n{content[:max_file_chars]}"
                )

    return "\n\n".join(expanded_parts)


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
