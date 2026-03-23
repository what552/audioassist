"""
MeetingAgent — Interactive Summary Agent for AudioAssist.

Phase 1: single-meeting agent with multi-turn conversation, tool calling,
and session persistence. Uses a custom tool-calling loop (ReAct style) over
any OpenAI-compatible API provider.

Tools (Phase 1):
  - get_transcript        Read and format the meeting transcript
  - get_current_summary   Read the latest summary version
  - get_summary_versions  List available summary versions
  - update_summary        Save a new summary version

Provider compatibility:
  If the provider raises an error for tool_choice / function calling,
  falls back to a no-tool one-shot mode where transcript + summary are
  injected directly into the system message.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI as _OpenAI
except ImportError:
    _OpenAI = None  # type: ignore[assignment,misc]

_SYSTEM_PROMPT = """\
你是 AudioAssist 的会议助手，帮助用户编辑和理解会议纪要。

规则：
- 使用工具获取最新的转写文本和纪要，不要凭空捏造内容
- 若用户要求修改纪要，生成新版本后调用 update_summary 保存
- 回答问题时尽量引用原文时间戳或发言者
- 如果工具调用失败，诚实告知用户，不要编造数据
"""

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_transcript",
            "description": (
                "Read the meeting transcript. Returns speaker-formatted text "
                "with timestamps, truncated to max_chars."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The job ID of the meeting"},
                    "max_chars": {
                        "type": "integer",
                        "description": "Maximum characters to return (default 6000)",
                    },
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_current_summary",
            "description": (
                "Get the most recent summary version text. "
                "Returns empty string if no summary exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The job ID of the meeting"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_summary_versions",
            "description": "List all saved summary versions for the meeting, with previews.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The job ID of the meeting"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_summary",
            "description": (
                "Save a new summary version. Call this after generating an edited summary."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "The job ID of the meeting"},
                    "new_text": {"type": "string", "description": "The full new summary text"},
                    "reason": {"type": "string", "description": "Why this version was created"},
                },
                "required": ["job_id", "new_text", "reason"],
            },
        },
    },
]


class MeetingAgent:
    """Interactive meeting assistant with tool-calling loop."""

    MAX_ITERATIONS = 5

    def __init__(
        self,
        output_dir: str,
        base_url: str,
        api_key: str,
        model: str,
        push_event: Callable[[str, Any], None],
    ):
        self._output_dir = output_dir
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._push = push_event  # push_event(event_name, payload_dict)

    # ── Public entry point ──────────────────────────────────────────────────

    def run(
        self,
        job_id: str,
        user_input: str,
        history_messages: list[dict],
    ) -> str:
        """
        Execute one agent turn.

        Args:
            job_id:           Meeting job ID.
            user_input:       The user's message for this turn.
            history_messages: Prior conversation messages (role/content pairs).

        Returns:
            Full assistant response text for this turn.
        """
        if _OpenAI is None:
            raise ImportError("openai package required for agent. Run: pip install openai")

        client = _OpenAI(base_url=self._base_url, api_key=self._api_key)

        system_content = (
            _SYSTEM_PROMPT
            + f"\n\n当前会议 job_id：{job_id}\n直接用这个 job_id 调用工具，不要询问用户。"
        )
        messages = [
            {"role": "system", "content": system_content},
            *history_messages,
            {"role": "user", "content": user_input},
        ]

        try:
            full_text = self._tool_loop(client, job_id, messages)
        except Exception as e:
            logger.warning("Tool loop failed (%s), falling back to no-tool mode", e)
            full_text = self._fallback_oneshot(client, job_id, user_input, history_messages)

        return full_text

    # ── Tool-calling loop ───────────────────────────────────────────────────

    def _tool_loop(self, client, job_id: str, messages: list[dict]) -> str:
        """Run the tool-calling loop. Returns final text response."""
        for _iteration in range(self.MAX_ITERATIONS):
            response = client.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=_TOOLS,
                tool_choice="auto",
                stream=False,
            )
            choice = response.choices[0]
            finish_reason = choice.finish_reason

            # No more tool calls — deliver final answer
            if finish_reason == "stop" or not choice.message.tool_calls:
                final_content = choice.message.content or ""
                if final_content:
                    self._push("onAgentChunk", {"job_id": job_id, "chunk": final_content})
                return final_content

            # Process tool calls
            tool_calls = choice.message.tool_calls
            messages.append({
                "role": "assistant",
                "content": choice.message.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            })

            for tc in tool_calls:
                tool_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}

                self._push("onAgentToolStart", {"job_id": job_id, "tool": tool_name})
                result = self._execute_tool(tool_name, args, job_id)
                self._push("onAgentToolEnd", {"job_id": job_id, "tool": tool_name})

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

        # Reached max iterations — ask for final answer without tools
        logger.warning("Agent reached max iterations (%d) for job %s", self.MAX_ITERATIONS, job_id)
        final_resp = client.chat.completions.create(
            model=self._model,
            messages=messages,
            stream=False,
        )
        content = final_resp.choices[0].message.content or ""
        if content:
            self._push("onAgentChunk", {"job_id": job_id, "chunk": content})
        return content

    # ── Tool execution ──────────────────────────────────────────────────────

    def _execute_tool(self, name: str, args: dict, default_job_id: str) -> Any:
        # Always use the session's job_id — never trust the model-supplied value
        # to prevent prompt-injection attacks that could read/write other sessions.
        job_id = default_job_id
        if name == "get_transcript":
            return self._tool_get_transcript(job_id, args.get("max_chars", 6000))
        elif name == "get_current_summary":
            return self._tool_get_current_summary(job_id)
        elif name == "get_summary_versions":
            return self._tool_get_summary_versions(job_id)
        elif name == "update_summary":
            return self._tool_update_summary(
                job_id,
                args.get("new_text", ""),
                args.get("reason", ""),
            )
        else:
            return {"error": f"Unknown tool: {name}"}

    def _tool_get_transcript(self, job_id: str, max_chars: int = 6000) -> dict:
        path = os.path.join(self._output_dir, f"{job_id}.json")
        if not os.path.exists(path):
            return {"error": "Transcript not found"}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            segments = data.get("segments", [])
            lines = []
            for seg in segments:
                speaker = seg.get("speaker", "?")
                start = seg.get("start", 0)
                text = seg.get("text", "").strip()
                mins = int(start) // 60
                secs = int(start) % 60
                lines.append(f"[{mins:02d}:{secs:02d}] {speaker}: {text}")
            formatted = "\n".join(lines)
            if len(formatted) > max_chars:
                formatted = formatted[:max_chars] + "\n... [truncated]"
            return {"transcript": formatted, "segment_count": len(segments)}
        except Exception as e:
            return {"error": str(e)}

    def _tool_get_current_summary(self, job_id: str) -> dict:
        path = os.path.join(self._output_dir, f"{job_id}_summary.json")
        if not os.path.exists(path):
            return {"summary": "", "version_count": 0}
        try:
            with open(path, encoding="utf-8") as f:
                versions = json.load(f)
            if not versions:
                return {"summary": "", "version_count": 0}
            return {"summary": versions[-1].get("text", ""), "version_count": len(versions)}
        except Exception as e:
            return {"error": str(e)}

    def _tool_get_summary_versions(self, job_id: str) -> dict:
        path = os.path.join(self._output_dir, f"{job_id}_summary.json")
        if not os.path.exists(path):
            return {"versions": []}
        try:
            with open(path, encoding="utf-8") as f:
                versions = json.load(f)
            return {
                "versions": [
                    {
                        "index": i + 1,
                        "created_at": v.get("created_at", ""),
                        "preview": v.get("text", "")[:200],
                    }
                    for i, v in enumerate(versions)
                ]
            }
        except Exception as e:
            return {"error": str(e)}

    def _tool_update_summary(self, job_id: str, new_text: str, reason: str) -> dict:
        if not new_text.strip():
            return {"error": "new_text is empty"}
        try:
            import time as _time

            os.makedirs(self._output_dir, exist_ok=True)
            path = os.path.join(self._output_dir, f"{job_id}_summary.json")
            versions: list[dict] = []
            if os.path.exists(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        versions = json.load(f)
                except Exception:
                    versions = []
            versions.append({"text": new_text, "created_at": _time.strftime("%Y-%m-%d %H:%M")})
            versions = versions[-3:]  # keep max 3
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(versions, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
            self._push("onAgentDraftUpdated", {"job_id": job_id, "text": new_text})
            return {"saved": True, "reason": reason}
        except Exception as e:
            return {"error": str(e)}

    # ── Fallback: no-tool one-shot ──────────────────────────────────────────

    def _fallback_oneshot(
        self,
        client,
        job_id: str,
        user_input: str,
        history_messages: list[dict],
    ) -> str:
        """Fallback when provider doesn't support tool_choice."""
        transcript_result = self._tool_get_transcript(job_id)
        summary_result = self._tool_get_current_summary(job_id)

        transcript_text = transcript_result.get("transcript", "(not available)")
        summary_text = summary_result.get("summary", "(none)")

        system_msg = (
            _SYSTEM_PROMPT
            + f"\n\n--- TRANSCRIPT ---\n{transcript_text}"
            + f"\n\n--- CURRENT SUMMARY ---\n{summary_text}"
        )
        messages = [
            {"role": "system", "content": system_msg},
            *history_messages,
            {"role": "user", "content": user_input},
        ]

        full = ""
        try:
            stream_resp = client.chat.completions.create(
                model=self._model,
                messages=messages,
                stream=True,
            )
            for chunk in stream_resp:
                delta = chunk.choices[0].delta.content
                if delta:
                    full += delta
                    self._push("onAgentChunk", {"job_id": job_id, "chunk": delta})
        except Exception:
            logger.exception("Fallback one-shot also failed for job %s", job_id)
        return full
