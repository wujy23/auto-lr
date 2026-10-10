# Lightroom harness repository

## Photo operations

This project is an executable Python workflow. When the user asks to edit a photo,
use `.agents/skills/lightroom-edit/SKILL.md`. For installation, updates, or connection
problems, use `.agents/skills/lightroom-setup/SKILL.md`.

The outer chat is an operator: collect file paths and the user's visual request,
invoke `python3 -B harness.py run`, wait for the process, and present saved results.
Goal generation proceeds directly to parameter planning, editing, review, and export;
there is no human confirmation step. Use the repository's absolute path
when the chat working directory differs.

Do not perform the planner or reviewer roles yourself. Do not read the internal
`skills/` recipes to emulate them, generate goal/parameter/review JSON, call
`lr_link.py` directly, write bridge queue files, use GUI sliders, or call `main.py`
directly for a photo request. The public entry point owns dispatch to `main.py`;
Python owns all internal model calls, writes, readback, correction, and export.

Do not alter source code, Skill snapshots, goals, digests, or state files to make a
photo request succeed. Report failures and use setup diagnostics. A user explicitly
requesting software development may authorize source edits and offline tests; this
does not authorize editing real photos as a test.

`run` executes the full workflow without confirmation. `plan` is optional planning-only
mode; `continue --run-dir ...` executes a saved unexecuted goal without an approval flag.
Keep goal, input, baseline, and photo identity checks. Never replay writes or resume a run
other than `goal_ready` (or legacy `awaiting_confirmation`). Do not start concurrent
photo tasks on one connection. Batch experiments use independent RAW copies.

## Execution and evidence

Run in a local environment on the Mac running Lightroom Classic. Python 3.10+ and
a logged-in Codex CLI are required. A cloud checkout alone cannot access local LR.
Attachments must resolve to readable local files; obtain the original RAW path if
the attachment is only a preview.

Keep long-running command sessions alive and poll their existing process session.
A tool returning a session ID is not a failure. Do not restart the command because
it has produced no output. Use `harness.py status --run-dir ...` for progress.
If the execution host denies necessary network/file access, use its normal scoped
approval mechanism. Do not weaken the inner CLI sandbox or copy login credentials.

Execution claims require a public-entry invocation record and actual run artifacts.
`work/harness-invocations.jsonl` records dispatch, engine hash, run directory, and
exit status. `state.json`, agent outputs, review, and `export.json` establish the
result. A receipt alone does not prove successful editing or prevent arbitrary
shell access from bypassing the interface.

## Development checks

Run `python3 -B -m unittest discover -s tests -v` after behavior changes.
Do not commit user photos, credentials, outputs, runs, or installation-specific paths.
Do not update code or the plug-in while a photo workflow is active.

In Markdown files use `$...$` and `$$...$$` for math, and verify no backslash-parenthesis
or backslash-square-bracket math delimiters remain after editing math documents.
