"""Guards for model-facing prompt/tool text."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

from eval.runner.eval import (
    ROOT,
    VARIANT_TOOLS,
    _tools_with_task_enums,
    build_system_prompt,
    load_task,
)


def _blocked_terms() -> list[str]:
    return [
        "".join(["d", "s", "t", "_", "before"]),
        "".join(["d", "s", "t", "_", "after"]),
        "".join(["s", "r", "c", "_", "replace"]),
        "".join(["c", "o", "p", "y", "_", "span"]),
        "".join(["indent", "_", "span"]),
        "no " + "longer",
        "former" + "ly",
        "depre" + "cated",
        "histor" + "ical",
    ]


class TestModelSurface(unittest.TestCase):
    def test_initial_messages_and_schemas_have_no_stale_edit_api_terms(self):
        exact_field = "".join(["p", "o", "s"])
        chunks: list[str] = []
        tasks = [load_task(p) for p in sorted((ROOT / "eval" / "tasks").glob("*.yml"))]
        for variant in sorted(VARIANT_TOOLS):
            chunks.append(build_system_prompt(variant)[0])
            for task in tasks:
                chunks.append(task.user_prompt)
                chunks.append(json.dumps(_tools_with_task_enums(VARIANT_TOOLS[variant], task), sort_keys=True))
        surface = "\n".join(chunks)
        for term in _blocked_terms():
            self.assertNotIn(term, surface)
        self.assertIsNone(re.search(r"(?<![A-Za-z0-9_])" + exact_field + r"(?![A-Za-z0-9_])", surface))

    def test_docs_and_task_prompts_have_no_stale_edit_api_terms(self):
        exact_field = "".join(["p", "o", "s"])
        text_paths = [ROOT / "README.md", ROOT / "spec.md"]
        text_paths += sorted((ROOT / "eval" / "prompts").glob("*.md"))
        text_paths += sorted((ROOT / "eval" / "tasks").glob("*.yml"))
        text_paths += sorted((ROOT / "eval" / "runner").glob("*.py"))
        combined = "\n".join(p.read_text() for p in text_paths)
        for term in _blocked_terms():
            self.assertNotIn(term, combined)
        self.assertIsNone(re.search(r"(?<![A-Za-z0-9_])" + exact_field + r"(?![A-Za-z0-9_])", combined))

    def test_task_yaml_comments_are_not_user_prompt(self):
        for path in sorted((ROOT / "eval" / "tasks").glob("*.yml")):
            raw = path.read_text()
            loaded = yaml.safe_load(raw)
            self.assertEqual(loaded.get("user_prompt", "").strip(), load_task(path).user_prompt)
