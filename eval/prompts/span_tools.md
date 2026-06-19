edit_tools:
  only_write_tool: edit_batch
  coords: snapshot (pre-batch line numbers)
  prefer_batch: true
  result: compacted unified diff (@@ header is `-old_start,old_count +new_start,new_count`) + new @rev (trust it, chain from it, verify output expected; no re-read to verify)
  rules: ops in one batch must not overlap the same line. the path@rev form addresses edit_batch only — read tools take plain cwd-relative paths.

  # Address forms (path-based):
  #   +count is mandatory for path edit addresses.
  #   path:line+count      e.g. src/parser.cpp:48+3         (3 lines from 48)
  #   path@rev:EOF                                       (append at end of pinned rev)
  # Span forms (from a prior read_range/rg result):
  #   S17, S17:N, S17:N+M  (whole span / 1-based line / line+count within span, limited to span range)
  # Bare line numbers ("48", "48+3") are NOT accepted.
  # NB: in path:N the N is an ABSOLUTE file line; in S17:N the N is RELATIVE
  #     to the span (1-based). Same ':N', different base.

  splice:       count=0 = INSERT only; count=N>=1 = REPLACE/DELETE. To change or cut one line use count=1, not 0.
  move_span:    move code. Needs from AND to. The destination address is exact:
                 to: path:N     = insert the body at line N (existing line N is pushed down)
                 to: path:N+M   = replace the M-line span starting at N with the body.
                 Source is always deleted.
  replace_word: identifier rename only. Needs expected.exact or expected.min count.
  verify:       set verify=true to run the build (run_check) right after a SUCCESSFUL edit, in the SAME turn (saves a separate run_check turn). Use it only when you want a build pass now; leave it false (default) while you still have more edits to make. It does nothing on a failed edit.

avoid: apply_patch, full unified diff as edit, generic regex replace
prefer:
  - replace_word for identifier changes
  - move_span for refactoring code blocks
  - splice count=1 for single line edits, count=0 for insert
