"""Per-run metrics: provider usage + local attribution."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

from . import token_count


@dataclass
class RunMetrics:
    # Provider usage (separate input vs output, plus provider sub-fields).
    turns: int = 0
    tool_calls: int = 0
    total_input_tokens: int = 0          # total input (incl. cached + reasoning input if any)
    total_input_cached_tokens: int = 0   # portion of input served from cache
    total_input_uncached_tokens: int = 0 # input - cached (what's billed as fresh input)
    total_output_tokens: int = 0
    total_output_reasoning_tokens: int = 0  # reasoning tokens (subset of output)
    total_billed_tokens: int = 0         # input + output, the spec's "total_billed_tokens"
    peak_input_tokens: int = 0

    # Local attribution.
    system_prompt_tokens: int = 0
    tool_schema_tokens: int = 0
    user_prompt_tokens: int = 0
    assistant_text_tokens: int = 0
    assistant_tool_arg_tokens: int = 0
    write_tool_arg_tokens: int = 0
    tool_output_tokens: dict = field(default_factory=dict)
    # Breakdown of tool call args by tool name (for write/edit).
    write_tool_arg_tokens_by_op: dict = field(default_factory=dict)

    def record_turn(
        self,
        *,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
        cached_input_tokens: Optional[int] = None,
        reasoning_output_tokens: Optional[int] = None,
        local_input_tokens: Optional[int] = None,
    ) -> None:
        self.turns += 1
        if input_tokens is not None:
            self.total_input_tokens += int(input_tokens)
        if output_tokens is not None:
            self.total_output_tokens += int(output_tokens)
        if cached_input_tokens is not None:
            self.total_input_cached_tokens += int(cached_input_tokens)
            self.total_input_uncached_tokens += max(
                0, int(input_tokens or 0) - int(cached_input_tokens)
            )
        elif input_tokens is not None:
            # We know total input but not the cached portion; assume zero
            # cached for this turn. This matches the older API surface.
            pass
        if reasoning_output_tokens is not None:
            self.total_output_reasoning_tokens += int(reasoning_output_tokens)
        # Billed = input + output, matching the spec's "total_billed_tokens".
        self.total_billed_tokens = (
            self.total_input_tokens + self.total_output_tokens
        )
        # Peak
        if local_input_tokens is not None:
            if local_input_tokens > self.peak_input_tokens:
                self.peak_input_tokens = local_input_tokens

    def record_tool_call(
        self,
        *,
        name: str,
        args_tokens: int,
        output_tokens: int,
    ) -> None:
        self.tool_calls += 1
        self.assistant_tool_arg_tokens += args_tokens
        if name in ("apply_patch", "edit_batch"):
            self.write_tool_arg_tokens += args_tokens
            self.write_tool_arg_tokens_by_op[name] = (
                self.write_tool_arg_tokens_by_op.get(name, 0) + args_tokens
            )
        self.tool_output_tokens[name] = (
            self.tool_output_tokens.get(name, 0) + output_tokens
        )

    def record_assistant_text(self, text: str) -> None:
        self.assistant_text_tokens += token_count.count_tokens(text)

    def to_dict(self) -> dict:
        d = asdict(self)
        # Keep the spec's expected key name even though it's also a stored
        # field now. ``asdict`` would already include it; this keeps the
        # serialized shape stable for downstream consumers.
        d["total_billed_tokens"] = self.total_billed_tokens
        return d
