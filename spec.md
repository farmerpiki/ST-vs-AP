# Eval spec: `apply_patch` vs span-edit tools

## Goal

Measure whether span/edit tools reduce token burn vs `apply_patch` for simple C++ code edits, while preserving correctness.

Primary comparison:

```txt
A. baseline: shared inspect tools + apply_patch as only write tool
B. span_tools: shared inspect tools + edit_batch(splice, move_span, replace_word) as only write tool
```

Out of scope for v1:

```txt
analysis/navigation helpers beyond shared read/search/diff
formatters or AST/symbol tooling
generic regex mutation
arbitrary shell writes
```

---

# 1. Experiment design

## Variants

### Variant A: `apply_patch`

Available tools:

```txt
status
rg
read_range
diff
run_check
apply_patch
```

Only mutation path:

```txt
apply_patch(patch)
```

### Variant B: `span_tools`

Available tools:

```txt
status
rg
read_range
diff
run_check
edit_batch
```

Only mutation path:

```txt
edit_batch(ops=[splice|move_span|replace_word])
```

## Fairness rule

Both variants get:

```txt
same model
same temperature
same user task
same fixture repo
same shared inspect tools
same verifier
same max turns/tool calls
same base system prompt
different edit-tool guidance only
```

No arbitrary `bash` write tool in either variant. Otherwise baseline can use `sed/perl/python`, which destroys the comparison.

---

# 2. Repo layout

```txt
eval/
  prompts/
    common.md
    apply_patch.md
    span_tools.md

  fixtures/
    tiny_cpp/
      base/
        CMakeLists.txt
        src/
        include/
        tests/

  tasks/
    001_set_line.yml
    002_insert_guard.yml
    003_delete_block.yml
    004_replace_block.yml
    005_move_helper.yml
    006_rename_identifier.yml
    007_multi_edit.yml

  runner/
    eval.py
    tools_shared.py
    tools_apply_patch.py
    tools_span.py
    token_count.py
    report.py

  runs/
    <timestamp>/
      config.yml
      results.jsonl
      transcripts/
      worktrees/
      report.md
      report.csv
```

---

# 3. Prompt structure

Build system prompt as:

```txt
prompts/common.md + "\n\n" + prompts/<variant>.md
```

Record:

```txt
system_prompt_path
system_prompt_sha256
system_prompt_tokens
tool_schema_tokens
```

## `prompts/common.md`

Canonical minified content:

```txt
role=cpp_dev(cwd-bound,task-driven); posture=min_diffs,read_before_write,run_check_done; no=unrel_refactor,new_deps,revert_user_work
final=do_task; "no change needed" only if satisfied+verified; <=3 lines: changed_files,verification
run_check: all_passed|pass=>done; tests_failed=>build_ok; inspect; may_preexist(tiny_cpp stats_test.cpp Median!=yours); build_error=>fix_compile/cmake; unknown=>empty_out/maybe_hung
tools: read_range|rg|diff|status paths=cwd-rel only(!abs); diff=>D# handle(not path); run_check=>O# handle; success hides log; verbose=1=>raw_log
```

## `prompts/apply_patch.md`

```txt
edit_tools: only_write=apply_patch; all_edits=apply_patch; hunks=min; ctx=safe_apply; !shell_write; 1 patch block/call exactly (*** Begin Patch..*** End Patch); after=diff_if_needed+verify
```

## `prompts/span_tools.md`

```txt
edit_tools: only_write=edit_batch; prefer_batch; coords=snapshot/pre-batch; result=unified_diff(@@=new_lines)+new_@rev(chain; no_re-read_verify); batch_ops:no_same-line_overlap; path@rev=edit_batch_only; read_tools=plain_cwd-rel_paths
addr: path:N=current line; path:N+C=C lines from N; path@rev:N=pinned; path@rev:EOF=append; S#|S#:N|S#:N+C=span whole|rel line|rel C lines; bare N/N+C invalid; path:N abs_line; S#:N span-rel_line
splice: C=0 insert@line(push old down); C>=1 replace C lines; edit_line=>C=1
move_span: needs from+to; to path:N insert before N; to path:N+C replace C lines; src_deleted
replace_word: id_rename_only; need expected.exact or expected.min count
avoid: apply_patch; full_unified_diff_as_edit; generic_regex_replace
```

---

# 4. Shared tool specs

## 4.1 `status`

Purpose:

