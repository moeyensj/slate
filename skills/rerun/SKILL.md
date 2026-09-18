---
name: rerun
description: Repeat a concluded slate experiment against the exact recorded state - commits, uncommitted changes, inputs - and report whether the result reproduces.
argument-hint: "<experiment-id>"
disable-model-invocation: true
allowed-tools: Bash(slate *), Bash(${CLAUDE_PLUGIN_ROOT}/bin/slate *)
---

# /slate:rerun

Experiment: $ARGUMENTS

(Call `${CLAUDE_PLUGIN_ROOT}/bin/slate` if `slate` is not on the PATH.)

1. Read the experiment's `provenance.json` verdict first. If it says
   `reconstructable: no`, give the reasons as printed and ask whether to go
   on with `--force`; say what a forced rerun can and cannot show. A rerun
   is always refused when an input no longer matches its recorded hash: that
   would be a new experiment, so offer `/slate:experiment` instead.
2. Start `slate exp rerun <id>` as ONE background command and wait for its
   completion notice; a rerun rebuilds the recorded commits in a temporary
   tree and may take as long as the original plus a build. Never poll. The
   original working trees are not touched. Add `--keep` only if the user
   wants to inspect the reconstructed tree.
3. Report `rerun.json` as it is: `reproduced: yes`, `no` or `partly`, and
   the per-output table (`identical`, `equivalent`, `different`). Bitwise
   difference is not failure by itself: if outputs differ, say by how much
   where the files allow it, and whether the original record has a
   `compare.sh` that defines equivalence. Do not explain a difference away.
4. The rerun is its own record, linked by `rerun_of`. Write its Result (facts)
   and, with the user, its Interpretation and Conclusion, then
   `slate exp conclude <rerun-id> --outcome ... --conclusion "..."`. A failed
   reproduction is a finding: offer to add it to the findings file, and if
   the original was promoted, say which note now rests on unreproduced
   evidence.
5. Commit under the commit policy that `slate where` prints.
