# The `slate` command line

`bin/slate` does every piece of bookkeeping that does not need judgment:
finding the knowledge base, allocating ids, snapshotting repository state,
starting and harvesting runs, generating the mechanical sections of a
handoff, checking a handoff against reality, and keeping the decision queue.
The skills call it; a person can call it too.

Python 3.9 or later, standard library only. Output is a compact plain-text
table or a few lines; `--json` on any reading command prints JSON instead.
Exit status: `0` success, `1` a check failed (the output says which), `2`
usage or configuration error.

## Finding the knowledge base

In order: the `--kb PATH` flag; the `SLATE_KB` environment variable; then a
walk upward from the working directory, at each level looking for
`./slate.toml` and then for `./*/slate.toml` (one level of children, hidden
directories skipped). Two matches at the same level is an error that lists
both. No knowledge base found: `slate status` prints nothing and exits `0`;
every other command exits `2` with a one-line message naming `slate init`.

## `slate.toml`

Lives at the knowledge base root. Every key is optional.

```toml
[kb]
name = "notes"              # default: the directory name
arcs = "arcs"              # arc folders live here, relative to the KB root
readme_tree = true         # promote annotates the README directory tree

[repos]
root = ".."                # relative to the KB root
include = ["*"]            # "*" = every git working tree directly under root
                           # (bare repositories are skipped), the KB itself
                           # included; or a list of names

[provenance]
tools = ["python3 --version"]   # each command's first output line is recorded
env = []                   # environment variables whose values are recorded
binaries = []              # executables the runs use: path, size, sha256 recorded
lockfiles = []             # globs relative to repos.root, e.g. "*/Cargo.lock": sha256 recorded

[tracker]
kind = "auto"              # auto | bd | none
dir = ".."                 # where tracker commands run, relative to the KB root

[agent]
experimenter = "slate:experimenter"   # subagent type that runs experiments

[commit]
policy = "ask"             # ask | auto | never  (read by the skills)
record_subject = "Record {title}"
correct_subject = "Correct {title}"

[experiment]
preflight = []             # commands that must exit 0 before a run starts
volatile = ["/tmp", "/private/tmp", "/var/tmp"]
max_hash_mb = 4096         # files larger than this are sized, not hashed
preserve_kb = 256          # an input that git cannot recover is kept in the
                           # record when it is no larger than this; 0 = never

[handoff]
max_lines = 150
landed_cap = 15            # commit subjects listed per repository
claim_hours = 12           # a pickup newer than this warns a second pickup
```

`tracker.kind = "auto"` resolves to `bd` when `bd` is on `PATH` and a
`.beads` directory exists in `tracker.dir`, otherwise to `none`. When
`tomllib` is missing (Python < 3.11) a small built-in parser reads the
subset above: tables, strings, booleans, integers, arrays of strings,
comments.

## Records

Frontmatter is a flat block of `key: value` lines between `---` markers.
Values are plain strings; surrounding quotes are stripped; an empty value is
the empty string. Lists are comma-separated strings. Updating a key rewrites
that line only and leaves the body byte-identical.

Two HTML comment markers flag text that a person or model still has to
write: `<!-- slate:fill ... -->` (before a run, or before a handoff is
done) and `<!-- slate:after ... -->` (after a run).

```
<kb>/<arcs>/<arc>/
  README.md                      slate: arc
  rulings.md                     the decision queue and the rulings
  handoffs/<date>[-n].md         slate: handoff
  experiments/<date>-<slug>/
    README.md                    slate: experiment  (plan, then result)
    run.sh                       the exact command, re-runnable
    inputs.txt                   declared inputs, by reference: one path, directory or URI per line
    outputs.txt                  declared outputs, one path, glob or URI per line
    preserved/                   small inputs that git could not recover (see Inputs)
    dirty/                       per repository: uncommitted changes as a patch
    provenance.json              written by `exp start`
    run.json                     pid, host, log path, start time
    outcome.json                 written when the run exits
    log.txt                      stdout and stderr of run.sh
    results/                     small result files
<kb>/.slate-private/<arc>/handoffs/   private handoffs, ignored by git
<kb>/.slate-private/hash-cache.json   sha256 by path, size and mtime
```

Ids: an arc is its slug; an experiment is `<arc>/<date>-<slug>`; a handoff
is `<arc>/<date>` with `-2`, `-3` appended for later notes on the same day;
a decision is `<arc>/D-001`. Every command that takes an id also accepts a
unique suffix of it.