```txt
dirty files; no content
```

Input:

```json
{}
```

Output:

```yaml
ok: true
cwd: "/tmp/eval/..."
dirty:
  - path: "src/parser.cpp"
    status: "modified"
```

Rules:

```txt
cwd-bound
no file content
include changed paths only
```

---

## 4.2 `rg`

Purpose:

```txt
low-context search
```

Input:

```json
{
  "pattern": "ParseName",
  "paths": ["src", "include"],
  "context": 0,
  "max_matches": 50,
  "case_sensitive": true,
  "word": false
}
```

Output:

```yaml
ok: true
hits:
  - id: H1
    path: src/parser.cpp
    line: 42
    text: "std::string ParseName(std::string_view input) {"
  - id: H2
    path: include/parser.hpp
    line: 18
    text: "std::string ParseName(std::string_view input);"
truncated: false
```

Rules:

```txt
default context=0
cap output
line previews only
no hidden full file content
```

---

## 4.3 `read_range`

Purpose:

```txt
read lines; create span S#
```

Input variants:

```json
{
  "path": "src/parser.cpp",
  "start": 35,
  "count": 40
}
```

```json
{
  "hit": "H1",
  "before": 8,
  "after": 20
}
```

Output:

```yaml
ok: true
span: S17
path: src/parser.cpp
rev: r8f31a2
range: 35+40
hash: h91cc0e
text: |
  35 static bool IsNameChar(char c) {
  36   return std::isalnum(static_cast<unsigned char>(c)) || c == '_';
  37 }
  ...
```

Rules:

```txt
line_base: 1
span rel lines: 1-based
max default count: 120
larger reads fail unless allow_large=true
span handle stores path, rev, start, count, hash
span becomes stale after file rev changes
```

---

## 4.4 `diff`

Purpose:

```txt
diff stat or hunks; pull-based
```

Input:

```json
{
  "mode": "stat",
  "paths": ["src/parser.cpp"]
}
```

or:

```json
{
  "mode": "hunks",
  "paths": ["src/parser.cpp"]
}
```

Output stat:

```yaml
ok: true
changed:
  - path: src/parser.cpp
    added: 3
    deleted: 1
diff_handle: D4
```

Output hunks:

```diff
ok: true
diff_handle: D5
diff: |
  @@ -41,3 +41,4 @@
   ...
```

Rules:

```txt
default stat
hunks capped
context fixed at 2 lines for hunk mode
```

---

## 4.5 `run_check`

Purpose:

```txt
run task-approved check
```

Input:

```json
{
  "name": "test"
}
```

Task config maps names to commands.

Output:

```yaml
ok: false
exit_code: 1
summary:
  errors: 1
  failed_tests: 1
diagnostics:
  - tests/parser_test.cpp:18: expected "invalid input"
output_handle: O9
```

Rules:

```txt
no arbitrary shell
max output lines
summarize failures
full log accessible only via optional read_output in v2
```

---

# 5. Baseline write tool

## 5.1 `apply_patch`

Input:

```json
{
  "patch": "*** Begin Patch\n*** Update File: src/parser.cpp\n@@\n-  return true;\n+  return false;\n*** End Patch"
}
```

Rules:

```txt
cwd-rel paths only
text only
no binary
no path outside cwd
no delete/create unless task allows
patch applies cleanly
compact summary
```

Output:

```yaml
ok: true
changed:
  - path: src/parser.cpp
    added: 1
    deleted: 1
    rev_old: r8f31a2
    rev_new: r72aa10
diff_handle: D6
```

Failure output:

```yaml
ok: false
error: patch_did_not_apply
details: "hunk failed at src/parser.cpp:42"
```

---

# 6. Span edit tool

## 6.1 Address syntax

Canonical:

```txt
path@rev:line
path@rev:start+count
path@rev:EOF
S17
S17:rel_line
S17:rel_start+count
```

Examples:

```txt
src/parser.cpp@r8f31a2:42
src/parser.cpp@r8f31a2:42+5
src/parser.cpp@r8f31a2:EOF
S17
S17:8
S17:8+3
```

Rules:

```txt
file lines 1-based
span rel lines 1-based
range=start+count
count=0 insert point
EOF append
rev required for path@rev
span addr checks rev/hash
stale rev/span fail
```

---

## 6.2 `edit_batch`

Input:

```json
{
  "atomic": true,
  "coords": "snapshot",
  "ops": []
}
```

