# slate

A Claude Code plugin for experimental and implementation work that has to
survive the session it happened in. It keeps three kinds of record inside
your project's knowledge base: experiments, handoffs between sessions, and
the questions that wait on the project's owner.

A film slate marks every take with its scene, take and date, so that footage
can be found and trusted later. A pickup is a shot filmed afterwards to
finish a scene.

## Why

Session tools preserve context. slate preserves evidence and decisions.

- **An experiment is a record that can fail.** The question, the
  hypothesis, the prediction, the decision rule and the controls are written
  and committed before the run. Fact and inference are written in separate
  sections afterwards.
- **An experiment can be reconstructed.** A script records the commit of
  every repository and whether it is pushed, uncommitted changes as a patch,
  the exact command, the hashes of every input and output, the executables,
  the lockfiles and the machine. Inputs are referenced by path and hash and
  never copied. Each record carries a verdict, reconstructable or not, with
  the reasons.
- **A handoff is checked against reality.** Picking one up compares the
  branches, commits and working trees it recorded with what is there now,
  and says what moved, before any work starts.
- **The owner's words are kept as they were said.** Every arc of work opens
  with the owner's directive, verbatim, and sessions are held to it. Open
  questions queue in one place; rulings are recorded verbatim and are not
  reopened.
- **The knowledge base learns.** A concluded experiment is promoted into a
  durable note that cites its evidence, and slate reports when the code that
  evidence measured has since changed. A findings file summarises what a set
  of experiments established, every claim cited to its experiments, with the
  citations checked by script.
- **It costs almost nothing when idle.** No server and nothing loaded into
  context: one line at session start, and only when something is open.

## Install

```
/plugin marketplace add moeyensj/slate
/plugin install slate@slate
```

Enabling the plugin asks for one option, the issue tracker: `auto`, `bd` or
`none`. Then, once per knowledge base:

```
/slate:init path/to/knowledge-base
```

Requires `git` and Python 3.9 or later. [beads](https://github.com/steveyegge/beads)
is optional.

## The loop

| Command | What it does |
|---|---|
| `/slate:arc` | Lists arcs, or opens one anchored to the owner's directive |
| `/slate:experiment` | Plans an experiment, has one agent run it, records the result |
| `/slate:harvest` | Settles runs that finished while no session was watching |
| `/slate:handoff` | Ends a session with a note the next one can start from |
| `/slate:pickup` | Lists open handoffs; checks one against the present and resumes |
| `/slate:decisions` | Shows what waits on the owner; records rulings verbatim |
| `/slate:findings` | Summarises conclusions across experiments, with checked citations |
| `/slate:promote` | Turns a conclusion into a knowledge-base note; lists aged evidence |
| `/slate:files` | Shows the files experiments hold on disk; marks them for deletion |
| `/slate:init` | Sets up a knowledge base |

Everything mechanical is done by the `slate` command line in `bin/`, which
works without Claude as well: `slate handoffs`, `slate decisions`,
`slate exp harvest`. See [docs/cli.md](docs/cli.md).

## Where records live

In the knowledge base, one folder per arc (a campaign, a feature, a release
step):

```
<knowledge-base>/<arcs>/<arc>/
  README.md        the directive, the state, a generated index
  rulings.md       open questions and verbatim rulings
  handoffs/        one note per session end
  experiments/     one folder per experiment: plan, run.sh, inputs.txt,
                   outputs.txt, provenance.json, outcome.json, results
```

Records are plain Markdown and JSON in git, so they are versioned, they
reach your other machines with a push, and they read fine without slate.
Private handoffs go to a directory that git ignores.

## With beads

With the `bd` tracker an arc is an epic, an experiment is a child task that
points at its record, and a question for the owner is an issue of type
`decision`. Handoffs list tracker ids instead of prose to-do lists, and a
pickup reports the ids that closed since the note was written. slate calls
the `bd` command line and needs nothing else from beads. A tracker failure
never loses a record.

## Configuration

`slate.toml` at the knowledge base root: where arcs live, which
repositories are snapshotted, the tracker, which agent type runs
experiments, the commit policy, pre-run checks. Every key is optional;
[docs/cli.md](docs/cli.md) lists them. The reasoning behind the design is in
[docs/design.md](docs/design.md).

## Development

```
python3 -m unittest discover -s tests
claude plugin validate .
claude --plugin-dir .
```
