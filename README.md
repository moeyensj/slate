# slate

Experiments you can reconstruct, handoffs you can trust, and decisions that
stay decided: a Claude Code plugin that keeps the record of your work inside
your project's knowledge base.

<a href="https://github.com/moeyensj/slate/actions/workflows/ci.yml"><img src="https://github.com/moeyensj/slate/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
<a href="https://code.claude.com/docs/en/plugins"><img src="https://img.shields.io/badge/Claude%20Code-plugin-D97757?logo=anthropic&logoColor=white&style=flat-square" alt="Claude Code plugin"></a>
<a href="https://github.com/steveyegge/beads"><img src="https://img.shields.io/badge/beads-optional-1a1a2e?style=flat-square" alt="beads optional"></a>
<br>
<a href="pyproject.toml"><img src="https://img.shields.io/badge/python-3.9%2B-blue?style=flat-square&logo=python&logoColor=white" alt="Python 3.9+"></a>
<a href="bin/slate"><img src="https://img.shields.io/badge/dependencies-none-brightgreen?style=flat-square" alt="No dependencies"></a>
<a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg?style=flat-square" alt="License"></a>
<br>
<a href="https://claude.ai"><img src="https://img.shields.io/badge/Built%20with-Claude%20Code-D97757?logo=anthropic&logoColor=white&style=flat-square" alt="Built with Claude Code"></a>
<a href="https://github.com/moeyensj"><img src="https://img.shields.io/badge/GitHub-moeyensj-1a1a2e?logo=github&logoColor=white&style=flat-square" alt="GitHub"></a>

---

A film slate marks every take with its scene, take and date, so the footage
can be found and trusted later. A pickup is a shot filmed afterwards to
finish a scene.

## Why

Work with an agent happens in sessions, and sessions end. Four things go
missing at that boundary.

- **The evidence behind a number.** A benchmark says 1.21x. Six weeks later
  nobody can say which commit, which configuration or which data produced
  it, or whether it still holds. slate captures all of that at the moment of
  the run, and can run it again.
- **Where you were.** The next session starts cold, or reloads an enormous
  conversation to recover a page of state. A handoff is that page, and it is
  checked against your repositories before it is believed.
- **What you decided.** Decisions made in chat get argued again, and
  questions for you get buried in transcripts. slate queues the questions
  and keeps your answers word for word.
- **What you asked for.** One reasonable measurement leads to the next, and
  three days later the session is far from the request. Every arc of work is
  anchored to your instruction, verbatim.

The record lives in your own knowledge base as plain Markdown and JSON in
git. It costs almost nothing while idle: no server, nothing loaded into
context, one line at session start when something is open.

## Quick start

```
/plugin marketplace add moeyensj/slate
/plugin install slate@slate
```

Enabling the plugin asks one question: which issue tracker (`auto`, `bd` or
`none`). Then, once per knowledge base:

```
/slate:init path/to/knowledge-base
```

A knowledge base is a git repository of notes that sits beside your code.
slate needs `git` and Python 3.9 or later, and nothing else.

## A day with slate

```
/slate:arc solver-speed "Make the solver twice as fast. Do not lose accuracy."
/slate:experiment Compare the cached and uncached solver on the July dataset
/slate:decisions
/slate:handoff
```

and the next morning, on any machine:

```
/slate:pickup
```

1. **An arc** opens with your instruction in your own words. Sessions are
   held to it, and work it does not cover gets asked about first.
2. **An experiment** is planned before it runs: question, hypothesis,
   prediction, decision rule, controls. One agent runs it. A script records
   what ran. You and Claude write what it means.
3. **Decisions** shows every question waiting on you, blocking ones first.
   Your answer is recorded word for word and is not reopened.
4. **A handoff** ends the session with a short note: the first thing to do
   next, what was verified, what is still running.
5. **Pickup** lists the open handoffs, checks the one you choose against the
   present state of your repositories, says what moved, and resumes.

## Commands

| | |
|---|---|
| `/slate:arc` | List arcs, or open one with your directive |
| `/slate:experiment` | Plan an experiment, run it, record the result |
| `/slate:harvest` | Settle runs that finished while nobody was watching |
| `/slate:rerun` | Repeat an experiment from its recorded state; does it reproduce? |
| `/slate:dataset` | Record how a dataset was made, or register an existing one |
| `/slate:handoff` | End a session with a note the next one starts from |
| `/slate:pickup` | List open handoffs; verify one and resume |
| `/slate:decisions` | What waits on you; record rulings verbatim |
| `/slate:findings` | Summarise conclusions across experiments, citations checked |
| `/slate:promote` | Turn a conclusion into a lasting note; list evidence that has aged |
| `/slate:files` | What records hold on disk; mark and sweep what nobody needs |
| `/slate:init` | Set up a knowledge base |

Everything mechanical is done by the `slate` command line in `bin/`, which
works without Claude too: `slate handoffs`, `slate decisions`,
`slate findings`, `slate data list`. Reference: [docs/cli.md](docs/cli.md).

## What gets recorded

```
<knowledge-base>/<arcs>/<arc>/
  README.md        your directive, the state, a generated index
  rulings.md       open questions and your rulings, verbatim
  handoffs/        one note per session end
  experiments/     one folder per experiment
<knowledge-base>/<datasets>/<dataset>/
  README.md        purpose, sources, recipe, contents
  MANIFEST.json    every file: path, size, sha256
```

An experiment folder holds the plan and the result (`README.md`), the exact
command (`run.sh`), its inputs and outputs by path and hash (`inputs.txt`,
`outputs.txt`), and what the script captured (`provenance.json`,
`outcome.json`). Data is referenced, never copied.

## How it keeps its promises

- **A record that can fail.** The plan is committed before the run. A run
  counts as done only if it exited cleanly and every declared output exists,
  is non-empty and is newer than the start.
- **Reconstructable, or it says why not.** Commits and whether they are
  pushed, uncommitted changes as a patch, input and output hashes,
  executables, lockfiles, the machine. Each record carries a verdict, and
  `/slate:rerun` puts it to the test.
- **Data has a history.** Sources, filters, code version, parent datasets,
  and a manifest of the files that resulted.
- **Handoffs are verified, not trusted.** Branch gone, commits diverged,
  work already merged: pickup tells you before anything starts.
- **The knowledge base learns.** Findings cite their experiments and a
  script checks the citations. Promoted notes are flagged when the code they
  measured has changed since.
- **Careful with your disk.** Cleanup is mark first, sweep later; the sweep
  is a dry run by default and explains every file it would keep.

## With beads

With [beads](https://github.com/steveyegge/beads) as the tracker, an arc is
an epic, an experiment is a child task, and a question for you is an issue
of type `decision`. Handoffs list issue ids, not prose to-do lists. slate
calls the `bd` command line and needs nothing else; a tracker failure never
loses a record.

## Configuration and design

`slate.toml` at the knowledge base root sets where arcs and datasets live,
which repositories are snapshotted, the tracker, the agent type that runs
experiments, the commit policy and the cleanup roots. Every key is
optional: see [docs/cli.md](docs/cli.md). The reasoning is in
[docs/design.md](docs/design.md).

## Development

```
python3 -m unittest discover -s tests
claude plugin validate .
claude --plugin-dir .
```

## License

MIT
