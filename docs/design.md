# Design

*As of 2026-09-18.*

## The problem

Work done with an agent happens in sessions, and a session ends. Two things
are lost at that boundary unless something keeps them.

**Evidence.** A measurement is made, a conclusion reaches a note, and the
configuration, the commits and the raw numbers behind it stay in a scratch
directory that is later wiped. Months on, nobody can say what the number
measured or whether it still holds.

**State and decisions.** What was verified, what is still running, what the
owner decided and what still waits on them live in the conversation. Notes
kept to bridge sessions tend to become a single file edited in place: it
grows without bound, nothing in it ever closes, and paragraphs begin to
supersede the paragraphs below them.

slate gives each of these a record with a lifecycle, inside the project's
knowledge base, and gives the bookkeeping to a script.

## Principles

**A protocol over files.** Records are Markdown and JSON in the knowledge
base's git repository. They are readable without slate, versioned, and
carried between machines by a push. There is no server and no database.

**Scripts do bookkeeping; the model does judgment.** Ids, timestamps,
repository state, exit codes, output checks, the mechanical sections of a
handoff and the decision queue are written by `bin/slate`. The model writes
the question, the prediction, the interpretation, the first action. A field
a script can fill is never left to the model, because a model fills it
plausibly.

**A record must be able to fail.** A check that cannot fail proves
nothing. The plan is committed before the run, so the prediction is provably
prior. The plan names controls: a case that must pass and a case that must
fail. `slate exp check` refuses an unfinished plan. The outcome is `done`
only when the exit code is zero and every declared output exists, is
non-empty and is newer than the start of the run. When something could not
be verified the record says so.

**A run can be reconstructed, or the record says why not.** Everything a
repeat would need is captured by script at the moment of the run: the
commit of every covered repository and whether that commit is pushed,
uncommitted changes as a patch that recreates untracked files too, the run
script and its hash, the hash of every declared input and output, the
executables, the lockfiles, the environment variables that matter, the
machine. Inputs are referenced by path and hash and never copied: datasets
are large, shared, and do not belong in a knowledge base. Only a small input
that git could not recover is preserved in the record, because it would
otherwise be lost. Each record carries a verdict, `reconstructable: yes` or
`no`, with reasons such as an unpushed commit, an unhashed dataset or a
volatile path. The verdict never blocks a run; it states what repeating the
run would take.

**Fact and inference apart.** The agent that runs an experiment writes the
Result: numbers, context, paths. Interpretation is written afterwards, by
the lead session with the owner, in its own section. Run facts are never
rewritten; interpretation is corrected in place, dated.

**The owner's words, verbatim.** An arc opens with the owner's directive
as they said it. An experiment names the directive it serves, and one that
serves none is a side quest that has to be asked about. Rulings are quoted
verbatim with a date and carried forward in every handoff under "do not
relitigate". This is aimed at one failure: a session in which every
reasonable-looking measurement spawns the next investigation until the
owner's actual request is three days behind.

**Near-zero idle cost.** Nothing is loaded into context except one line at
session start, printed only when a handoff is open, a run has finished
unharvested, or a decision is waiting. Skill descriptions are one sentence.
Commands print tables, not files.

## The arc

The unit of organisation is the arc: a campaign, a feature, a release
step. One folder holds its directive, its state, its experiments, its
handoffs and its rulings, because that is how the working record is read:
by line of work, not by record type. Where arcs live is configurable, so a
knowledge base that already keeps per-campaign folders keeps them.

## Lifecycles

An experiment is `planned`, then `running`, then `done`, `failed` or
`lost`, or `abandoned` at any point. `lost` means the process died without
writing an outcome, for instance across a reboot. A settled experiment is
concluded with an outcome: `confirmed`, `refuted`, `null` or
`inconclusive`. A null result is a full result.

Runs outlive sessions. A detached run is supervised by a process in its own
session that records the exit code when the run ends. Nothing polls. A later
`harvest`, or the next `pickup`, verifies the outputs and settles the record.

A handoff is `open`, then `picked-up`, then `closed`. A note is written
once. The next note for the same arc supersedes it and closes it, so an arc
has at most one open note and the chain of notes is the arc's history.
Pickup verifies before it trusts: each repository is reported as the same,
advanced, diverged, merged or gone relative to the note.

A decision is `open`, blocking or not, then `ruled`. Non-blocking questions
wait until the owner asks for the queue.

## Findings

Every experiment carries a one-sentence hypothesis, written before the run,
and a one-sentence conclusion, written after it, both in its frontmatter. A
table of them costs almost nothing to print. A findings file reads across
experiments: one section per question, every claim cited as `[exp:<id>]`,
disagreeing experiments side by side, null and refuted results at full
weight. `slate findings check` fails on a citation that does not resolve
and on a concluded experiment that is neither cited nor listed as not yet
synthesized, and warns where a cited experiment is inconclusive, corrected,
aged or marked for deletion.

## Files and letting go

Because data is referenced, not copied, the record can outlive the data.
`slate exp files` shows what each experiment holds on disk and whether it
still matches its recorded hashes. An experiment nobody needs any more is
marked for deletion, with a reason; marking deletes nothing and is visible
to the next reader. The record, its hashes and its conclusion always stay.

## Promotion and staleness

Arc folders hold the working record; the rest of the knowledge base holds
what stays true. `promote` writes a note under the knowledge base's own
rules, offered to the owner before it is committed, citing the experiment
it rests on. The experiment records which code paths it measured, so
`slate stale` can count the commits that have touched them since: a note
whose evidence has aged is due for a re-run, not for blind trust.

## Trackers

The knowledge base is the record; a tracker is a view of the queue. With
beads an arc is an epic, an experiment a child task, a decision an issue of
type `decision`. slate writes the file first and the tracker second, and a
tracker failure is reported without losing the record.

## What slate leaves out

Automatic memory, semantic search, a server, dashboards and multi-agent
pipelines. One short-lived agent runs one experiment from a complete brief
and returns a one-page report.

## Prior art

Handoff plugins for Claude Code are numerous; most keep one appended file
and none we found verifies a note against the present. HumanLayer's shared
`thoughts/` directory showed handoffs living beside research in a shared
repository, with the commit and branch in each note's frontmatter. The
snapshot-at-a-boundary idea appears in several session-continuity plugins.
DataLad's `run` record and Sumatra showed what a run must capture to be
repeatable: the command, the inputs, the code state, the outputs. slate
borrows these ideas and depends on none of the tools.