### `rulings.md` entries

```markdown
## D-001

- status: open
- blocking: yes
- asked: 2026-09-18
- tracker: proj-abc12
- ruled:

**Question.** Where do handoffs live?

**Ruling.**

> The owner's words, verbatim.
```

`status` is `open` or `ruled`. `ruled:` holds `<date> by <name>`. The
`**Ruling.**` block is absent until the entry is ruled.

## Commands

### Setup

`slate init [--kb PATH] [--arcs DIR] [--tracker auto|bd|none] [--repos-root PATH]`
writes `slate.toml` (never overwrites one), creates the arcs directory with
a `README.md` that holds an empty `<!-- slate:index -->` block, adds
`.slate-private/` to the knowledge base's `.gitignore`, and prints what it
resolved, the tracker included.

`slate where` prints the knowledge base root, the arcs directory, the
resolved tracker, the repositories that a snapshot covers, the agent type
that runs experiments, and the commit policy with its two subject patterns.

`slate status` prints at most one line for the session-start hook, and
nothing when there is nothing to say:
`slate: 3 open handoffs (oldest 6d) · 1 run finished, not harvested · 2 decisions waiting on the owner — /slate:pickup`.
Only the non-zero segments are printed. The closing hint names the skill for
the first segment present: `/slate:pickup` for handoffs, `/slate:harvest`
for finished runs, `/slate:decisions` for decisions. It reads files only: no
tracker calls, no git calls.

### Arcs

`slate arc new <slug> --title T --directive TEXT [--by NAME] [--epic ID]`
creates the arc folder from `templates/arc.md` and `templates/rulings.md`.
With the `bd` tracker and no `--epic`, it creates an epic
(`bd create --type epic --silent`) and stores its id in `tracker:`.

`slate arc list` prints one row per arc: id, status, open handoffs, running
experiments, open decisions. `slate arc show <arc>` prints the arc's
timeline: experiments, handoffs and rulings in date order, one line each.
`slate arc close <arc>` sets `status: closed` and refuses while the arc has
an open handoff or a running experiment.

`slate index` rewrites the `<!-- slate:index -->` block in every arc
README (that arc's experiments and handoffs) and in the arcs README (the
arcs table). Nothing outside the markers changes.

### Snapshot

`slate snapshot` reports, for each covered repository: name, branch, HEAD
sha, dirty file count, commits ahead of and behind upstream (blank when
there is no upstream), and `pushed`: whether HEAD is contained in a
remote-tracking branch (no network access; `no` when there is no remote).
Then host, user, UTC time, hardware (CPU model, core count, memory, OS), the
`provenance.tools` lines, the `provenance.env` values, and size and sha256
of each `provenance.binaries` and `provenance.lockfiles` match. For the
knowledge base repository the dirty count ignores slate's own paths (the
arcs directory and `.slate-private/`), so writing a record does not make
every later pickup report `dirty changed`.

### Experiments

`slate exp new <arc> <slug> --title T [--serves TEXT] [--hypothesis TEXT]`
creates the record from `templates/experiment.md` with an empty `run.sh`
(executable), `inputs.txt`, `outputs.txt` and `results/`. `--hypothesis` is
the claim under test in one sentence; it is stored in the frontmatter, where
`slate findings` reads it. `--serves` defaults to the arc's
directive. With the `bd` tracker it creates a child task of the arc's epic,
labelled `slate-experiment`, and stores the id. Prints the record path.

`slate exp check <id>` is the gate before a run. It fails when: a
`slate:fill` marker remains; the frontmatter `hypothesis:` is empty;
`run.sh` is empty; `outputs.txt` is empty; `inputs.txt` is empty (a run
with no inputs says so with the single word `none`); a declared input does
not exist; a preflight command exits non-zero. It reports without failing:
each dirty repository; each declared input or output under a `volatile`
prefix; the arm parity table; and the reconstructability verdict.

