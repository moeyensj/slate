---
name: pickup
description: Start a session from a slate handoff note - lists the open handoffs, checks the chosen note against the present state of the repositories and the tracker, and resumes at its first action. Use when the user wants to pick up, continue or resume earlier work.
argument-hint: "[handoff-id]"
allowed-tools: Bash(slate *), Bash(${CLAUDE_PLUGIN_ROOT}/bin/slate *)
---

# /slate:pickup

Requested: $ARGUMENTS

`slate` below means `${CLAUDE_PLUGIN_ROOT}/bin/slate`: call it by that full path, since
the plugin's `bin/` is not reliably on the PATH.

## No id given

Run `slate handoffs` and show its table as it is: id, age, first action.
If there is exactly one open note, continue with it. Otherwise ask which
one, and stop.

## With an id

1. `slate pickup <id> --session ${CLAUDE_SESSION_ID}`. It compares the state
   recorded in the note with the present: each repository is `same`,
   `advanced N`, `diverged`, `branch gone`, `branch merged` or `missing`,
   and `dirty changed` when the working tree differs. It lists tracker
   issues closed since the note was written, settles the arc's running
   experiments, and warns if another session picked this note up recently.
   The last line it prints is the note's path.
2. Read the note. Read nothing else yet: the note's Pointers say what is
   worth opening, and the first action says when.
3. Tell the user, briefly: the directive; the first action; every drift row
   that is not `same`; blocking decisions that are still open; runs that
   finished or died since the note.
4. Then act:
   - Exit status 1 (`diverged`, `branch gone`, `missing`), or a claim
     warning: stop and ask. The note describes a world that no longer exists.
   - `branch merged`: the arc may be finished. Say so; ask whether to close
     the note (`slate handoff close <id> --reason merged`).
   - The first action is a question for the owner, or a blocking decision is
     open: ask it, and wait.
   - Otherwise begin the first action. With the `bd` tracker, claim the
     issue it names first (`bd update <id> --claim`).

The directive bounds the session. Work it does not cover is a side quest:
ask before starting it. Rulings listed in the note are settled and are not
reopened.