Rules:

```txt
atomic=true
coords=snapshot
validate all before writes
coords=pre-batch snapshot
writes cwd-bound
overlap fails unless same move src
same-file move dst uses original snapshot
dst inside moved src fails
all-or-none
summary, not full diff
```

Output:

```yaml
ok: true
changed:
  - path: src/parser.cpp
    added: 3
    deleted: 8
    rev_old: r8f31a2
    rev_new: r72aa10
touched:
  - path: src/parser.cpp
    ranges:
      - 42+1
      - 80+3
diff_handle: D8
new_spans:
  - id: S22
    path: src/parser.cpp
    rev: r72aa10
    range: 42+1
```

Failure output:

```yaml
ok: false
error: stale_span
span: S17
path: src/parser.cpp
expected_rev: r8f31a2
actual_rev: r72aa10
```

---

## 6.4 Op: `splice`

Use:

```txt
insert/delete/replace line ranges
```

Inputs:

Insert before line:

```json
{
  "op": "splice",
  "at": "src/parser.cpp@r8f31a2:80+0",
  "text": "constexpr int kMaxRetries = 3;"
}
```

Delete:

```json
{
  "op": "splice",
  "at": "S17:10+6"
}
```

Replace:

```json
{
  "op": "splice",
  "at": "S17:10+6",
  "text": "return BuildFallback(config);"
}
```

Rules:

```txt
count > 0, text omitted => delete
count > 0, text provided => replace
count = 0, text provided => insert before start
count = 0, text omitted => fail
text may be multiline
line numbers in text forbidden
normalize newlines to \n
preserve final newline style where possible
```

---

## 6.5 Op: `move_span`

Use:

```txt
move existing code without restating moved body
```

Basic move:

```json
{
  "op": "move_span",
  "from": "S21",
  "to": "src/parser.cpp@r8f31a2:40"
}
```

Move to EOF:

```json
{
  "op": "move_span",
  "from": "src/parser.cpp@r8f31a2:180+27",
  "to": "src/helpers.cpp@r4aa991:EOF"
}
```

Replace destination range with moved body:

```json
{
  "op": "move_span",
  "from": "S31:5+12",
  "to": "src/parser.cpp@r8f31a2:60+4",
  "moved_indent_delta": -2
}
```

Rules:

```txt
source is deleted
to=path:N inserts before N
to=path:N+M replaces M destination lines with moved body
to=path:EOF appends
moved_indent_delta adjusts moved body indentation
tabs preserved unless delta requires space adjustment
destination line uses original snapshot coordinates
same-file destination inside source span fails
```

---

## 6.6 Op: `replace_word`

Use:

```txt
identifier-bound rename/refactor only
```

Input:

```json
{
  "op": "replace_word",
  "paths": ["src/parser.cpp", "include/parser.hpp"],
  "from": "ParseName",
  "to": "ParseIdentifier",
  "boundary": "cpp_identifier",
  "expected": {"exact": 7},
  "include": "code_and_comments"
}
```

Rules:

```txt
not generic regex
boundary cpp_identifier means:
  before is not [A-Za-z0-9_]
  after is not [A-Za-z0-9_]
expected.exact required unless dry_run=true
paths required
cwd-bound paths only
```

Modes:

```txt
include=code_only          # optional; v1 may approximate poorly
include=code_and_comments
include=all_text
```

For v1, safest implementation:

```txt
support include=all_text and code_and_comments as same behavior
document code_only as unsupported unless lexer exists
```

Dry run:

```json
{
  "op": "replace_word",
  "paths": ["src"],
  "from": "OldName",
  "to": "NewName",
  "boundary": "cpp_identifier",
  "dry_run": true
}
```

Dry-run output:

```yaml
ok: true
matches:
  - path: src/a.cpp
    count: 3
  - path: src/a.hpp
    count: 1
total: 4
```

Prompt should still prefer:

```txt
rg first -> expected exact -> replace_word
```

---

# 7. Task schema

Each task file:

```yaml
id: 001_set_line
fixture: tiny_cpp
user_prompt: |
  Change the default retry count from 3 to 5.

allowed_paths:
  - src/config.cpp
  - include/config.hpp

checks:
  - name: test
    cmd: "ctest --test-dir build --output-on-failure"

limits:
  max_turns: 12
  max_tool_calls: 40
  max_changed_files: 3

oracle:
  required_contains:
    - path: src/config.cpp
      text: "constexpr int kDefaultRetries = 5;"
  forbidden_contains:
    - path: src/config.cpp
      text: "constexpr int kDefaultRetries = 3;"
  optional_gold_patch: tasks/gold/001_set_line.patch
```