**Inputs are referenced, never copied.** Each line of `inputs.txt` is a
file, a directory or a URI, optionally prefixed `a:` or `b:` to assign it to
an arm. For every input the check records, in `provenance.json`: the
resolved absolute path, size, mtime and sha256 (a directory: a per-file list
and a tree hash over the sorted `relpath` and `sha256` pairs; a URI: a
pointer, unverified). Hashes are cached in `.slate-private/hash-cache.json`
by path, size and mtime so a large dataset is hashed once. A file over
`max_hash_mb` is recorded by size and mtime only and marked `unhashed`. An
input that sits inside a covered repository, tracked and unmodified, is
marked `recoverable: git` with its repository, path and blob sha. An input
that git cannot recover (untracked, modified, or outside every repository)
and is no larger than `preserve_kb` is copied to `preserved/`, because it
would otherwise be lost; a larger one is not copied and counts against the
verdict.

**Dirty repositories.** For each, `dirty/<repo>.patch` holds
`git diff HEAD --binary` plus every untracked, unignored file no larger than
`preserve_kb` (as a no-index diff, so the patch recreates it); larger
untracked files are listed with size and sha256 and count against the
verdict. The patch is capped at 1 MB; over the cap it is not written and
counts against the verdict.

**Arm parity.** When inputs carry `a:` and `b:` prefixes, files are paired
by basename, then by order. The table lists every file present in one arm
only, and for each pair every differing key (TOML and JSON compared by
flattened key, anything else line by line). The record's Method section must
account for every row; the command only prints them.

**Reconstructability verdict.** `reconstructable: yes` or `no`, with one
reason per line, printed and written to `provenance.json` (the check writes
the inputs and the verdict; `exp start` adds the snapshot to the same file).
Reasons: a covered repository's HEAD is not pushed (the knowledge base
itself is exempt: it holds the record, not the code under test);
uncommitted changes that were not fully preserved; an input with no hash
that is neither recoverable from git nor preserved; an input or output
under a volatile prefix. A hashed input is identified, not stored: repeating
the run still needs the file to exist where the record says. `no` does not
fail the check: the run may be worth making anyway, and the record says
what it would take to repeat it.

`slate exp start <id> [--detach]` runs the check, writes `provenance.json`
(the snapshot, plus `plan_commit`: the knowledge base commit that last
touched the record's README, or `null` when the plan is uncommitted or
modified since, in which case it prints
`plan is not committed: the prediction is not provably prior to the run`),
sets `status: running` and `started:`, records the sha256 of `run.sh` and
the working directory, and runs `run.sh` from the record directory with
stdout and stderr to `log.txt`. A supervisor process in its
own session outlives the caller, waits for `run.sh`, and writes
`outcome.json` (`exit_code`, `finished`, `wall_seconds`). Without `--detach`
the command waits and then harvests; with it, it returns at once and prints
the pid and the log path.

`slate exp harvest [<id>]` settles every running experiment, or one. For
each: if `outcome.json` exists, verify the declared outputs (exists,
non-empty, modified after `started`, size, sha256 unless over
`max_hash_mb`; URIs are recorded as pointers and not verified), write the
results into `outcome.json`, re-check every declared input against its
recorded size and mtime (rehashing when they differ) and list any that
changed under `inputs_mutated`, which adds a reason to the verdict, and set
`status` to `done` (exit 0 and every output verified) or `failed`; if there is no outcome and the pid is dead,
set `status: lost`; otherwise report it as running with elapsed time and the
last three log lines. Prints one row per experiment.

`slate exp conclude <id> --outcome confirmed|refuted|null|inconclusive --conclusion TEXT`
requires `status: done` or `failed` and no remaining `slate:after` marker.
`--conclusion` is one sentence, at most 300 characters, that can be quoted
without the record beside it. It sets `outcome:` and `conclusion:`, and
closes the tracker task with the outcome as the reason.
`slate exp abandon <id> --reason TEXT` sets `status: abandoned` and
`abandoned: <timestamp> <reason>`.

### Files

`slate exp files [<id>] [--arc ARC] [--verify]` lists what each experiment
holds on disk, one row per declared input and output: experiment, role,
path, size, state (`present`, `changed` when size or mtime differ from the
record, `missing`, `remote`), the number of other experiments that declare
the same path, and the mark. `--verify` rehashes instead of trusting size
and mtime. A total per experiment, inputs and outputs apart, closes each
group.

`slate exp mark <id> --outputs|--inputs|--all --reason TEXT` marks an
experiment's files for deletion: `marked: <date> <scope> <reason>` in the
frontmatter. Nothing is deleted. It warns when the experiment is promoted
or cited in a findings file, because its outputs are then evidence for a
note. `slate exp unmark <id>` clears the mark.

