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

(Call `${CLAUDE_PLUGIN_ROOT}/bin/slate` if `slate` is not on the PATH.)

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
6. Read the knowledge base's own rules (`CLAUDE.md`, README, any note on how
   knowledge is captured). Where they fix commit subjects or forbid
   committing without asking, mirror that in `slate.toml` under `[commit]`.
   Where the project pins which agent type may run work, set
   `[agent] experimenter` to it. Show the final `slate.toml`.
7. Offer, do not make, a three-line addition to the project's `CLAUDE.md`
   saying that sessions end with `/slate:handoff` and start with
   `/slate:pickup`.

Commit `slate.toml` and the arcs README only after asking.