Verifier pass condition:

```txt
all checks pass
allowed_paths respected
required_contains satisfied
forbidden_contains absent
no unexpected file creation/deletion
optional exact diff used only as diagnostic unless strict=true
```

---

# 8. Suggested v1 task pack

Use small C++ fixtures. Each task should be solvable with 1–4 edits.

## 001: single-line replacement

Purpose:

```txt
set_line should beat patch hunk
```

Prompt:

```txt
Change the default retry count from 3 to 5.
```

Expected edit:

```txt
one line in src/config.cpp
```

Expected useful new-tool op:

```txt
set_line
```

---

## 002: insert guard

Purpose:

```txt
splice count=0 insertion
```

Prompt:

```txt
In ParseName, reject empty input by returning an empty optional before reading input[0].
```

Expected edit:

```txt
insert 2–3 lines near top of function
```

Expected useful new-tool op:

```txt
splice
```

---

## 003: delete dead block

Purpose:

```txt
delete without restating deleted lines
```

Prompt:

```txt
Remove the legacy fallback branch in NormalizePath. Do not change current behavior otherwise.
```

Expected edit:

```txt
delete 5–10 lines
```

Expected useful new-tool op:

```txt
splice without text
```

---

## 004: replace small block

Purpose:

```txt
replace span with shorter logic
```

Prompt:

```txt
Simplify ClampToByte so values below 0 return 0 and values above 255 return 255.
```

Expected edit:

```txt
replace small conditional block
```

Expected useful new-tool op:

```txt
splice with replacement text
```

---

## 005: move helper function

Purpose:

```txt
move_span should avoid delete+add duplicate body in patch
```

Prompt:

```txt
Move the static IsWhitespace helper above Tokenize so helpers are grouped before parser functions. Do not change behavior.
```

Expected edit:

```txt
move existing 10–25-line helper within same file
```

Expected useful new-tool op:

```txt
move_span
```

---

## 006: rename identifier

Purpose:

```txt
replace_word should beat multi-hunk patch
```

Prompt:

```txt
Rename the helper ParseName to ParseIdentifier in src/parser.cpp and include/parser.hpp.
```

Expected edit:

```txt
rename declaration, definition, call sites
```

Expected useful new-tool op:

```txt
rg -> replace_word expected exact
```

---

## 007: multi-edit small task

Purpose:

```txt
batching should reduce turns and context drift
```

Prompt:

```txt
Make FindUser return std::optional<User> instead of bool and update the one call site accordingly.
```

Expected edit:

```txt
include insertion + declaration line + definition line + call-site adjustment
```

Expected useful new-tool ops:

```txt
set_line + splice, same edit_batch
```

---

# 9. Runner algorithm

Pseudo-flow:

```python
for task in selected_tasks:
  for variant in ["apply_patch", "span_tools"]:
    for repeat in range(n_repeats):
      worktree = copy_fixture(task.fixture)
      init_git_commit(worktree)

      prompt = load(common.md) + load(f"{variant}.md")
      tools = shared_tools + variant_tools

      transcript = [
        {"role": "system", "content": prompt},
        {"role": "user", "content": task.user_prompt},
      ]

      state = ToolState(worktree, task)
      metrics = Metrics()

      while not done:
        response = call_model(
          model=model,
          messages=transcript,
          tools=tools,
          temperature=temperature,
          max_output_tokens=max_output_tokens,
        )

        record_usage(response.usage)
        append_assistant_message(transcript, response)

        if response.final_text and no_tool_calls:
          done = True
          break

        for tool_call in response.tool_calls:
          result = execute_tool(tool_call, state)
          record_tool_metrics(tool_call, result)
          append_tool_result(transcript, tool_call.id, result)

        if limits_exceeded:
          done = True
          mark_failure("limit_exceeded")

      verify = run_oracle(task, worktree)
      save_transcript()
      save_diff()
      write_result_jsonl()
```

Run order:

```txt
randomize variant order per task
run paired variants on fresh worktrees
store seed
store fixture commit hash
```

Recommended defaults:

```yaml
temperature: 0
n_repeats: 3
max_turns: 12
max_tool_calls: 40
max_output_tokens: 4096
```

