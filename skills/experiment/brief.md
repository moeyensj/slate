# Brief: run one slate experiment

Record: `<RECORD_PATH>` (experiment id `<EXP_ID>`)
What to run: <WHAT_TO_RUN>
Inputs and configuration: <INPUTS>
Detach: <yes|no>
Working directory for the code under test: <CODE_DIR>

The `slate` command is at `<SLATE_BIN>`.

## Steps

1. Read the record's `README.md`. The plan sections (Question through
   Method) are fixed. Do not edit them. If the plan cannot be run as
   written, stop and report.
2. Write `run.sh` in the record: the exact commands, re-runnable, starting
   with `set -euo pipefail`. It runs with the record directory as its
   working directory. Everything the run does must be in this file,
   including any build: a step done by hand is a step nobody can repeat.
   Refer to code only as `"$SLATE_REPOS_ROOT/<repo>/..."` and write large
   outputs only under `"$SLATE_OUT"`. Never write the absolute path of a
   repository into `run.sh`: a later rerun points `SLATE_REPOS_ROOT` at a
   reconstruction of the recorded commits, and a hard-coded path would
   silently run today's code instead.
3. Write `inputs.txt`: every file or directory the run reads, one absolute
   path or URI per line: datasets, configuration files, kernels, and any
   prebuilt library or executable the run loads that `run.sh` does not build. A
   registered dataset is written `dataset:<slug>` (`slate data list`). NEVER copy
   an input into the record; `slate` records sizes and hashes from where the
   files are. For a comparison of two arms, prefix each arm's files `a:` and
   `b:`. A run with no inputs has the single word `none`.
4. Write `outputs.txt`: every output the run must produce, one path per
   line. Small result files go in `results/` (relative to the record);
   large ones go under `$SLATE_OUT/` and are listed with that prefix.
5. `slate exp check <EXP_ID>`. Fix what it fails on. Report, do not fix,
   what it only reports: dirty repositories, volatile paths, arm-parity rows
   that Method does not account for, and every reason behind a
   `reconstructable: no` verdict.
6. `slate exp start <EXP_ID>` (add `--detach` when Detach is yes).
   Without `--detach`, start it as a single background command and wait for
   its completion notice. Never poll, never sleep in a loop, never tail the
   log while it runs.
7. Detached: stop here and report the pid and the log path.
   Otherwise: read `outcome.json`. If the status is `failed` or `lost`,
   report the exit code and the last 20 log lines; do not retry with
   different settings, because that would be a different experiment.
8. Write the **Result** section of the README, replacing its marker: facts
   only. Numbers with units and their measurement context (host, build,
   configuration), a small table when it helps, paths to the outputs.
   Include the controls' results. No interpretation, no adjectives, no
   recommendation. Leave Interpretation and Conclusion alone.

## Report (one page)

- Status (`done`, `failed`, `lost`, or `running` with pid), exit code, wall time.
- The headline numbers, as they appear in Result.
- The reconstructability verdict and its reasons, verbatim.
- Everything else `slate exp check` reported, verbatim.
- Anything that differs from the plan, however small.
- Paths only, never file contents.
