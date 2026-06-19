"""Model adapter interface.

The eval runner needs to call a model with a chat-style messages list
and a list of tool specs, and get back either text or a sequence of
tool calls. The runner is responsible for serializing tool specs and
parsing tool calls; the adapter just needs to translate the call.

For v1 we ship four adapters:

- ``MockModel``: scripted responses. Used by the dry-run/self-test
  path and by the acceptance test for the runner.

- ``EchoModel``: returns the user prompt back. Useful as a smoke
  check that the wiring works end-to-end.

- ``OpenAIChatAdapter``: OpenAI Chat Completions.

- ``MiniMaxAdapter``: MiniMax (Responses API).

The interface for a "real" adapter is documented in ``BaseAdapter``.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Protocol


# ---------------------------------------------------------------------
# Tool call and chat response types
# ---------------------------------------------------------------------


@dataclass
class ToolCall:
    name: str
    args: dict
    id: str = ""  # caller may assign

    def to_openai(self) -> dict:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.args),
            },
        }


@dataclass
class ModelResponse:
    text: str = ""
    tool_calls: list[ToolCall] = None  # type: ignore[assignment]
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str = ""
    # Provider response/session id. For the Responses API (minimax) this can
    # be passed back as previous_response_id to continue server-side; logged
    # per turn so a replay can branch from it.
    response_id: str = ""

    def __post_init__(self) -> None:
        if self.tool_calls is None:
            self.tool_calls = []


class AdapterError(Exception):
    pass


# ---------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------


class BaseAdapter(Protocol):
    name: str

    def chat(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        temperature: Optional[float],
        max_output_tokens: Optional[int],
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
    ) -> ModelResponse: ...


# ---------------------------------------------------------------------
# Mock adapter: replays a script.
# ---------------------------------------------------------------------


@dataclass
class ScriptedResponse:
    text: str = ""
    tool_calls: list[ToolCall] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.tool_calls is None:
            self.tool_calls = []


class MockModel:
    """Replay a fixed list of responses in order.

    Each response may include text and tool calls. When the script is
    exhausted, a default final ``text`` is emitted (configurable).
    """

    name = "mock"

    def __init__(
        self,
        responses: list[ScriptedResponse],
        final_text: str = "Task complete.",
        input_tokens: int = 100,
        output_tokens: int = 50,
    ) -> None:
        self._responses = list(responses)
        self._final = final_text
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._idx = 0

    def chat(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        temperature: Optional[float],
        max_output_tokens: Optional[int],
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
    ) -> ModelResponse:
        if self._idx < len(self._responses):
            r = self._responses[self._idx]
            self._idx += 1
            tcs = list(r.tool_calls)
        else:
            tcs = []
            r = ScriptedResponse(text=self._final)
        return ModelResponse(
            text=r.text,
            tool_calls=tcs,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            total_tokens=self._input_tokens + self._output_tokens,
            finish_reason="stop" if not tcs else "tool_calls",
        )


class EchoModel:
    """Returns the last user message back as text. For smoke tests."""

    name = "echo"

    def chat(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        temperature: Optional[float],
        max_output_tokens: Optional[int],
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
    ) -> ModelResponse:
        last_user = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                last_user = m.get("content", "")
                break
        return ModelResponse(
            text=f"echo: {last_user[:200]}",
            tool_calls=[],
            input_tokens=20,
            output_tokens=10,
            total_tokens=30,
            finish_reason="stop",
        )


# ---------------------------------------------------------------------
# Chat-completions adapter (OpenAI-compatible)
# ---------------------------------------------------------------------


class OpenAIChatAdapter:
    """Adapter for the OpenAI Chat Completions API.

    Token accounting: uses the provider's reported
    ``prompt_tokens`` / ``completion_tokens``. Most Chat Completions
    providers do not break out cached/reasoning tokens in the same way
    as the Responses API, so those sub-fields stay zero.

    For providers that need extra request-body fields (e.g. some
    ``reasoning_budget`` and ``chat_template_kwargs``), pass them via
    ``extra_body``. The adapter forwards them as-is to the SDK.
    """

    name = "openai_chat"

    def __init__(
        self,
        model: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        env_key: str = "OPENAI_API_KEY",
        extra_body: Optional[dict] = None,
        reasoning_field: Optional[str] = None,
        env_base_url: Optional[str] = None,
        default_temperature: Optional[float] = None,
        default_top_p: Optional[float] = None,
        default_max_tokens: Optional[int] = None,
    ) -> None:
        try:
            import openai  # type: ignore
        except Exception as e:
            raise AdapterError(
                "openai package not installed; run pip install openai"
            ) from e
        self._openai = openai
        self._model = model
        if api_key is None:
            api_key = os.environ.get(env_key)
        if not api_key:
            raise AdapterError(f"{env_key} is not set")
        if base_url is None and env_base_url is not None:
            base_url = os.environ.get(env_base_url)
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        # 10 minute per-request timeout - long enough for legitimate
        # responses, short enough to keep the eval moving when the
        # model gets stuck on a long text response.
        kwargs["timeout"] = 600.0
        self._client = openai.OpenAI(**kwargs)
        self._extra_body = dict(extra_body or {})
        # Some providers put reasoning tokens on the response message
        # rather than in usage. ``reasoning_field`` is the attribute
        # name on the message to read (e.g. "reasoning_content").
        self._reasoning_field = reasoning_field
        # Per-model defaults applied when the runner passes None.
        # Set by the per-model defaults (each model has its own preferred
        # temperature / top_p / max_tokens from the provider docs).
        self._default_temperature = default_temperature
        self._default_top_p = default_top_p
        self._default_max_tokens = default_max_tokens
        # Some providers expect custom fields at the top level of the
        # JSON body (e.g. some providers use ``reasoning_effort``,
        # Google diffusiongemma uses ``chat_template_kwargs``).
        # Convention: any extra_body key wrapped in ``__`` is popped
        # at registry time and forwarded as a top-level kwarg. Keys
        # without the ``__`` wrapping go through the normal extra_body
        # channel (which the OpenAI SDK nests under "extra_body").
        self._top_level: dict[str, Any] = {}
        for k in list(self._extra_body.keys()):
            if k.startswith("__") and k.endswith("__"):
                self._top_level[k.strip("_")] = self._extra_body.pop(k)

    def chat(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        temperature: Optional[float],
        max_output_tokens: int,
        top_p: Optional[float] = None,
    ) -> ModelResponse:
        # Convert each tool's flat {name, description, input_schema}
        # into Chat Completions' nested {type, function: {name,
        # description, parameters}}. The internal tool object is
        # not Chat-shaped; passing it through unchanged yields a
        # 400 from the OpenAI Chat Completions API.
        oa_tools = [
            {
                "type": "function",
                "function": {
                    "name": t.get("name", ""),
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema") or t.get("parameters") or {"type": "object", "properties": {}},
                },
            }
            for t in tools
        ]
        # Resolve effective values: caller value wins, else adapter default.
        eff_temperature = temperature if temperature is not None else self._default_temperature
        eff_max_tokens = max_output_tokens if max_output_tokens is not None else self._default_max_tokens
        kwargs: dict = dict(
            model=self._model,
            messages=messages,
            tools=oa_tools or None,
        )
        if eff_max_tokens is not None:
            kwargs["max_tokens"] = eff_max_tokens
        # None means "let the provider decide": useful for models that
        # require a specific temperature (Nemotron, Kimi, Mistral, etc).
        if eff_temperature is not None:
            kwargs["temperature"] = eff_temperature
        # Per-call top_p wins over the adapter's construction-time default.
        eff_top_p = top_p if top_p is not None else self._default_top_p
        if eff_top_p is not None:
            kwargs["top_p"] = eff_top_p
        for k, v in self._top_level.items():
            kwargs[k] = v
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body
        # Retry on rate-limit (429) and transient server/connection errors
        # (5xx incl. non-standard codes like 529 "overloaded", timeouts,
        # connection resets) with a 5-second pause, up to 3 attempts (2
        # retries). The openai SDK maps any >=500 status to
        # InternalServerError regardless of the literal code,  so this
        # also covers 529. Other (non-transient, e.g. 4xx) errors
        # propagate immediately.
        resp = None
        last_err: Exception | None = None
        retryable = (
            self._openai.RateLimitError,
            self._openai.InternalServerError,
            self._openai.APIConnectionError,
        )
        for attempt in range(3):
            try:
                resp = self._client.chat.completions.create(**kwargs)
                break
            except retryable as e:
                last_err = e
                if attempt < 2:
                    print(f"[openai] {type(e).__name__}; sleeping 5s (attempt {attempt+1}/3)", flush=True)
                    time.sleep(5.0)
                continue
        if resp is None:
            raise AdapterError(f"openai request failed after 3 attempts: {last_err}") from last_err
        msg = resp.choices[0].message
        tcs: list[ToolCall] = []
        for tc in (msg.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            tcs.append(ToolCall(name=tc.function.name, args=args, id=tc.id))
        text = msg.content or ""
        # Optional reasoning field on the message itself (e.g. some
        # returns ``reasoning_content`` next to ``content``).
        reasoning_text = ""
        if self._reasoning_field:
            reasoning_text = getattr(msg, self._reasoning_field, "") or ""
        usage = resp.usage
        in_tok = getattr(usage, "prompt_tokens", 0) or 0
        out_tok = getattr(usage, "completion_tokens", 0) or 0
        total = in_tok + out_tok
        # Chat Completions doesn't always expose cached/reasoning sub-fields.
        cached = 0
        reasoning = 0
        if hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details:
            cached = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0
        if hasattr(usage, "completion_tokens_details") and usage.completion_tokens_details:
            reasoning = getattr(usage.completion_tokens_details, "reasoning_tokens", 0) or 0
        return ModelResponse(
            text=text,
            tool_calls=tcs,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_input_tokens=cached,
            reasoning_output_tokens=reasoning,
            total_tokens=total,
            finish_reason=resp.choices[0].finish_reason or "",
            response_id=getattr(resp, "id", "") or "",
        )


# Backwards-compat alias for code that imports the original class name.
OpenAIAdapter = OpenAIChatAdapter


# ---------------------------------------------------------------------
# MiniMax Responses adapter
# ---------------------------------------------------------------------


class MiniMaxAdapter:
    """Adapter for MiniMax's OpenAI-compatible Responses API.

    The wire API is ``responses``; the same SDK call shape as OpenAI's
    Responses endpoint is used. We translate the runner's chat-style
    messages into the Responses ``input`` list, and the response back
    into a uniform ``ModelResponse`` with separate input / cached-input
    / output / reasoning-output token counts.
    """

    name = "minimax"

    def __init__(
        self,
        model: str = "MiniMax-M3",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> None:
        try:
            import openai  # type: ignore
        except Exception as e:
            raise AdapterError(
                "openai package not installed; run pip install openai"
            ) from e
        self._openai = openai
        self._model = model
        if api_key is None:
            api_key = os.environ.get("MINIMAX_API_KEY")
        if not api_key:
            raise AdapterError("MINIMAX_API_KEY is not set")
        if base_url is None:
            base_url = os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
        self._base_url = base_url
        self._reasoning_effort = reasoning_effort
        # 10 minute per-request timeout - long enough for legitimate
        # responses, short enough to keep the eval moving when the
        # model gets stuck on a long text response.
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url, timeout=600.0)

    def chat(
        self,
        *,
        messages: list[dict],
        tools: list[dict],
        temperature: Optional[float],
        max_output_tokens: Optional[int],
        top_p: Optional[float] = None,
        top_k: Optional[int] = None,
    ) -> ModelResponse:
        # Translate chat-style messages into Responses input.
        input_items = _messages_to_responses_input(messages)
        oa_tools = [
            {
                "type": "function",
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or t.get("parameters") or {"type": "object", "properties": {}},
            }
            for t in tools
        ]
        kwargs: dict = dict(
            model=self._model,
            input=input_items,
            tools=oa_tools or None,
            max_output_tokens=max_output_tokens,
        )
        if self._reasoning_effort:
            kwargs["reasoning"] = {"effort": self._reasoning_effort}
        if top_p is not None:
            kwargs["top_p"] = top_p
        # The MiniMax Responses SDK does not accept top_k as a direct kwarg
        # (raises: unexpected keyword argument 'top_k'). It does accept it
        # via extra_body, and the server applies it (verified empirically).
        if top_k is not None:
            kwargs.setdefault("extra_body", {})["top_k"] = top_k
        # Temperature is accepted but the server may clamp it.
        # Retry on rate-limit (429) and transient server/connection errors
        # (5xx incl. non-standard codes like 529 "overloaded", timeouts,
        # connection resets) with a 5-second pause, up to 3 attempts (2
        # retries). minimax has been observed returning 529 mid-run; the
        # openai SDK maps any >=500 status to InternalServerError. Other
        # (non-transient) errors propagate immediately.
        resp = None
        last_err: Exception | None = None
        retryable = (
            self._openai.RateLimitError,
            self._openai.InternalServerError,
            self._openai.APIConnectionError,
        )
        for attempt in range(3):
            try:
                resp = self._client.responses.create(**kwargs)
                break
            except retryable as e:
                last_err = e
                if attempt < 2:
                    print(f"[minimax] {type(e).__name__}; sleeping 5s (attempt {attempt+1}/3)", flush=True)
                    time.sleep(5.0)
                continue
            except Exception as e:
                raise AdapterError(f"minimax request failed: {e}") from e
        if resp is None:
            raise AdapterError(f"minimax request failed after 3 attempts: {last_err}") from last_err

        text, tcs = _parse_responses_output(resp)
        u = getattr(resp, "usage", None)
        in_tok = int(getattr(u, "input_tokens", 0) or 0)
        out_tok = int(getattr(u, "output_tokens", 0) or 0)
        total = int(getattr(u, "total_tokens", in_tok + out_tok) or (in_tok + out_tok))
        cached = 0
        reasoning = 0
        if getattr(u, "input_tokens_details", None) is not None:
            cached = int(getattr(u.input_tokens_details, "cached_tokens", 0) or 0)
        if getattr(u, "output_tokens_details", None) is not None:
            reasoning = int(getattr(u.output_tokens_details, "reasoning_tokens", 0) or 0)
        finish = ""
        if getattr(resp, "incomplete_details", None):
            finish = "incomplete"
        return ModelResponse(
            text=text,
            tool_calls=tcs,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cached_input_tokens=cached,
            reasoning_output_tokens=reasoning,
            total_tokens=total,
            finish_reason=finish or ("tool_calls" if tcs else "stop"),
            response_id=getattr(resp, "id", "") or "",
        )


# ----- helpers for the Responses adapter -----


def _messages_to_responses_input(messages: list[dict]) -> list[dict]:
    """Convert chat-style messages to Responses input items.

    The Responses API uses ``content`` arrays of typed parts. For tool
    results, we emit ``{"type": "function_call_output", ...}``. For
    assistant turns with tool calls, we emit
    ``{"type": "function_call", ...}`` items alongside the message.
    """
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            content = m.get("content") or ""
            if isinstance(content, str) and content:
                out.append({"role": "system", "content": content})
            continue
        if role == "user":
            content = m.get("content") or ""
            if isinstance(content, list):
                out.append({"role": "user", "content": content})
            else:
                out.append({"role": "user", "content": str(content)})
            continue
        if role == "assistant":
            # Emit text first (if any), then each tool call as a
            # function_call item so the model can correlate the
            # function_call_output items in the next turn.
            text = m.get("content") or ""
            if text:
                out.append({"role": "assistant", "content": text})
            for tc in m.get("tool_calls", []) or []:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", "")
                if isinstance(args, str):
                    try:
                        args_obj = json.loads(args) if args.strip() else {}
                    except Exception:
                        args_obj = {"_raw": args}
                else:
                    args_obj = args
                out.append(
                    {
                        "type": "function_call",
                        "name": name,
                        "arguments": json.dumps(args_obj),
                        "call_id": tc.get("id", ""),
                    }
                )
            continue
        if role == "tool":
            # Function result message: emit a function_call_output.
            out.append(
                {
                    "type": "function_call_output",
                    "call_id": m.get("tool_call_id", ""),
                    "output": m.get("content", ""),
                }
            )
            continue
        # Unknown role: pass through as user content.
        out.append({"role": "user", "content": str(m.get("content") or "")})
    return out


def _parse_responses_output(resp) -> tuple[str, list[ToolCall]]:
    """Pull text and tool calls from a Responses object.

    Returns ``(text, tool_calls)``. ``text`` is the concatenation of
    all ``output_text`` items; ``tool_calls`` is the list of
    ``ResponseFunctionToolCall`` items parsed into ``ToolCall``.
    """
    text_parts: list[str] = []
    tcs: list[ToolCall] = []
    for item in (resp.output or []):
        t = type(item).__name__
        if t == "ResponseOutputMessage":
            for c in (item.content or []):
                if getattr(c, "type", None) == "output_text":
                    text_parts.append(getattr(c, "text", "") or "")
        elif t == "ResponseFunctionToolCall":
            raw = getattr(item, "arguments", "") or ""
            try:
                args = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw or {})
            except Exception:
                args = {"_raw": raw}
            tcs.append(
                ToolCall(
                    name=getattr(item, "name", ""),
                    args=args if isinstance(args, dict) else {"_raw": args},
                    id=getattr(item, "call_id", "") or getattr(item, "id", ""),
                )
            )
        # Other item types (reasoning, etc.) are ignored for now.
    return "".join(text_parts), tcs


# ---------------------------------------------------------------------
# Spec resolution
# ---------------------------------------------------------------------


def make_adapter(spec: str, *, reasoning_effort: Optional[str] = None) -> BaseAdapter:
    """Resolve a model spec into an adapter.

    Formats:
        mock:...
        echo
        openai:<model_name>
        openai_chat:<model_name>
        minimax[:<model_name>]
        minimax:<model_name>@<base_url>
    """
    if spec == "echo":
        return EchoModel()
    if spec.startswith("mock:"):
        return MockModel([], final_text="Task complete.")
    if spec == "mock":
        return MockModel([], final_text="Task complete.")
    if spec.startswith("openai_chat:"):
        model = spec[len("openai_chat:"):]
        return OpenAIChatAdapter(model=model)
    if spec.startswith("openai:"):
        model = spec[len("openai:"):]
        return OpenAIChatAdapter(model=model)
    if spec == "openai":
        return OpenAIChatAdapter(model=os.environ.get("EVAL_MODEL", "gpt-4o-mini"))
    if spec.startswith("minimax:"):
        rest = spec[len("minimax:"):]
        if "@" in rest:
            model, base_url = rest.split("@", 1)
        else:
            model, base_url = rest, None
        return MiniMaxAdapter(model=model, base_url=base_url, reasoning_effort=reasoning_effort)
    if spec.startswith("minimax@"):
        base_url = spec[len("minimax@"):]
        return MiniMaxAdapter(base_url=base_url, reasoning_effort=reasoning_effort)
    if spec == "minimax":
        return MiniMaxAdapter(reasoning_effort=reasoning_effort)
    raise AdapterError(f"unknown model spec: {spec!r}")
