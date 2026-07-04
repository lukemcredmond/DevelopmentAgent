import os
import time
from typing import Any, Dict, Generator, List, Mapping, Optional, Sequence, Union

from ollama import Client
from ollama._types import Message

from backend import state
from backend.agents.task_context import record_task_decision, record_task_transcript
from backend.agents.tools import ToolRegistry
from backend.storage.memory_engine import SemanticMemoryEngine

ChatMessage = Union[Mapping[str, Any], Message]


class ScrumAgent:
    def __init__(
        self,
        role: str,
        model: str,
        system_prompt: str,
        ollama_url: str = "http://localhost:11434",
    ):
        self.role = role
        self.model = model
        self.system_prompt = system_prompt
        self.ollama_url = ollama_url.rstrip("/")
        self.memory = SemanticMemoryEngine(ollama_url=self.ollama_url)
        self.registry = ToolRegistry()
        self.assigned_skills: List[str] = []
        self._client: Optional[Client] = None
        self._client_host: Optional[str] = None

    def register_tool(self, tool) -> None:
        self.registry.register(tool)

    def _get_client(self) -> Client:
        if self._client is None or self._client_host != self.ollama_url:
            self._client = Client(host=self.ollama_url, timeout=120.0)
            self._client_host = self.ollama_url
        return self._client

    def _get_skills_context(self) -> str:
        if not self.assigned_skills:
            return ""

        skills_context = "\n=== SPECIALIZED AGENT SKILLS ===\n"
        for skill_file in self.assigned_skills:
            skill_path = os.path.join(state.SKILLS_DIR, skill_file)
            if os.path.exists(skill_path):
                try:
                    with open(skill_path, "r", encoding="utf-8") as f:
                        skills_context += f"\n[Skill: {skill_file}]\n{f.read()}\n"
                except Exception:
                    pass
        return skills_context

    def _build_system_content(self) -> str:
        return self.system_prompt + self._get_skills_context()

    def _build_user_content(self, user_prompt: str) -> str:
        related_memories = self.memory.search(self.role, user_prompt, limit=2)
        memory_context = ""
        if related_memories:
            memory_context = "\n=== RELEVANT HISTORICAL MEMORIES ===\n" + "\n".join(
                [f"[{m['category']}] {m['content']}" for m in related_memories]
            )
        parts = [part for part in (memory_context, f"Task Detail:\n{user_prompt}") if part]
        return "\n\n".join(parts)

    def _chat_options(self) -> Dict[str, Any]:
        return {"temperature": 0.1}

    def _chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        stream: bool = False,
        tools: Optional[Sequence[Dict[str, Any]]] = None,
    ):
        client = self._get_client()
        delays = [0, 1, 2, 4]

        for delay in delays:
            if delay:
                time.sleep(delay)
            try:
                return client.chat(
                    model=self.model,
                    messages=list(messages),
                    tools=tools,
                    stream=stream,
                    options=self._chat_options(),
                )
            except Exception:
                continue

        return None

    def _record_tool_usage(
        self,
        task_id: str,
        tool_name: str,
        tool_output: str,
        user_prompt: str,
    ) -> None:
        record_task_transcript(
            task_id,
            "tool",
            f"{tool_name}: {tool_output[:500]}",
            agent=self.role,
        )
        record_task_decision(
            task_id,
            self.role,
            "tool",
            f"Used tool '{tool_name}'",
            tool_output[:500],
        )
        self.memory.save(
            self.role,
            f"Invoked tool '{tool_name}' on task: {user_prompt}",
            "tool_usage",
        )

    def _process_tool_calls(
        self,
        message: Message,
        messages: List[ChatMessage],
        user_prompt: str,
    ) -> None:
        if message.content and state.ACTIVE_SPRINT_TASK_ID:
            record_task_transcript(
                state.ACTIVE_SPRINT_TASK_ID,
                "assistant",
                message.content,
                agent=self.role,
            )

        messages.append(message)

        for call in message.tool_calls or []:
            tool_name = call.function.name
            arguments = dict(call.function.arguments)
            tool_output = self.registry.invoke(tool_name, arguments)

            if state.ACTIVE_SPRINT_TASK_ID:
                self._record_tool_usage(
                    state.ACTIVE_SPRINT_TASK_ID,
                    tool_name,
                    tool_output,
                    user_prompt,
                )

            messages.append(
                {
                    "role": "tool",
                    "tool_name": tool_name,
                    "content": tool_output,
                }
            )

    def execute_step(self, user_prompt: str, max_iterations: int = 8) -> str:
        tools = self.registry.get_ollama_tools()
        messages: List[ChatMessage] = [
            {"role": "system", "content": self._build_system_content()},
            {"role": "user", "content": self._build_user_content(user_prompt)},
        ]

        for _ in range(max_iterations):
            response = self._chat(messages, tools=tools or None)
            if response is None:
                return "SIMULATION_FALLBACK"

            message = response.message
            if message.tool_calls:
                self._process_tool_calls(message, messages, user_prompt)
                continue

            content = (message.content or "").strip()
            if state.ACTIVE_SPRINT_TASK_ID and content:
                record_task_transcript(
                    state.ACTIVE_SPRINT_TASK_ID,
                    "assistant",
                    content,
                    agent=self.role,
                )
            return content or "Task completed."

        return "Max tool iterations reached without completing the task."

    def stream_messages(
        self,
        messages: List[Dict[str, str]],
    ) -> Generator[str, None, None]:
        """Streams Ollama chat response chunks; yields fallback token on failure."""
        full_messages: List[ChatMessage] = [
            {"role": "system", "content": self._build_system_content()},
            *messages,
        ]

        stream = self._chat(full_messages, stream=True)
        if stream is None:
            yield "SIMULATION_FALLBACK"
            return

        try:
            for chunk in stream:
                content = chunk.message.content
                if content:
                    yield content
        except Exception:
            yield "SIMULATION_FALLBACK"