---

# 10. Token accounting

## Primary metric

Use provider-reported usage per model call:

```txt
total_billed_tokens = sum(input_tokens + output_tokens)
total_input_tokens = sum(input_tokens)
total_output_tokens = sum(output_tokens)
peak_input_tokens = max(input_tokens)
turns = number of model calls
```

This captures actual repeated-context cost.

## Secondary attribution

Also estimate local token counts by serializing messages/tool args/tool outputs with the same tokenizer.

Track:

```yaml
system_prompt_tokens
tool_schema_tokens
user_prompt_tokens

assistant_text_tokens
assistant_tool_arg_tokens

write_tool_arg_tokens:
  apply_patch: tokens in patch arg
  span_tools: tokens in edit_batch args

tool_output_tokens:
  rg
  read_range
  diff
  run_check
  apply_patch
  edit_batch

read_context_tokens:
  rg output + read_range output

review_tokens:
  diff output

verification_tokens:
  run_check output
```

Important derived metrics:

```txt
working_tokens_excl_system =
  total_billed_tokens - turns * system_prompt_tokens

edit_arg_tokens =
  tokens in apply_patch.patch OR edit_batch.ops

context_burn =
  peak_input_tokens

efficiency_on_success =
  total_billed_tokens for successful runs only
```

Report both:

```txt
all-in token cost
working token cost excluding system prompt
edit-only argument tokens
peak context size
```

Reason:

```txt
new tools may save edit tokens but have larger schemas/prompts
all-in metric shows real cost
edit-only metric shows whether primitive design works
```

---

# 11. Correctness scoring

## Required fields per run

```yaml
task_id
variant
success: true|false
failure_reason
checks_passed: true|false
allowed_paths_ok: true|false
changed_files
diff_stat
final_diff_path
```

## Success

```txt
success = checks_passed
          AND allowed_paths_ok
          AND oracle_required_contains_ok
          AND oracle_forbidden_contains_ok
          AND no_forbidden_file_ops
```

## Failure reasons

Use one primary reason:

```txt
model_no_final
max_turns
max_tool_calls
invalid_tool_call
forbidden_path
stale_rev_or_span
patch_failed
tests_failed
oracle_failed
over_edit
timeout
tool_exception
```

Optional secondary tags:

```txt
wrong_line
missed_callsite
syntax_error
unrelated_refactor
invalid_rename_count
```

---

# 12. Comparison logic

## Paired comparison

For each `(task_id, repeat_seed)`:

```txt
baseline = apply_patch result
candidate = span_tools result
```

Classify:

```txt
both_success
baseline_only_success
span_only_success
both_fail
```

Token-savings only on `both_success` by default:

```txt
savings_pct = 100 * (baseline_tokens - span_tokens) / baseline_tokens
```

Also report penalized score:

```txt
if success:
  cost_to_success = total_billed_tokens
else:
  cost_to_success = max_task_budget_tokens * 2
```

This prevents a broken variant from looking cheap.

## Aggregate report

Per task:

```txt
success_rate_apply_patch
success_rate_span_tools
median_tokens_apply_patch
median_tokens_span_tools
median_savings_pct
median_peak_input_delta
median_write_arg_delta
median_turn_delta
median_tool_call_delta
```

Overall:

```txt
macro_avg_savings = mean(task medians)
micro_avg_savings = aggregate all successful paired runs
success_delta = span_success_rate - baseline_success_rate
```

Use median as primary. Mean can be skewed by one bad run.

Optional bootstrap CI:

```txt
bootstrap paired savings over task/repeat pairs
report p10/p50/p90
```

---

# 13. Output files

## `results.jsonl`

One line per run:

```json
{
  "run_id": "2026-06-14T12-00-00Z/001_set_line/span_tools/0",
  "task_id": "001_set_line",
  "variant": "span_tools",
  "model": "MODEL_NAME",
  "temperature": 0,
  "success": true,
  "failure_reason": null,
  "turns": 4,
  "tool_calls": 7,
  "total_billed_tokens": 8432,
  "total_input_tokens": 6900,
  "total_output_tokens": 1532,
  "peak_input_tokens": 3120,
  "system_prompt_tokens": 710,
  "tool_schema_tokens": 1450,
  "assistant_tool_arg_tokens": 210,
  "write_tool_arg_tokens": 58,
  "tool_output_tokens": {
    "rg": 180,
    "read_range": 920,
    "edit_batch": 80,
    "run_check": 140
  },
  "changed_files": ["src/parser.cpp"],
  "diff_stat": {"added": 2, "deleted": 1},
  "prompt_sha256": "...",
  "fixture_sha256": "..."
}
```

