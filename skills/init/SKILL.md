---
name: init
description: Set up a knowledge base for slate - writes slate.toml, chooses where arcs live, and connects the issue tracker (beads or none).
argument-hint: "[path to the knowledge base]"
disable-model-invocation: true
allowed-tools: Bash(slate *), Bash(${CLAUDE_PLUGIN_ROOT}/bin/slate *)
---

# /slate:init

Knowledge base: $ARGUMENTS
Tracker preference set when the plugin was enabled: `${user_config.tracker}`
(if that still reads as a `user_config` placeholder, no preference was set:
use `auto`)

`slate` below means `${CLAUDE_PLUGIN_ROOT}/bin/slate`: call it by that full path, since
the plugin's `bin/` is not reliably on the PATH.

slate keeps its records inside a knowledge base: a git repository of notes
that sits beside, or inside, the code it describes.

1. Find the knowledge base. Use the path given; otherwise look for the
   project's knowledge repository (its `CLAUDE.md` usually names one) and
   confirm it with the user. If there is none, offer to create a directory
   for it and stop until they answer: do not invent one.
2. Choose where arcs live. Look at the knowledge base first. If it already
   keeps per-campaign or per-project working folders somewhere, propose that
   directory so existing habits carry over; otherwise propose `arcs`.
3. Choose the tracker. Pass the preference above unless the user says
   otherwise. `auto` picks beads when the project has a `.beads` directory
   and `bd` is installed, and no tracker when it does not.
4. `slate init --kb <path> --arcs <dir> --tracker <auto|bd|none> --repos-root <dir>`.
   `--repos-root` is the directory whose git repositories get snapshotted
   into provenance; for a knowledge base that sits beside its code
   repositories that is `..`.
5. Run `slate where` and show what it resolved: knowledge base, arcs
   directory, tracker, repositories covered.
6. Fit the configuration to the workspace. The defaults are safe but
   generic; propose each of these edits to `slate.toml` with its reason, and
   make the ones the user accepts:
   - **Repositories.** `slate.toml` applies to every arc in the knowledge
     base, and arcs differ in which repositories they touch. So keep
     `[repos] include` as a modest default (the few repositories most work
     touches, or `"*"` in a small workspace) and say that each arc names its
     own with `/slate:arc ... --repos`. The knowledge base itself is always
     covered and need not be listed.
   - **Agent type.** If the project's `CLAUDE.md` or its `.claude/agents`
     pins which agent may do delegated work, set `[agent] experimenter` to it.
   - **Large outputs.** Propose an `[experiment] out_root`: a directory
     outside the knowledge base, outside `/tmp`, and outside every repository
     and worktree, because a worktree is removed when its branch lands and
     the outputs would go with it. It becomes a cleanup root.
   - **Toolchain.** Set `[provenance] tools` from what the repositories use
     (`cargo --version` and `rustc --version` beside a `Cargo.toml`, the
     interpreter beside a `pyproject.toml`) and `lockfiles` where runs depend
     on one. Leave `binaries` for executables that every arc uses; check that
     each path exists before writing it, since a missing one counts against
     every run's verdict. A library that only some runs load belongs in those
     runs' `inputs.txt`, where it is hashed and its absence fails the check.
   - **Commits.** Read the knowledge base's own rules (`CLAUDE.md`, README,
     any note on how knowledge is captured). Where they fix commit subjects
     or forbid committing without asking, mirror that under `[commit]`.
   Show the final `slate.toml`.
7. Offer, do not make, a three-line addition to the project's `CLAUDE.md`
   saying that sessions end with `/slate:handoff` and start with
   `/slate:pickup`.

Commit `slate.toml` and the arcs README only after asking.
