---
name: files
description: Show which input and output files slate experiments hold on disk, with sizes and whether they still match their recorded hashes, and mark experiments nobody needs any more for deletion.
argument-hint: "[experiment-id | arc]"
allowed-tools: Bash(slate *), Bash(${CLAUDE_PLUGIN_ROOT}/bin/slate *)
---

# /slate:files

Scope: $ARGUMENTS

(Call `${CLAUDE_PLUGIN_ROOT}/bin/slate` if `slate` is not on the PATH.)

Experiments reference their inputs and outputs by path and hash; the data
stays where it was. This command shows what is being held and lets the
owner let go of it.

1. `slate exp files [<id>] [--arc <arc>]`. Show the table as it is: per
   experiment, each input and output with its size, its state (`present`,
   `changed` since the run, `missing`, `remote`), how many other experiments
   declare the same path, any mark, and the totals. Add `--verify` to rehash
   instead of trusting size and modification time; say first that it reads
   every file.
2. Point out, in a line each: the experiments holding the most disk; files
   that are `changed` or `missing`, because those experiments can no longer
   be checked against their evidence; inputs shared with other experiments.
3. Marking is the owner's call. When they name experiments to let go of:
   `slate exp mark <id> --outputs|--inputs|--all --reason "<their reason>"`.
   Default to `--outputs`. Relay any warning in full: a promoted or cited
   experiment's outputs are evidence for a note, and an input shared with an
   unmarked experiment is still in use. `slate exp unmark <id>` undoes it.

Marking deletes nothing. It records the intent in the experiment, where the
next person can see it and object. The record itself (plan, provenance,
hashes, result, conclusion) is never removed.

Commit the changed records under the commit policy that `slate where`
prints.
