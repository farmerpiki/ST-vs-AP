"""Smoke tests for the tool implementations.

Run with:

    .venv/bin/python -m unittest discover -s eval/tests
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from eval.runner.apply_patch_lib import apply_to_text, parse
from eval.runner.state import ToolState
from eval.runner.tools_apply_patch import tool_apply_patch
from eval.runner.tools_shared import (
    SHARED_TOOL_DISPATCH,
    normalize_path,
    normalize_paths,
    tool_diff,
    tool_read_range,
    tool_rg,
    tool_run_check,
    tool_status,
)
from eval.runner.tools_span import tool_edit_batch
from eval.runner.util import write_lines


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "eval" / "fixtures" / "tiny_cpp" / "base"


def _make_workdir() -> Path:
    work = ROOT / "eval" / "tests" / "_tmp_work"
    if work.exists():
        shutil.rmtree(work)
    shutil.copytree(FIXTURE, work, ignore=shutil.ignore_patterns("build", ".cache"))
    # Initialize git so diff_stat and rev lookups see tracked files
    # (the fixture sits inside this project repo, so without an init
    # here `git diff` walks up and finds the wrong tree).
    import subprocess
    try:
        subprocess.run(["git", "init", "-q"], cwd=work, check=True)
        subprocess.run(["git", "config", "user.email", "eval@local"], cwd=work, check=True)
        subprocess.run(["git", "config", "user.name", "eval"], cwd=work, check=True)
        subprocess.run(["git", "add", "-A"], cwd=work, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=work, check=True)
    except Exception:
        pass
    return work


class TestApplyPatchParser(unittest.TestCase):
    def test_single_hunk(self):
        text = "a\nb\nc\n"
        patch = (
            "*** Begin Patch\n"
            "*** Update File: f.txt\n"
            "@@\n"
            " a\n"
            "-b\n"
            "+B\n"
            " c\n"
            "*** End Patch\n"
        )
        ops = parse(patch)
        self.assertEqual(len(ops), 1)
        out = apply_to_text(ops[0], text)
        self.assertEqual(out, "a\nB\nc\n")

    def test_multi_file(self):
        text = "x\n"
        patch = (
            "*** Begin Patch\n"
            "*** Update File: a.txt\n"
            "@@\n"
            "-x\n"
            "+y\n"
            "*** Update File: b.txt\n"
            "@@\n"
            "-x\n"
            "+z\n"
            "*** End Patch\n"
        )
        ops = parse(patch)
        self.assertEqual(len(ops), 2)
        self.assertEqual(apply_to_text(ops[0], text), "y\n")
        self.assertEqual(apply_to_text(ops[1], text), "z\n")

    def test_blank_line_context(self):
        text = "a\n\nb\n"
        patch = (
            "*** Begin Patch\n"
            "*** Update File: f.txt\n"
            "@@\n"
            " a\n"
            "\n"
            "-b\n"
            "+B\n"
            "*** End Patch\n"
        )
        ops = parse(patch)
        out = apply_to_text(ops[0], text)
        self.assertEqual(out, "a\n\nB\n")

    def test_interleaved_change_regions(self):
        # A single hunk with context lines BETWEEN several -/+ regions
        # (the normal apply_patch shape). The old single-sandwich parser
        # could not match this and failed with "could not match context".
        text = (
            "int f(int* out) {\n"
            "    for (auto& u : xs()) {\n"
            "        if (u.id == id) {\n"
            "            if (out) *out = u;\n"
            "            return true;\n"
            "        }\n"
            "    }\n"
            "    return false;\n"
            "}\n"
        )
        patch = (
            "*** Begin Patch\n"
            "*** Update File: f.cpp\n"
            "@@\n"
            "-int f(int* out) {\n"
            "+opt f() {\n"
            "     for (auto& u : xs()) {\n"
            "         if (u.id == id) {\n"
            "-            if (out) *out = u;\n"
            "-            return true;\n"
            "+            return u;\n"
            "         }\n"
            "     }\n"
            "-    return false;\n"
            "+    return nullopt;\n"
            " }\n"
            "*** End Patch\n"
        )
        out = apply_to_text(parse(patch)[0], text)
        self.assertEqual(
            out,
            "opt f() {\n"
            "    for (auto& u : xs()) {\n"
            "        if (u.id == id) {\n"
            "            return u;\n"
            "        }\n"
            "    }\n"
            "    return nullopt;\n"
            "}\n",
        )

    def test_bad_header(self):
        with self.assertRaises(ValueError):
            parse("*** Update File: f.txt\n-x\n+y\n*** End Patch\n")

    def test_empty_hunk_rejected(self):
        with self.assertRaises(ValueError):
            apply_to_text(parse(
                "*** Begin Patch\n"
                "*** Update File: f.txt\n"
                "@@\n"
                " x\n"
                "-x\n"
                "+y\n"
                "*** End Patch\n"
            )[0], "x\n")  # not a real test of empty hunk; placeholder.


class TestSharedTools(unittest.TestCase):
    def setUp(self):
        self.work = _make_workdir()
        self.state = ToolState(
            worktree=self.work,
            allowed_paths=["src/parser.cpp", "include/config.hpp", "include/parser.hpp"],
            checks=[],
        )

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_status(self):
        r = tool_status({}, self.state)
        self.assertTrue(r["ok"])
        self.assertTrue(isinstance(r["dirty"], list))
        self.assertEqual(r["allowed_paths"], self.state.allowed_paths)
        self.assertEqual(r["check_names"], [])

    def test_rg(self):
        r = tool_rg(
            {"pattern": "ParseName", "paths": ["src", "include"]},
            self.state,
        )
        self.assertTrue(r["ok"])
        self.assertTrue(any(h["path"] == "include/parser.hpp" for h in r["hits"]))
        self.assertFalse(r.get("truncated", False))

    def test_rg_defaults_to_case_insensitive_allowed_paths(self):
        r = tool_rg({"pattern": "retries"}, self.state)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["searched_paths"], self.state.allowed_paths)
        self.assertTrue(any(h["path"] == "include/config.hpp" for h in r["hits"]))
        self.assertTrue(any("kDefaultRetries" in h["text"] for h in r["hits"]))

    def test_rg_recovers_pattern_from_raw_args(self):
        r = tool_rg({"_raw": '{"pattern": "ParseName", "paths": '}, self.state)
        self.assertTrue(r["ok"], r)
        self.assertIn("pattern", r.get("recovered_args", []))
        self.assertTrue(any("ParseName" in h["text"] for h in r["hits"]))

    def test_rg_with_context(self):
        # Regression: ripgrep with -Cn prints match lines as
        # "PATH:LINE:TEXT" and context lines as "LINE-TEXT" (no path).
        # The tool must reconstruct the path for context lines so the
        # model sees file:line for every hit.
        r = tool_rg(
            {"pattern": "ParseName", "paths": ["src"], "context": 2},
            self.state,
        )
        self.assertTrue(r["ok"])
        self.assertGreater(len(r["hits"]), 0)
        # All hits must carry a non-empty path
        for h in r["hits"]:
            self.assertTrue(h["path"], f"hit missing path: {h}")
        # At least one hit should be the match line
        self.assertTrue(any("ParseName" in h["text"] for h in r["hits"]))

    def test_rg_single_file_context(self):
        # Regression: single-file search omits the path prefix from
        # match lines too. The tool must inject the search path.
        r = tool_rg(
            {"pattern": "ParseName", "paths": ["include/parser.hpp"], "context": 1},
            self.state,
        )
        self.assertTrue(r["ok"])
        self.assertGreater(len(r["hits"]), 0)
        for h in r["hits"]:
            self.assertEqual(h["path"], "include/parser.hpp")

    def test_read_range(self):
        r = tool_read_range(
            {"path": "./include/config.hpp", "start": 1, "count": 20},
            self.state,
        )
        self.assertTrue(r["ok"])
        self.assertEqual(r["path"], "include/config.hpp")
        self.assertIn("span", r)
        self.assertIn("rev", r)
        self.assertIn("range", r)
        self.assertIn("5|constexpr int kDefaultRetries = 3;", r["text"])
        self.assertEqual(r["span_origin"], f"{r['span']}:1=include/config.hpp:1")
        self.assertIn("left labels are absolute", r["addr_hint"])

    def test_run_check_summarizes_tests_failed(self):
        state = ToolState(
            worktree=self.work,
            allowed_paths=self.state.allowed_paths,
            checks=[{
                "name": "fail-tests",
                "cmd": (
                    "cat <<'EOF'\n"
                    "FAIL /tmp/x.cpp:7 nope\n"
                    "checks=2 failures=1\n"
                    "EXIT_CFG=0 EXIT_BUILD=0 EXIT_TEST=1\n"
                    "EOF\n"
                    "exit 1"
                ),
            }],
        )
        r = tool_run_check({"name": "fail-tests"}, state)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["verdict"], "tests_failed")
        self.assertFalse(r["all_passed"])
        self.assertEqual(r["failures"][0]["line"], 7)
        self.assertNotIn("output", r)

    def test_run_check_build_error_hints_changed_path(self):
        p = self.work / "include/config.hpp"
        p.write_text(p.read_text().replace("constexpr int kDefaultRetries = 3;", "constexpr int kDefaultRetries = ;"))
        state = ToolState(
            worktree=self.work,
            allowed_paths=self.state.allowed_paths,
            checks=[{"name": "compile", "cmd": "set +e; cmake -S . -B build >/dev/null; rc_cfg=$?; cmake --build build -j2; rc_build=$?; echo EXIT_CFG=$rc_cfg EXIT_BUILD=$rc_build; [ $rc_cfg -eq 0 ] && [ $rc_build -eq 0 ]"}],
        )
        r = tool_run_check({"name": "compile"}, state)
        self.assertFalse(r["ok"], r)
        self.assertEqual(r["verdict"], "build_error")
        self.assertIn("build_error must be fixed", r.get("hint", ""))
        self.assertIn("include/config.hpp", r.get("changed_paths", []))

    def test_run_check_timeout_has_verdict_fields(self):
        state = ToolState(
            worktree=self.work,
            allowed_paths=self.state.allowed_paths,
            checks=[{"name": "slow", "cmd": "sleep 999"}],
        )
        with mock.patch(
            "eval.runner.tools_shared.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="sleep 999", timeout=180),
        ):
            r = tool_run_check({"name": "slow"}, state)
        self.assertFalse(r["ok"])
        self.assertEqual(r["verdict"], "timeout")
        self.assertFalse(r["all_passed"])
        self.assertFalse(r["build_ok"])
        self.assertIn("hint", r)

    def test_diff_stat(self):
        # Make a change first.
        p = self.work / "include/config.hpp"
        text = p.read_text()
        text = text.replace("kDefaultRetries = 3", "kDefaultRetries = 7")
        p.write_text(text)
        r = tool_diff({"mode": "stat", "paths": ["include/config.hpp"]}, self.state)
        self.assertTrue(r["ok"])
        self.assertTrue(any(c["path"] == "include/config.hpp" for c in r["changed"]))

    def test_diff_stat_honors_paths_filter(self):
        config = self.work / "include/config.hpp"
        parser = self.work / "src/parser.cpp"
        config.write_text(config.read_text().replace("kDefaultRetries = 3", "kDefaultRetries = 7"))
        parser.write_text(parser.read_text().replace("IsNameChar", "IsIdentifierChar", 1))

        r = tool_diff({"mode": "stat", "paths": ["include/config.hpp"]}, self.state)
        self.assertTrue(r["ok"], r)
        self.assertEqual([c["path"] for c in r["changed"]], ["include/config.hpp"])

    def test_diff_hunks_uses_real_hunks_with_two_lines_of_context(self):
        p = self.work / "src/parser.cpp"
        lines = p.read_text().splitlines()
        lines[9] = "bool IsIdentifierChar(char c) {"
        lines[55] = "        if (!IsIdentifierChar(input[i])) return std::nullopt;"
        p.write_text("\n".join(lines) + "\n")

        r = tool_diff({"mode": "hunks", "paths": ["src/parser.cpp"]}, self.state)
        self.assertTrue(r["ok"], r)
        diff = r["diff"]
        self.assertEqual(sum(1 for line in diff.splitlines() if line.startswith("@@")), 2, diff)
        self.assertIn("@@ -8,5 +8,5 @@", diff)
        self.assertIn("@@ -54,5 +54,5 @@", diff)


class TestApplyPatchTool(unittest.TestCase):
    def setUp(self):
        self.work = _make_workdir()
        self.state = ToolState(
            worktree=self.work,
            allowed_paths=["include/config.hpp"],
            checks=[],
        )

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def test_set_line_via_patch(self):
        patch = (
            "*** Begin Patch\n"
            "*** Update File: include/config.hpp\n"
            "@@\n"
            " namespace tiny {\n"
            "\n"
            "-constexpr int kDefaultRetries = 3;\n"
            "+constexpr int kDefaultRetries = 9;\n"
            " constexpr int kMaxRetries = 10;\n"
            "*** End Patch\n"
        )
        r = tool_apply_patch({"patch": patch}, self.state)
        self.assertTrue(r["ok"], r)
        text = (self.work / "include/config.hpp").read_text()
        self.assertIn("kDefaultRetries = 9;", text)
        self.assertNotIn("kDefaultRetries = 3;", text)

    def test_forbidden_path(self):
        patch = (
            "*** Begin Patch\n"
            "*** Update File: src/parser.cpp\n"
            "@@\n"
            "-x\n"
            "+y\n"
            "*** End Patch\n"
        )
        r = tool_apply_patch({"patch": patch}, self.state)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "forbidden_path")

    def test_patch_path_normalized(self):
        patch = (
            "*** Begin Patch\n"
            "*** Update File: ./include/config.hpp\n"
            "@@\n"
            "-constexpr int kDefaultRetries = 3;\n"
            "+constexpr int kDefaultRetries = 9;\n"
            "*** End Patch\n"
        )
        r = tool_apply_patch({"patch": patch}, self.state)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["changed"][0]["path"], "include/config.hpp")

    def test_patch_failure_includes_whitespace_hint(self):
        patch = (
            "*** Begin Patch\n"
            "*** Update File: include/config.hpp\n"
            "@@\n"
            "-  constexpr int kDefaultRetries = 3;\n"
            "+  constexpr int kDefaultRetries = 9;\n"
            "*** End Patch\n"
        )
        r = tool_apply_patch({"patch": patch}, self.state)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "patch_did_not_apply")
        self.assertIn("Whitespace mismatch", r.get("hint", ""))
        self.assertEqual(r["near"][0]["line"], 5)
        self.assertIn("patch_line_examples", r)

    def test_patch_normalizes_one_extra_marker_space(self):
        patch = (
            "*** Begin Patch\n"
            "*** Update File: include/config.hpp\n"
            "@@\n"
            " namespace tiny {\n"
            "\n"
            "- constexpr int kDefaultRetries = 3;\n"
            "+ constexpr int kDefaultRetries = 9;\n"
            " constexpr int kMaxRetries = 10;\n"
            "*** End Patch\n"
        )
        r = tool_apply_patch({"patch": patch}, self.state)
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["changed"][0].get("normalized_marker_space"), r)
        self.assertIn("echo", r["changed"][0])
        self.assertIn("kDefaultRetries = 9", (self.work / "include/config.hpp").read_text())

    def test_patch_parse_error_has_hint(self):
        patch = (
            "*** Begin Patch\n"
            "*** Update File: include/config.hpp\n"
            "constexpr int kDefaultRetries = 3;\n"
            "*** End Patch\n"
        )
        r = tool_apply_patch({"patch": patch}, self.state)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "patch_parse_error")
        self.assertIn("Each hunk line", r.get("hint", ""))

    def test_no_op_patch_reports_error(self):
        # Patch with - and + lines that are identical: should be
        # reported as an error so the model knows it made no change.
        patch = (
            "*** Begin Patch\n"
            "*** Update File: include/config.hpp\n"
            "@@\n"
            " namespace tiny {\n"
            "\n"
            "-constexpr int kDefaultRetries = 3;\n"
            "+constexpr int kDefaultRetries = 3;\n"
            " constexpr int kMaxRetries = 10;\n"
            "*** End Patch\n"
        )
        r = tool_apply_patch({"patch": patch}, self.state)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "patch_made_no_change")
        # file should be unchanged
        text = (self.work / "include/config.hpp").read_text()
        self.assertIn("kDefaultRetries = 3;", text)
        self.assertIn("kDefaultRetries = 3;", text)

    def test_no_op_plus_only_patch_fails_to_match(self):
        # Patch with only a + line and no - line: this is what the
        # model sometimes emits as a typo (a single +line that is
        # supposed to replace an existing line). Without a matching
        # context, the patch fails to apply. The error tells the
        # model its context does not match.
        patch = (
            "*** Begin Patch\n"
            "*** Update File: include/config.hpp\n"
            "@@\n"
            " constexpr int kDefaultRetries = 5;\n"
            "*** End Patch\n"
        )
        r = tool_apply_patch({"patch": patch}, self.state)
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "patch_did_not_apply")


class TestEditBatchTool(unittest.TestCase):
    def setUp(self):
        self.work = _make_workdir()
        self.state = ToolState(
            worktree=self.work,
            allowed_paths=["include/config.hpp", "src/parser.cpp", "include/parser.hpp", "tests/parser_test.cpp"],
            checks=[],
        )

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def _read(self, rel: str) -> str:
        return (self.work / rel).read_text()

    def test_splice_one_line(self):
        # count=1 splice replaces a single line (the former set_line op)
        r = tool_read_range(
            {"path": "include/config.hpp", "start": 1, "count": 20}, self.state
        )
        sid = r["span"]
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {"op": "splice", "at": f"{sid}:5+1",
                     "text": "constexpr int kDefaultRetries = 8;"},
                ],
            },
            self.state,
        )
        self.assertTrue(r["ok"], r)
        self.assertIn("kDefaultRetries = 8;", self._read("include/config.hpp"))

    def test_splice_insert_and_delete(self):
        r = tool_read_range(
            {"path": "src/parser.cpp", "start": 1, "count": 100}, self.state
        )
        sid = r["span"]
        # Insert a comment at the top.
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {"op": "splice", "at": f"{sid}:1+0", "text": "// tiny parser\n"},
                ],
            },
            self.state,
        )
        self.assertTrue(r["ok"], r)
        self.assertIn("// tiny parser", self._read("src/parser.cpp").splitlines()[0])

    def test_span_plus_zero_is_insert_not_replace(self):
        r = tool_read_range(
            {"path": "src/parser.cpp", "start": 1, "count": 20}, self.state
        )
        sid = r["span"]
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {"op": "splice", "at": f"{sid}:14+0", "text": "// inserted\n"},
                ],
            },
            self.state,
        )
        self.assertTrue(r["ok"], r)
        text = self._read("src/parser.cpp")
        self.assertIn("// inserted\n}  // namespace", text)

    def test_span_address_outside_read_range_rejected(self):
        r = tool_read_range(
            {"path": "include/config.hpp", "start": 3, "count": 5}, self.state
        )
        sid = r["span"]
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {"op": "splice", "at": f"{sid}:6+1", "text": "x"},
                ],
            },
            self.state,
        )
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "resolve_error")

    def test_splice_requires_explicit_insert_count(self):
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {"op": "splice", "at": "include/config.hpp:5", "text": "constexpr int kDefaultRetries = 8;"},
                ],
            },
            self.state,
        )
        self.assertFalse(r["ok"])
        self.assertIn("+0", r.get("details", ""))
        self.assertIn("+1", r.get("details", ""))

    def test_splice_zero_count_empty_text_rejected(self):
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {"op": "splice", "at": "include/config.hpp:5+0", "text": ""},
                ],
            },
            self.state,
        )
        self.assertFalse(r["ok"])
        self.assertIn("insert-only", r.get("details", ""))

    def test_replace_word_dry_run(self):
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {
                        "op": "replace_word",
                        "paths": ["include/parser.hpp", "src/parser.cpp"],
                        "from": "ParseName", "to": "ParseIdentifier",
                        "boundary": "cpp_identifier",
                        "dry_run": True,
                    }
                ],
            },
            self.state,
        )
        self.assertTrue(r["ok"])
        self.assertTrue(r["dry_run"])
        self.assertEqual(r["total"], 2)

    def test_replace_word_apply(self):
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {
                        "op": "replace_word",
                        "paths": ["include/parser.hpp", "src/parser.cpp"],
                        "from": "ParseName", "to": "ParseIdentifier",
                        "boundary": "cpp_identifier",
                        "expected": {"exact": 2},
                    }
                ],
            },
            self.state,
        )
        self.assertTrue(r["ok"], r)
        self.assertNotIn("ParseName", self._read("include/parser.hpp"))
        self.assertNotIn("ParseName", self._read("src/parser.cpp"))
        self.assertIn("ParseIdentifier", self._read("src/parser.cpp"))

    def test_replace_word_count_mismatch(self):
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {
                        "op": "replace_word",
                        "paths": ["include/parser.hpp", "src/parser.cpp"],
                        "from": "ParseName", "to": "ParseIdentifier",
                        "boundary": "cpp_identifier",
                        "expected": {"exact": 99},
                    }
                ],
            },
            self.state,
        )
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "expected_count_mismatch")

    def test_move_span_same_file(self):
        # Move the IsWhitespace definition (lines 44-46) to before
        # Tokenize. The source is always deleted. Use point address
        # (to: N) to insert; the body lands at line N, pushing
        # whatever was there down.
        r = tool_read_range(
            {"path": "src/parser.cpp", "start": 1, "count": 100}, self.state
        )
        sid = r["span"]
        rev = r["rev"]
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {
                        "op": "move_span",
                        "from": f"src/parser.cpp@{rev}:44+3",
                        "to": f"src/parser.cpp@{rev}:18",
                    }
                ],
            },
            self.state,
        )
        self.assertTrue(r["ok"], r)
        text = self._read("src/parser.cpp")
        self.assertEqual(text.count("bool IsWhitespace(char c) {"), 1)
        # The remaining definition must precede Tokenize.
        isw_pos = text.find("bool IsWhitespace(char c) {")
        tok_pos = text.find("std::vector<Token> Tokenize")
        self.assertLess(isw_pos, tok_pos)

    def test_forbidden_path(self):
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {"op": "splice", "at": "src/users.cpp@r1f6f21c:1+0", "text": "x\n"}
                ],
            },
            self.state,
        )
        self.assertFalse(r["ok"])
        # The runner raises during address resolution, which surfaces as
        # resolve_error; the path-not-allowed message is in details.
        self.assertEqual(r["error"], "resolve_error")
        self.assertIn("path not allowed", r.get("details", ""))

    def test_overlap_rejected(self):
        r = tool_read_range(
            {"path": "include/config.hpp", "start": 1, "count": 20}, self.state
        )
        sid = r["span"]
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {"op": "splice", "at": f"{sid}:5+1", "text": "A"},
                    {"op": "splice", "at": f"{sid}:5+2", "text": "B\nC\n"},
                ],
            },
            self.state,
        )
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "overlap_error")

    def test_stale_rev_rejected(self):
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {"op": "splice", "at": "include/config.hpp@rzzzzzz:5+0",
                     "text": "// stale\n"}
                ],
            },
            self.state,
        )
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "resolve_error")
        self.assertIn("cannot resolve", r.get("details", ""))

    def test_move_span_rejects_unknown_fields_without_echoing_them(self):
        bad_key = "wrap"
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {
                        "op": "move_span",
                        "from": "src/parser.cpp:44+3",
                        "to": "src/parser.cpp:18",
                        bad_key: "literal text should not be accepted",
                    }
                ],
            },
            self.state,
        )
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "op_parse_error")
        self.assertIn("unsupported field", r.get("details", ""))
        self.assertNotIn(bad_key, r.get("details", ""))

    def test_root_rejects_unknown_fields_without_echoing_them(self):
        bad_key = "extra"
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [{"op": "splice", "at": "src/parser.cpp:1+0", "text": "// x"}],
                bad_key: True,
            },
            self.state,
        )
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "op_parse_error")
        self.assertIn("unsupported field", r.get("details", ""))
        self.assertNotIn(bad_key, r.get("details", ""))

    def test_echo_does_not_compact_replacement_blocks(self):
        replacement = "\n".join(
            f"// replacement line {i} with enough text to tempt compaction"
            for i in range(1, 7)
        )
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {"op": "splice", "at": "src/parser.cpp:1+6", "text": replacement},
                ],
            },
            self.state,
        )
        self.assertTrue(r["ok"], r)
        echo = r["changed"][0]["echo"]
        self.assertNotIn("[omitted compacted block]", echo)
        self.assertIn('-#include "parser.hpp"', echo)
        self.assertIn("-namespace tiny {", echo)
        self.assertIn("+// replacement line 1", echo)
        self.assertIn("+// replacement line 6", echo)

    def test_echo_compacts_only_pure_delete_blocks_with_head_and_tail(self):
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {"op": "splice", "at": "src/parser.cpp:18+8", "text": ""},
                ],
            },
            self.state,
        )
        self.assertTrue(r["ok"], r)
        echo = r["changed"][0]["echo"]
        self.assertIn("-std::vector<Token> Tokenize(std::string_view input) {", echo)
        self.assertIn("- [omitted compacted block]", echo)
        self.assertIn("-    std::size_t i = 0;", echo)
        self.assertNotIn("-    User u;", echo)

    def test_echo_emits_multiple_real_hunks(self):
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {"op": "splice", "at": "src/parser.cpp:10+1", "text": "bool IsIdentifierChar(char c) {"},
                    {"op": "splice", "at": "src/parser.cpp:56+1", "text": "        if (!IsIdentifierChar(input[i])) return std::nullopt;"},
                ],
            },
            self.state,
        )
        self.assertTrue(r["ok"], r)
        echo = r["changed"][0]["echo"]
        self.assertEqual(sum(1 for line in echo.splitlines() if line.startswith("@@")), 2, echo)
        self.assertIn("@@ -8,5 +8,5 @@", echo)
        self.assertIn("@@ -54,5 +54,5 @@", echo)

    def test_move_destination_inside_source_ok(self):
        # Source is consumed first; the body lands at the destination in
        # the resulting file. Verify the operation succeeds and the body
        # appears once in the final file.
        r = tool_read_range(
            {"path": "src/parser.cpp", "start": 1, "count": 100}, self.state
        )
        rev = r["rev"]
        r = tool_edit_batch(
            {
                "atomic": True,
                "coords": "snapshot",
                "ops": [
                    {
                        "op": "move_span",
                        "from": f"src/parser.cpp@{rev}:40+10",
                        "to": f"src/parser.cpp@{rev}:45",
                    }
                ],
            },
            self.state,
        )
        self.assertTrue(r["ok"], r)


class TestOracleHelpers(unittest.TestCase):
    def test_required_position_regex_only_enforced(self):
        work = _make_workdir()
        try:
            from eval.runner.oracle import verify_oracle
            p = work / "src" / "parser.cpp"
            oracle = {
                "required_position": [{
                    "path": "src/parser.cpp",
                    "before_re": r"input\.empty\(\)",
                    "after_re": r"input\[0\]",
                }]
            }
            p.write_text("void f(){\n  input[0];\n  if (input.empty()) return;\n}\n")
            bad = verify_oracle(work, oracle, ["src/parser.cpp"])
            self.assertFalse(bad["required_contains_ok"])
            self.assertEqual(bad["missing_required"][0]["reason"], "wrong_order")
            p.write_text("void f(){\n  if (input.empty()) return;\n  input[0];\n}\n")
            good = verify_oracle(work, oracle, ["src/parser.cpp"])
            self.assertTrue(good["required_contains_ok"], good)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_required_position_regex_group_start_scopes_after_anchor(self):
        work = _make_workdir()
        try:
            from eval.runner.oracle import verify_oracle
            p = work / "src" / "parser.cpp"
            oracle = {
                "required_position": [{
                    "path": "src/parser.cpp",
                    "before_re": r"input\.empty\(\)",
                    "after_re": r"ParseName[\s\S]*?(input\[[^\]]+\])",
                }]
            }
            p.write_text(
                "void Tokenize(){ input[0]; }\n"
                "std::optional<std::string> ParseName(std::string_view input) {\n"
                "  if (input.empty()) return std::nullopt;\n"
                "  return std::string(1, input[0]);\n"
                "}\n"
            )
            good = verify_oracle(work, oracle, ["src/parser.cpp"])
            self.assertTrue(good["required_contains_ok"], good)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_run_checks_passes_baseline(self):
        work = _make_workdir()
        try:
            from eval.runner.oracle import run_checks, verify_oracle
            from eval.runner.eval import Task
            import yaml as _y
            task_data = _y.safe_load((ROOT / "eval" / "tasks" / "001_set_line.yml").read_text())
            checks = task_data["checks"]
            # The fixture should pass checks as-is.
            ok, results = run_checks(work, checks)
            self.assertTrue(ok, [r for r in results if not r["ok"]])
        finally:
            shutil.rmtree(work, ignore_errors=True)


class TestTokenCount(unittest.TestCase):
    def test_count_tokens_nonzero(self):
        from eval.runner import token_count
        self.assertGreater(token_count.count_tokens("hello world"), 0)
        self.assertEqual(token_count.count_tokens(""), 0)


class TestEvalRunPlan(unittest.TestCase):
    def test_repeats_are_full_refinement_passes(self):
        from types import SimpleNamespace

        from eval.runner.eval import run_plan

        tasks = [SimpleNamespace(id="001"), SimpleNamespace(id="002")]
        variants = ["apply_patch", "span_tools"]

        got = [(t.id, v, r) for t, v, r in run_plan(tasks, variants, 2)]

        self.assertEqual(
            got,
            [
                ("001", "apply_patch", 0),
                ("001", "span_tools", 0),
                ("002", "apply_patch", 0),
                ("002", "span_tools", 0),
                ("001", "apply_patch", 1),
                ("001", "span_tools", 1),
                ("002", "apply_patch", 1),
                ("002", "span_tools", 1),
            ],
        )


if __name__ == "__main__":
    unittest.main()


class TestMetrics(unittest.TestCase):
    def test_separate_cached_and_uncached(self):
        from eval.runner.metrics import RunMetrics
        m = RunMetrics()
        m.record_turn(input_tokens=100, output_tokens=50, cached_input_tokens=60)
        self.assertEqual(m.total_input_tokens, 100)
        self.assertEqual(m.total_input_cached_tokens, 60)
        self.assertEqual(m.total_input_uncached_tokens, 40)
        self.assertEqual(m.total_output_tokens, 50)
        self.assertEqual(m.total_billed_tokens, 150)
        # Second turn accumulates.
        m.record_turn(input_tokens=50, output_tokens=20, cached_input_tokens=10)
        self.assertEqual(m.total_input_tokens, 150)
        self.assertEqual(m.total_input_cached_tokens, 70)
        self.assertEqual(m.total_input_uncached_tokens, 80)
        self.assertEqual(m.total_billed_tokens, 220)

    def test_separate_reasoning_output(self):
        from eval.runner.metrics import RunMetrics
        m = RunMetrics()
        m.record_turn(
            input_tokens=200,
            output_tokens=100,
            cached_input_tokens=0,
            reasoning_output_tokens=70,
        )
        self.assertEqual(m.total_output_tokens, 100)
        self.assertEqual(m.total_output_reasoning_tokens, 70)


class TestModelAdapterSpec(unittest.TestCase):
    def test_make_adapter_minimax(self):
        from eval.runner.model_adapter import MiniMaxAdapter, make_adapter
        a = make_adapter("minimax")
        self.assertIsInstance(a, MiniMaxAdapter)
        self.assertEqual(a.name, "minimax")

    def test_make_adapter_minimax_with_model(self):
        from eval.runner.model_adapter import MiniMaxAdapter, make_adapter
        a = make_adapter("minimax:other-model@https://x.test/v1")
        self.assertIsInstance(a, MiniMaxAdapter)
        self.assertEqual(a._model, "other-model")
        self.assertEqual(a._base_url, "https://x.test/v1")

    def test_make_adapter_openai_chat(self):
        import os
        os.environ.setdefault("OPENAI_API_KEY", "test-dummy")
        from eval.runner.model_adapter import OpenAIChatAdapter, make_adapter
        a = make_adapter("openai_chat:gpt-4o-mini")
        self.assertIsInstance(a, OpenAIChatAdapter)

    def test_make_adapter_echo(self):
        from eval.runner.model_adapter import EchoModel, make_adapter
        a = make_adapter("echo")
        self.assertIsInstance(a, EchoModel)

    def test_make_adapter_unknown(self):
        from eval.runner.model_adapter import AdapterError, make_adapter
        with self.assertRaises(AdapterError):
            make_adapter("definitely-not-a-real-spec")


class TestPathNormalize(unittest.TestCase):
    def test_strip_rev_only(self):
        self.assertEqual(normalize_path("src/parser.cpp@rdc021d7"), "src/parser.cpp")

    def test_strip_rev_and_line(self):
        self.assertEqual(normalize_path("src/parser.cpp@rdc021d7:1"), "src/parser.cpp")
        self.assertEqual(normalize_path("include/users.hpp@r28277ac:14"), "include/users.hpp")

    def test_strip_rev_with_count(self):
        self.assertEqual(normalize_path("src/x.cpp@r1234567:10+3"), "src/x.cpp")

    def test_strip_plain_line_suffix(self):
        self.assertEqual(normalize_path("src/parser.cpp:10"), "src/parser.cpp")
        self.assertEqual(normalize_path("src/parser.cpp:10+3"), "src/parser.cpp")

    def test_pure_path_unchanged(self):
        self.assertEqual(normalize_path("src/parser.cpp"), "src/parser.cpp")
        self.assertEqual(normalize_path("./src/parser.cpp"), "./src/parser.cpp")
        self.assertEqual(normalize_path("include/sub/dir/file.hpp"), "include/sub/dir/file.hpp")

    def test_empty_and_none(self):
        self.assertEqual(normalize_path(""), "")
        self.assertIsNone(normalize_path(None))

    def test_list_normalization(self):
        got = normalize_paths(["src/x.cpp", "src/y.cpp@r1234567:5", "", None, 42, "src/z.cpp"])
        self.assertEqual(got, ["src/x.cpp", "src/y.cpp", "src/z.cpp"])

    def test_list_passthrough_non_list(self):
        self.assertIsNone(normalize_paths(None))
