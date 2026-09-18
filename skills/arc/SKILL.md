---
name: arc
description: List slate arcs, or open a new arc (a campaign, a feature, a release step) anchored to the owner's directive in their own words.
argument-hint: "[slug] [the owner's directive]"
allowed-tools: Bash(slate *), Bash(${CLAUDE_PLUGIN_ROOT}/bin/slate *)
---

# /slate:arc

Request: $ARGUMENTS

(Call `${CLAUDE_PLUGIN_ROOT}/bin/slate` if `slate` is not on the PATH.)

An arc is one line of work with a beginning and an end. Its folder in the
knowledge base holds its experiments, its handoffs and its rulings.

**No arguments:** run `slate arc list` and show the table. For one arc's
history, `slate arc show <arc>`.

**Opening an arc:**

1. The directive is the owner's instruction for this arc in their own
   words. Take it verbatim from the request or from what they said in this
   conversation. If they have not said it, ask; never compose a directive on
   the owner's behalf, and never tidy their wording. The directive is what
   later sessions are held to.
2. Choose a short kebab-case slug and a plain title.
3. Ask which repositories this arc's runs depend on, worktrees included,
   and pass them as `--repos a,b`. They decide what every snapshot, handoff
   and rerun for this arc covers; without them the arc gets the knowledge
   base's default list, which may not include its code at all.
   `slate arc new <slug> --title "<title>" --directive "<verbatim>" --by "<owner's name>" --repos <a,b>`.
   With the `bd` tracker this also creates the arc's epic; pass
   `--epic <id>` instead when an epic already exists for this work.
4. Fill the arc README's **State** paragraph with an as-of date, then
   `slate index`.
5. Commit under the commit policy (`slate where` prints it): the arc folder
   only; `ask` means show the paths and subject and ask first.

**Closing an arc:** `slate arc close <arc>`. It refuses while a handoff is
open or a run is going. Before closing, offer `/slate:promote` for anything
the arc learned that has not reached the knowledge base proper.
