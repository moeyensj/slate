---
name: promote
description: Turn a concluded slate experiment or a settled ruling into a durable knowledge-base note that cites its evidence; with no argument, list what is ready to promote and which notes rest on evidence that has aged.
argument-hint: "[experiment-id]"
allowed-tools: Bash(slate *), Bash(${CLAUDE_PLUGIN_ROOT}/bin/slate *)
---

# /slate:promote

Target: $ARGUMENTS

(Call `${CLAUDE_PLUGIN_ROOT}/bin/slate` if `slate` is not on the PATH.)

Arc folders hold the working record. The rest of the knowledge base holds
what stays true after the arc closes. Promotion moves a conclusion from one
to the other and keeps the link back to its evidence.

## No argument: upkeep

Run `slate exp list --status done` and show the concluded experiments with
an empty `promoted_to`. Then run `slate stale` and show its table: each
promoted note, the code path its experiment measured, and how many commits
have touched that path since. A high count does not make the note wrong; it
makes it due for a re-run. Offer, do not start, a re-run.

## Promoting one experiment

1. Read the experiment's README and `provenance.json`. Only a concluded
   experiment is promoted. Null and refuted results are worth the most:
   they evaporate fastest.
2. Find the knowledge base's own rules before writing: its `CLAUDE.md`, its
   README, and any note on how knowledge is captured. They decide the room
   (tribal, design, decisions, ...), the house style and the commit style.
   Where they are silent, use these defaults:
   - An `As of <date>` stamp and a provenance line: the experiment id, the
     commit shas from `provenance.json`, the host.
   - Fact and inference kept visibly apart and labelled.
   - Numbers keep their measurement context or are marked order-of-magnitude.
   - Code is cited by symbol, not by line number.
   - Literature cited lands wherever the knowledge base tracks references.
3. Draft the note at its target path. Then OFFER it: show the path, the
   one-line purpose and the draft's headline, and ask. Knowledge is never
   committed unoffered. If the owner declines, delete the draft.
4. On acceptance, set the experiment's `measures:` frontmatter to the code
   paths the result depends on, as `<repo>/<path>`, comma-separated. That is
   what lets `slate stale` notice when the evidence ages.
5. `slate promote <exp-id> --to <path> --purpose "<one line>"`. It checks
   the note cites the experiment and carries its stamp, records the link,
   and annotates the README directory tree. If it cannot place the tree
   line, add the line it prints by hand.
6. Commit under the commit policy (`slate where`): the note, the README and
   the experiment record, one note per commit, subject from
   `commit.record_subject`.

A settled ruling is promoted the same way, as a decision record: context,
the ruling quoted verbatim with its date, consequences. Cite the decision id
in place of an experiment id and skip step 5's command.

Correcting a promoted note happens in place, dated, with the wrong version
quoted carefully and a subject from `commit.correct_subject`.
