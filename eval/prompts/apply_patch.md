edit_tools:
  only_write_tool: apply_patch
  use_apply_patch_for_all_edits: true
  keep_hunks_minimal: true
  include_enough_context_to_apply_safely: true
  do_not_use_shell_to_modify_files: true
  one_block_per_call: true  # exactly one *** Begin Patch / *** End Patch block per call
  after_patch: inspect_diff_if_needed_then_verify
  verify: set verify=true to run the build (run_check) right after the patch applies, in the SAME turn (saves a separate run_check turn). Use only when you want a build pass now; leave false (default) while more edits remain. No effect if the patch fails.
reponse compact diff + added/removed counts
