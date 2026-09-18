---
name: findings
description: Summarise what a set of slate experiments established - the important conclusions across an arc or the whole knowledge base, each one cited to the experiments behind it, with the citations checked by script.
argument-hint: "[arc]"
allowed-tools: Bash(slate *), Bash(${CLAUDE_PLUGIN_ROOT}/bin/slate *)
---

# /slate:findings

Scope: $ARGUMENTS (empty means every arc)

(Call `${CLAUDE_PLUGIN_ROOT}/bin/slate` if `slate` is not on the PATH.)

A findings file answers "what do we know, and how do we know it" for a set
of experiments. Promotion turns one experiment into one durable note; a
findings file reads across many.

1. `slate findings [--arc <arc>]` prints every concluded experiment: id,
   outcome, hypothesis, conclusion. Start from this table. Open an
   experiment's README only where its one-line conclusion is not enough to
   place it, and then read its Result, Interpretation and Corrections, not
   its log.
2. Write or update `FINDINGS.md`: in the arc's folder for one arc, at the
   root of the arcs directory for all of them. Structure:
   - An `As of <date>` line.
   - One section per question the experiments bear on, not one per
     experiment. Lead each with the finding in a sentence, then the evidence.
   - Every claim cites its evidence as `[exp:<full id>]`. A claim with no
     experiment behind it does not belong here.
   - Numbers keep their context (what was measured, on what, under which
     configuration). Quote conclusions; do not improve them.
   - Experiments that disagree appear side by side, with what differs
     between them. Never average a disagreement away and never drop the
     inconvenient one.
   - Null and refuted results are findings. List them with the same weight.
   - Keep fact and inference apart: what the experiments showed, then, under
     its own label, what you think it means.
   - Concluded experiments that you have not placed go under
     `## Not yet synthesized`, so nothing is silently missing.
3. `slate findings check <path>`. It fails on a citation that does not
   resolve to a concluded experiment, and on a concluded experiment that is
   neither cited nor listed as not yet synthesized. Fix those. It warns on
   citations to experiments that are inconclusive, corrected, resting on
   aged evidence, or marked for deletion: carry each warning into the text
   beside the claim it weakens, do not hide it.
4. Show the user the headline findings and every warning. Commit under the
   commit policy that `slate where` prints.

When updating an existing file, keep earlier findings unless an experiment
overturns them; an overturned finding is corrected in place with a date and
the experiment that overturned it.
