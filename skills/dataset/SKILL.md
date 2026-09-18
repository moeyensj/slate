---
name: dataset
description: Record how a dataset was made - its sources, the recipe that filtered and built it, the code version, its parent datasets and a manifest of the resulting files - or register one that already exists, so experiments can reference it by id and hash. Use when the user builds, filters, subsets or registers a dataset.
argument-hint: "[new <slug> | adopt <slug> <paths> | list | show <id>]"
allowed-tools: Bash(slate *), Bash(${CLAUDE_PLUGIN_ROOT}/bin/slate *)
---

# /slate:dataset

Request: $ARGUMENTS

(Call `${CLAUDE_PLUGIN_ROOT}/bin/slate` if `slate` is not on the PATH.)

A dataset record answers, for anyone holding the files later: where did this
come from, what was done to it, and is this still the same data. The data
itself never enters the knowledge base; the record holds paths and hashes.

**Looking.** `slate data list` shows every dataset: status, whether it can
be rebuilt, size, and how many records use it. `slate data show <id>` shows
its lineage: parents above it, and the datasets and experiments that depend
on it. `slate data verify <id>` rehashes the files against the manifest; say
first that it reads every file.

**Registering data that already exists.**
`slate data adopt <slug> --title "<title>" <path>...` hashes the files into
a manifest. Then fill Purpose and Sources in its README from what the owner
tells you; ask for what you do not know (where it was fetched, when, which
vintage) and write "unknown" where they do not know either. Never guess a
source. An adopted dataset cannot be rebuilt by slate, and the record says so.

**Building a new dataset.**

1. `slate data new <slug> --title "<title>"` prints the record path.
2. Fill the plan sections before building:
   - **Purpose**: what it is for, and what it must not be used for.
   - **Sources**: each raw source with its URL or path, retrieval date and
     vintage or hash; say which cannot be fetched again.
   - **Recipe**: every filter, cut and join with its parameter values, and
     why each is there. A filter nobody can explain later is how a selection
     effect gets into a result.
3. The build is a run like an experiment's. Follow section 3 of
   `/slate:experiment` with the same brief, pointing it at this record: the
   agent writes `run.sh` (the recipe, complete, nothing done by hand),
   `inputs.txt` (sources by path or URI; parent datasets as
   `dataset:<slug>`) and `outputs.txt` (the dataset's files, under
   `$SLATE_OUT`), then `slate data check`, `slate data start`.
4. After `slate data harvest <id>` reports `built`, write **Contents**:
   facts only: files, rows, columns, counts per class, date range, and how
   many rows each filter removed.
5. Commit the record under the commit policy that `slate where` prints.

Experiments then reference the dataset as `dataset:<slug>` in their
`inputs.txt`, and the pre-run check verifies the files against the manifest.

A correction to a dataset record is made in place, dated, under Corrections.
A change to the data is a new dataset with the old one as its parent.
