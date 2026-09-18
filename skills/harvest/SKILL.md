---
name: harvest
description: Settle slate experiments whose runs finished while no session was watching - verify their outputs, record the result, and conclude them.
argument-hint: "[experiment-id]"
allowed-tools: Bash(slate *), Bash(${CLAUDE_PLUGIN_ROOT}/bin/slate *)
---

# /slate:harvest

Target: $ARGUMENTS (empty means every running experiment)

`slate` below means `${CLAUDE_PLUGIN_ROOT}/bin/slate`: call it by that full
path, since the plugin's `bin/` is not reliably on the PATH.

Run `slate exp harvest $ARGUMENTS`. It checks each running experiment: exit
code, whether every declared output exists, is non-empty and is newer than
the start of the run. It prints one row each: `done`, `failed`, `lost` (the
process died without writing an outcome, for instance across a reboot), or
still `running`.

Show the table. Then, for each experiment that settled:

1. If its **Result** section still holds the `slate:after` marker, write
   it from `outcome.json` and the files in `results/`: facts only, numbers
   with units and measurement context, the controls' results, paths to the
   outputs. Open the log only for a `failed` or `lost` run, and only its
   tail.
2. Relay what harvest reported about reproducibility: inputs that changed
   while the run was going, and the verdict with its reasons, as printed.
3. Write **Interpretation** with the user: inference labelled as inference,
   compared plainly against the prediction in the plan. Then **Conclusion**:
   what was established, in sentences that stand alone. Do not edit the plan.
4. `slate exp conclude <id> --outcome confirmed|refuted|null|inconclusive --conclusion "<one sentence>"`.
   A `lost` run has no result: `slate exp abandon <id> --reason "..."`, and
   ask whether to run it again as a new experiment.
5. Offer `/slate:promote` when the conclusion outlives the arc.

Commit under the knowledge base's commit policy (`slate where` prints it):
paths inside the arc folder only; `ask` means show the paths and subject and
ask first, and a granted commit includes the push.