`slate exp list [--arc ARC] [--status S]` prints id, status, outcome, title.

### Handoffs

`slate handoff new <arc> [--private] [--session ID]` writes a note from
`templates/handoff.md`. It generates: the directive (from the arc); Running
now (experiments with `status: running`: id, host, pid, elapsed, log path;
"Nothing." when none); Landed since the last handoff (per repository, commit
subjects between the sha recorded in the arc's previous handoff and HEAD,
capped, or "First handoff for this arc."); Decisions (open entries with
their blocking flag, then the five most recent rulings, one line each,
under the words "Ruled, do not relitigate"); Tracker (with `bd`: in-progress
and open issues under the arc's epic, capped at 15; otherwise "No
tracker."); and the `slate-state` JSON block (the snapshot). It sets
`supersedes:` to the arc's newest open handoff and closes that note
(`status: closed`, `closed: <timestamp> superseded`). Prints the path.

`slate handoff check <id>` fails while a `slate:fill` marker remains or the
note exceeds `handoff.max_lines`.

`slate handoffs [--all]` lists open handoffs (or all), oldest first: id,
age, status, and the first line of First action, truncated.

`slate pickup <id> [--dry] [--session ID]` verifies the note's
`slate-state` against the present, one row per repository: `same`;
`advanced N` (recorded sha is an ancestor of HEAD); `diverged`; `branch
gone`; `branch merged` (recorded sha is reachable from the default branch);
`missing`; and `dirty changed` when the dirty count differs. It lists
tracker issues named in the note that have closed since, harvests the arc's
running experiments, and warns when the note was picked up less than
`claim_hours` ago by another session. Unless `--dry`, it sets
`status: picked-up` and `picked_up: <timestamp> <host> <session>`. The last
line printed is the note's path. Exit `1` when any repository is `diverged`,
`branch gone` or `missing`.

`slate handoff close <id> [--reason TEXT]` closes a note.

### Decisions

`slate decision ask <arc> [--blocking] TEXT` appends an entry and prints its
id. With `bd` it also creates an issue of type `decision` under the arc's
epic and stores the id.

`slate decision rule <id> --by NAME TEXT` records the ruling verbatim with
today's date, sets `status: ruled`, and closes the tracker issue with the
ruling as the reason.

`slate decisions [--arc ARC] [--all]` prints the queue, blocking first and
oldest first: id, blocking, age, question truncated.

### Findings

`slate findings [--arc ARC]` prints one row per concluded experiment: id,
outcome, hypothesis, conclusion, read from frontmatter only.

A findings file is a Markdown summary of what a set of experiments showed.
It cites experiments as `[exp:<full id>]`. A `FINDINGS.md` inside an arc
folder covers that arc; anywhere else in the knowledge base it covers every
arc. `slate findings check <path>` fails when a citation does not resolve to
a concluded experiment, or when a concluded experiment in scope is neither
cited nor listed under a `## Not yet synthesized` heading. It warns, without
failing, on a citation whose experiment is `inconclusive`, has a non-empty
Corrections section (anything but `None.`), has aged evidence (`slate stale`
counts commits against it), or is marked for deletion.

### Knowledge

`slate promote <exp-id> --to PATH --purpose TEXT` runs after the note at
`PATH` (relative to the knowledge base) has been written. It fails when the
note does not mention the experiment id or has no `As of <date>` stamp.
It sets the experiment's `promoted_to:` (comma-appending), and when
`readme_tree` is on and the knowledge base README has a fenced directory
tree that lists the note's directory, inserts `<file>  # <purpose>` after
that directory's last entry, matching the tree's indentation; when it
cannot place the line it says so and prints the line to add by hand.

`slate stale` reads every experiment with `promoted_to:` and `measures:`
(comma-separated paths of the form `<repo>/<path>`), and for each counts the
commits that touched the path since the sha recorded in `provenance.json`.
Prints note, experiment, path, commit count and the age of the evidence,
highest count first. Exit `0` always.

## The tracker adapter

One small interface with two implementations, `bd` and `none`:
`create_epic`, `create_child(kind, title, parent)`, `close(id, reason)`,
`open_under(parent)`, `closed_among(ids)`. The `bd` adapter shells out with
`--json` or `--silent` and runs in `tracker.dir`. A tracker failure never
loses a record: the command finishes its file work, prints the tracker
error on one line, and exits `0`.
