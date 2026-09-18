---
name: files
description: Show which input and output files slate experiments and datasets hold on disk, mark the ones nobody needs any more for deletion, and sweep marked files after a dry run.
argument-hint: "[experiment-id | arc | sweep]"
disable-model-invocation: true
allowed-tools: Bash(slate *), Bash(${CLAUDE_PLUGIN_ROOT}/bin/slate *)
---

# /slate:files

Scope: $ARGUMENTS

`slate` below means `${CLAUDE_PLUGIN_ROOT}/bin/slate`: call it by that full path, since
the plugin's `bin/` is not reliably on the PATH.

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

## Sweeping

Deleting is a separate, deliberate step, and it is the owner's.

1. `slate sweep` (add `--arc <arc>` to narrow it). Without `--confirm` it
   deletes nothing. Show its table in full: every file of every marked
   record, the decision, and the reason for each file it will keep (outside
   the cleanup roots, tracked by git, hash no longer matches, still declared
   by another record, remote, not listed in the record). Then the bytes a
   confirmed sweep would free, and the token it prints.
2. Ask the owner, quoting the file count and the bytes. Do not run
   `--confirm` on an earlier "yes" or on a general instruction to clean up:
   the confirmation is for this table.
3. `slate sweep --confirm <token>`, with the same `--arc` as the dry run. The
   token stands for exactly the files in that table; if anything changed in
   between, slate refuses and deletes nothing: show the new dry run and ask
   again. Report what was removed, what was kept, and any `FAILED` line. Each record keeps a `cleaned.json`
   tombstone with every removed path, its size and its hash, so the record
   still says what the evidence was.

Files that a sweep keeps for a reason the owner disagrees with are deleted
by the owner, by hand. Do not delete them for them, and do not widen
`cleanup.roots` without being asked.

Commit the changed records under the commit policy that `slate where`
prints.