## `report.md`

Include:

```txt
config summary
success table
token table
paired savings table
per-task notes
failure breakdown
largest wins/losses
raw transcript links
```

Example table:

```markdown
| task | both success | apply_patch median | span median | savings | write arg delta | turns delta |
|---|---:|---:|---:|---:|---:|---:|
| 001_set_line | 3/3 | 7,900 | 7,420 | 6.1% | -82 | 0 |
| 005_move_helper | 3/3 | 13,800 | 8,900 | 35.5% | -1,900 | -1 |
```

---

# 14. Tool implementation details

## File revision

Use content hash:

```txt
rev = "r" + sha1(file_bytes)[:7]
```

Each span stores:

```yaml
id: S17
path: src/parser.cpp
rev: r8f31a2
start: 35
count: 40
span_hash: sha1(join(lines[35:75]))[:8]
```

Mutation invalidates spans for changed files.

V1 stale behavior:

```txt
fail on stale rev/span
no auto-rebase
```

Optional v2:

```txt
rebase if exact span_hash occurs once
```

---

## `edit_batch` validation order

```txt
1. parse all ops
2. resolve addresses/spans
3. check cwd-bound paths
4. check current revs
5. expand replace_word
6. validate expected counts
7. detect overlapping edits
8. validate move destinations
9. build modified file contents in memory
10. write all files
11. compute revs and diff summary
```

No partial writes.

---

## Overlap rules

Fail when:

```txt
two set_line/splice ops touch same original line
splice overlaps move source
move source overlaps another move source
replace_word touches range also edited explicitly
destination inside source span for same-file move
```

Allowed:

```txt
multiple insertions at same line only if op order is explicit
```

If multiple inserts at same original line:

```txt
apply in given op order
```

---

## Newline handling

```txt
read files as UTF-8 text
preserve LF
set_line text cannot contain newline
splice text may contain newline
trailing newline in text does not create extra blank line unless explicit blank line exists
preserve final newline if original file had one
```

---

# 15. Controls to avoid benchmark artifacts

## Do not reveal variant

User prompt should not mention:

```txt
token savings
benchmark
span tools
apply_patch
```

System prompt may mention tool preferences because tools differ.

## Disable hidden mutation paths

No tools that can write except:

```txt
apply_patch
edit_batch
```

So avoid:

```txt
bash
python_exec
sed
perl
tee
cat > file
```

## Tool output caps

Set hard caps:

```yaml
rg_max_matches: 80
read_range_default_max: 120
diff_hunk_max_lines: 200
run_check_max_lines: 120
```

If capped:

```txt
tool must say truncated=true
```

## Same inspect behavior

Do not make `read_range` better for span variant only. It should return spans in both variants, but baseline prompt/tool set should not include span edit ops. The baseline may ignore span handles.

This isolates editing primitive effect.

---

# 16. CLI spec

```bash
python -m eval.runner.eval \
  --model "$MODEL" \
  --tasks eval/tasks/*.yml \
  --variants apply_patch,span_tools \
  --repeats 3 \
  --temperature 0 \
  --out eval/runs/$(date +%Y%m%d_%H%M%S)
```

Options:

```txt
--model
--variants
--tasks
--repeats
--temperature
--max-turns
--max-tool-calls
--keep-worktrees
--strict-gold-diff
--seed
--out
```

---

# 17. Acceptance criteria for eval script v1

The eval is usable when it can:

```txt
load prompts from repo files
run same task against both variants
execute tool calls against isolated worktree
enforce only allowed write tool per variant
record provider token usage
record local attribution token estimates
verify final repo correctness
save transcript + final diff
produce paired token-savings report
```

Minimum useful v1 run:

```txt
6 tasks
2 variants
3 repeats
36 total runs
```

Interpretation threshold:

```txt
span_tools is promising if:
  success rate >= apply_patch success rate - 5 percentage points
  median all-in tokens lower on both-success pairs
  median write_tool_arg_tokens materially lower
  biggest savings appear on delete/move/rename tasks
```

Do not conclude from edit-token savings alone. Tool-schema/system-prompt overhead must be included in all-in totals.
