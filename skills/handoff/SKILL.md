---
name: handoff
description: End a session by writing a handoff note into the knowledge base - the first action for the next session, verified state, what is still running, open owner decisions, traps. Use when the user asks for a handoff or wants to stop and continue later.
argument-hint: "[arc] [--private]"
allowed-tools: Bash(slate *), Bash(${CLAUDE_PLUGIN_ROOT}/bin/slate *)
---

# /slate:handoff

Request: $ARGUMENTS

A handoff is what a fresh session reads instead of this conversation. It is
short, it is written once, and it is superseded by the next note rather than
edited. `slate` generates everything mechanical; you write four sections.
(Call `${CLAUDE_PLUGIN_ROOT}/bin/slate` if `slate` is not on the PATH.)

## 1. Before the note

- `slate where`, then pick the arc: the one named, else the arc this
  session worked on, else ask. If the work has no arc yet, open one first
  with the `/slate:arc` steps; the directive must be the owner's own words.
- Every question that waits on the owner goes into the queue now, so the
  note picks it up: `slate decision ask <arc> [--blocking] "<question>"`.
  Mark it blocking only when work cannot continue without the answer.
- Every follow-up task goes into the tracker (`bd create ...` when the
  tracker is `bd`), not into prose. The note points at the queue; it is not
  the queue.
- A run still going? `slate exp harvest` first, so the note's "Running now"
  is true as of now.

## 2. The note

`slate handoff new <arc> --session ${CLAUDE_SESSION_ID}` (add `--private`
when the user says the work is confidential; private notes are never
committed). It prints the path. The directive, Running now, Landed,
Decisions, Tracker and the state block are generated: leave them alone.
Replace the four `slate:fill` markers:

- **First action**: ONE concrete thing: a command, a file to open, or a
  question to put to the owner. If the honest first action is "ask the
  owner what they want", write exactly that.
- **State**: what is done, each item with how it was verified; then what is
  not done; then what was not verified. No story of the session. Nothing
  the generated sections already say.
- **Traps**: what cost time and how to avoid it, or "None."
- **Pointers**: paths, experiment ids, documents. Nothing else.

If the session went wrong (scope drift, a wrong guess about what the owner
wanted), say so plainly under State, quoting the owner. The next session
needs that more than it needs a tidy account.

Knowledge that outlives the arc does not belong in a handoff: offer
`/slate:promote` for it instead.

## 3. Finish

`slate handoff check <id>` and fix what it reports (a leftover marker, too
many lines: cut, do not compress). Then `slate index`.

Commit under the commit policy that `slate where` printed: paths inside the
arc folder only. `ask`: show the paths and the subject, then ask; a granted
commit includes the push, because a handoff that is not pushed does not
reach the other machine. `auto`: commit and push. `never`, or a private
note: leave it uncommitted and say so.

Reply in three lines: the note's path, its First action, and whether it is
committed and pushed.
