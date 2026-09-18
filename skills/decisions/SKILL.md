---
name: decisions
description: Show the queue of questions waiting on the owner across all slate arcs, and record the owner's rulings verbatim. Use when the user asks what is waiting on them, or rules on an open question.
argument-hint: "[arc | decision-id]"
allowed-tools: Bash(slate *), Bash(${CLAUDE_PLUGIN_ROOT}/bin/slate *)
---

# /slate:decisions

Request: $ARGUMENTS

`slate` below means `${CLAUDE_PLUGIN_ROOT}/bin/slate`: call it by that full path, since
the plugin's `bin/` is not reliably on the PATH.

**Showing the queue.** Run `slate decisions` (add `--arc <arc>` for one
arc). Show the table as it is: blocking questions first, oldest first. For
each blocking question add one line on what it blocks. Do not argue for an
answer unless asked; give a recommendation in one sentence when asked.

**Adding a question.** `slate decision ask <arc> [--blocking] "<question>"`.
Write the question so that it can be answered cold: the choice, the options,
what each costs. Blocking means work cannot continue without the answer;
everything else waits until the owner asks or it starts to block.

**Recording a ruling.** When the owner answers, record their words exactly
as they said them: no paraphrase, no cleanup, no added emphasis.

`slate decision rule <decision-id> --by "<owner's name>" "<verbatim words>"`

With the `bd` tracker this also closes the decision issue, which unblocks
the work that depended on it. A ruling is settled: later sessions do not
reopen it. If a ruling turns out to rest on a wrong fact, put a new question
in the queue that names the ruling and the fact; never edit the old entry.

A ruling that outlives its arc is worth promoting as a decision record:
offer `/slate:promote`.

Commit `rulings.md` under the commit policy that `slate where` prints.
