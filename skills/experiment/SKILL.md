---
name: experiment
description: Run an experiment as a record in the knowledge base - the plan (question, hypothesis, prediction, decision rule, controls) is written before the run, one agent runs it, and the result is logged with enough provenance to reconstruct it. Use when the user asks to run, measure, benchmark or compare something and wants the result kept.
argument-hint: "[arc] <what to run and with which configuration>"
allowed-tools: Bash(slate *), Bash(${CLAUDE_PLUGIN_ROOT}/bin/slate *)
---

# /slate:experiment

Request: $ARGUMENTS

The `slate` command does the bookkeeping. `slate` below means
`${CLAUDE_PLUGIN_ROOT}/bin/slate`: call it by that full path, since the
plugin's `bin/` is not reliably on the PATH. You supply the judgment. The record has two
halves separated by a rule: the plan, written before the run, and the
result, written after. Never edit the plan after the run starts.

## 1. Locate

Run `slate where`. It prints the knowledge base, the tracker, the agent type
that runs experiments and the commit policy. If it finds no knowledge base,
say so and point at `/slate:init`; stop.

Pick the arc: the one named in the request, else the only active one in
`slate arc list`, else ask. Read the arc's directive (top of its README).
If the experiment does not serve that directive, say so in one sentence and
ask before going on. This guard exists because sessions drift into side
quests one reasonable-looking measurement at a time.

## 2. Plan, before anything runs

`slate exp new <arc> <slug> --title "<title>" --hypothesis "<one sentence>"`
prints the record path. The one-sentence hypothesis is the claim under test;
it is what a later summary of findings quotes, so it must read on its own.
Fill the plan sections of the README:

- **Question**: one question.
- **Hypothesis**: the claim, and why it is held.
- **Prediction**: what will be observed if the hypothesis holds and if it
  does not, with numbers, and who predicts it ("owner" or "Claude"). If the
  user has not said what they expect and it cannot be inferred from the
  conversation, ask once, together with the decision rule.
- **Decision rule**: which result changes what happens next, and how. If no
  result would change anything, say that to the user before spending the
  compute.
- **Controls**: a case that must pass and a case that must fail, so a
  broken harness cannot look like a clean result; or `none:` with the reason.
- **Method**: what `run.sh` will do, which inputs it reads, which outputs it
  writes. For a comparison of two arms, Method names every intended
  difference between them.

Then commit the plan, following the commit policy (section 5). A plan
committed before the run is what makes the prediction provably prior.

## 3. Run: one agent, no polling

Read `brief.md` next to this file, fill its placeholders, and spawn ONE
agent of the type `slate where` reported, in the background, with the filled
brief as its whole prompt. Do not pass a model override. Set DETACH to yes
when the run could take more than about ten minutes or must outlive this
session. Do not poll the agent or the run; you are notified when it
finishes.

Inputs are never copied into the record. The agent lists them in
`inputs.txt` by path, and `slate` records their sizes and hashes. Say so if
the user asks where the data went.

## 4. Conclude

When the agent reports, read the record's Result section and
`outcome.json`, not the log. Then:

- Relay the reconstructability verdict. If it is `no`, give the reasons as
  they are printed (an unpushed commit, an unhashed dataset, a volatile
  path) and what would fix each. Do not soften it.
- Write **Interpretation**: inference, labelled as inference, including
  what the result does not show. Compare against the prediction plainly; a
  refuted hypothesis and a null result are full results.
- Write **Conclusion**: what the experiment established, in a few sentences
  that stand alone.
- `slate exp conclude <id> --outcome confirmed|refuted|null|inconclusive --conclusion "<one sentence>"`.
  The sentence carries the number and its context; it will be quoted without
  the record beside it.
- If the run was detached and is still going, say so, name the log path,
  and tell the user `/slate:harvest` or the next `/slate:pickup` settles it.
- If the conclusion is knowledge that outlives the arc (a measured baseline,
  a null result, a settled question), offer `/slate:promote`.
- A later correction goes under **Corrections** with a date; the run facts
  themselves are never rewritten.

## 5. Committing

Stage only paths inside the arc folder. Policy `ask`: show the paths and the
subject line, then ask; a granted commit includes the push. `auto`: commit
and push without asking. `never`: leave the files uncommitted and say so.
Subjects follow `commit.record_subject`; trailers and everything else follow
the project's own commit conventions.
