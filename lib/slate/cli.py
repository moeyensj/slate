"""Argument parsing and command dispatch for the ``slate`` CLI."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import (
    arcs,
    config,
    decisions,
    findings,
    gitstate,
    handoffs,
    index,
    knowledge,
    records,
    runs,
    scan,
    tracker,
)

# --------------------------------------------------------------------------
# Output helpers
# --------------------------------------------------------------------------


def _out(line=""):
    print(line)


def _emit_json(obj):
    print(json.dumps(obj, indent=2))


def _print_table(headers, rows):
    """Print a compact left-aligned table; the shared table printer."""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    print(fmt.format(*headers).rstrip())
    for row in rows:
        print(fmt.format(*row).rstrip())


# --------------------------------------------------------------------------
# init / where / status / snapshot (config-light or no-git commands)
# --------------------------------------------------------------------------


def cmd_init(args):
    root = os.path.abspath(args.kb) if args.kb else os.getcwd()
    os.makedirs(root, exist_ok=True)
    toml_path = os.path.join(root, config.SLATE_TOML)
    arcs_dir_name = args.arcs or "arcs"
    tracker_kind = args.tracker or "auto"
    repos_root = args.repos_root or ".."

    wrote_toml = False
    if not os.path.isfile(toml_path):
        body = (
            "[kb]\n"
            f'arcs = "{arcs_dir_name}"\n\n'
            "[repos]\n"
            f'root = "{repos_root}"\n\n'
            "[tracker]\n"
            f'kind = "{tracker_kind}"\n'
        )
        records.write(toml_path, body)
        wrote_toml = True

    cfg = config.load(root)
    os.makedirs(cfg.arcs_dir, exist_ok=True)
    arcs_readme = os.path.join(cfg.arcs_dir, "README.md")
    if not os.path.isfile(arcs_readme):
        records.write(
            arcs_readme, f"# {cfg.name} arcs\n\n<!-- slate:index -->\n<!-- /slate:index -->\n"
        )
    handoffs._ensure_private_ignored(cfg)

    if args.json:
        _emit_json(
            {
                "root": cfg.root,
                "arcs": cfg.arcs_dir,
                "tracker": cfg.tracker_kind(),
                "repos": [n for n, _ in cfg.covered_repos()],
                "wrote_slate_toml": wrote_toml,
            }
        )
        return 0
    _out("slate.toml: {}{}".format(toml_path, "" if wrote_toml else " (already existed, kept)"))
    _out(f"knowledge base: {cfg.root}")
    _out(f"arcs directory: {cfg.arcs_dir}")
    _out(f"tracker: {cfg.tracker_kind()}")
    _out("repos: {}".format(", ".join(n for n, _ in cfg.covered_repos())))
    return 0


def cmd_where(args, cfg, trk):
    repos = [n for n, _ in cfg.covered_repos()]
    if args.json:
        _emit_json(
            {
                "root": cfg.root,
                "arcs": cfg.arcs_dir,
                "tracker": cfg.tracker_kind(),
                "repos": repos,
                "experimenter": cfg.agent_experimenter,
                "commit_policy": cfg.commit_policy,
                "record_subject": cfg.record_subject,
                "correct_subject": cfg.correct_subject,
            }
        )
        return 0
    _out(f"knowledge base: {cfg.root}")
    _out(f"arcs directory: {cfg.arcs_dir}")
    _out(f"tracker: {cfg.tracker_kind()}")
    _out("repos: {}".format(", ".join(repos)))
    _out(f"experimenter: {cfg.agent_experimenter}")
    _out(f"commit policy: {cfg.commit_policy}")
    _out(f"  record subject: {cfg.record_subject!r}")
    _out(f"  correct subject: {cfg.correct_subject!r}")
    return 0


def _status_counts(cfg):
    open_created = []
    for _hid, p in scan.handoff_files(cfg):
        text = records.read(p)
        if records.get_field(text, "status") == "open":
            open_created.append(records.get_field(text, "created"))
    finished = 0
    for _eid, d in scan.experiment_dirs(cfg):
        if scan.experiment_field(d, "status") == "running" and os.path.isfile(
            os.path.join(d, "outcome.json")
        ):
            finished += 1
    open_dec = len([e for e in decisions.load_all(cfg) if e["fields"].get("status") == "open"])
    oldest_age = None
    from . import util

    for created in open_created:
        dt = util.parse_ts(created)
        if dt:
            age = util.now_utc().timestamp() - dt.timestamp()
            oldest_age = age if oldest_age is None else max(oldest_age, age)
    return {
        "open_handoffs": len(open_created),
        "oldest_age_seconds": oldest_age,
        "runs_finished_not_harvested": finished,
        "decisions_waiting": open_dec,
    }


def cmd_status(args, cfg, trk):
    from . import util

    counts = _status_counts(cfg)
    if args.json:
        _emit_json(counts)
        return 0
    segments = []
    hint = None
    if counts["open_handoffs"]:
        age = util.human_age(counts["oldest_age_seconds"] or 0)
        segments.append(f"{counts['open_handoffs']} open handoffs (oldest {age})")
        hint = hint or "/slate:pickup"
    if counts["runs_finished_not_harvested"]:
        n = counts["runs_finished_not_harvested"]
        segments.append(f"{n} run{'' if n == 1 else 's'} finished, not harvested")
        hint = hint or "/slate:harvest"
    if counts["decisions_waiting"]:
        segments.append(f"{counts['decisions_waiting']} decisions waiting on the owner")
        hint = hint or "/slate:decisions"
    if segments:
        _out("slate: " + " · ".join(segments) + " — " + hint)
    return 0


def cmd_snapshot(args, cfg, trk):
    snap = gitstate.snapshot(cfg)
    if args.json:
        _emit_json(snap)
        return 0
    for name, rec in snap["repos"].items():
        ahead = "-" if rec["ahead"] is None else rec["ahead"]
        behind = "-" if rec["behind"] is None else rec["behind"]
        _out(
            f"{name:<16} {rec['branch']:<20} {rec['head'][:12]}  "
            f"dirty={rec['dirty']}  ahead={ahead} behind={behind}  "
            f"pushed={'yes' if rec['pushed'] else 'no'}"
        )
    _out(f"host: {snap['host']}")
    _out(f"user: {snap['user']}")
    _out(f"time: {snap['time']}")
    hw = snap.get("hardware", {})
    _out(
        "hardware: cpu={} cores={} memory={} os={}".format(
            hw.get("cpu"), hw.get("cores"), _human_bytes(hw.get("memory_bytes")), hw.get("os")
        )
    )
    for tool in snap["tools"]:
        _out(f"tool: {tool['line']}")
    for var, value in snap.get("env", {}).items():
        _out(f"env: {var}={value}")
    for b in snap.get("binaries", []):
        _out("binary: {} size={} sha256={}".format(b.get("path"), b.get("size"), b.get("sha256")))
    for lf in snap.get("lockfiles", []):
        _out("lockfile: {} sha256={}".format(lf.get("path"), lf.get("sha256")))
    return 0


def _human_bytes(n):
    if not n:
        return "-"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


# --------------------------------------------------------------------------
# arcs
# --------------------------------------------------------------------------


def cmd_arc_new(args, cfg, trk):
    slug, arc_dir, err = arcs.new(
        cfg, trk, args.slug, args.title, args.directive, by=args.by, epic=args.epic
    )
    _out(arc_dir)
    if err:
        _out(f"tracker error: {err}")
    return 0


def cmd_arc_list(args, cfg, trk):
    rows = arcs.listing(cfg)
    if args.json:
        _emit_json(rows)
        return 0
    for r in rows:
        _out(
            f"{r['id']:<20} {r['status']:<8} handoffs={r['open_handoffs']} "
            f"running={r['running']} decisions={r['open_decisions']}"
        )
    return 0


def cmd_arc_show(args, cfg, trk):
    slug, _ = scan.find_arc(cfg, args.arc)
    items = arcs.timeline(cfg, slug)
    if args.json:
        _emit_json([{"date": d, "kind": k, "text": t} for d, k, t in items])
        return 0
    for date, kind, text in items:
        _out(f"{date}  {kind:<10} {text}")
    return 0


def cmd_arc_close(args, cfg, trk):
    slug, _ = scan.find_arc(cfg, args.arc)
    ok, msg = arcs.close(cfg, slug)
    _out(msg)
    return 0 if ok else 1


def cmd_index(args, cfg, trk):
    updated = index.rebuild(cfg)
    if args.json:
        _emit_json({"updated": updated})
        return 0
    _out(f"index: {len(updated)} file(s) updated")
    for path in updated:
        _out(f"  {path}")
    return 0


# --------------------------------------------------------------------------
# experiments
# --------------------------------------------------------------------------


def cmd_exp_new(args, cfg, trk):
    slug, _ = scan.find_arc(cfg, args.arc)
    exp_id, record_dir, err = runs.new(
        cfg, trk, slug, args.slug, args.title, serves=args.serves, hypothesis=args.hypothesis
    )
    _out(record_dir)
    if err:
        _out(f"tracker error: {err}")
    return 0


def cmd_exp_check(args, cfg, trk):
    _eid, record_dir = scan.find_experiment(cfg, args.id)
    ok, lines = runs.check(cfg, record_dir)
    if args.json:
        _emit_json({"ok": ok, "lines": lines})
        return 0 if ok else 1
    for line in lines:
        _out(line)
    _out("check: %s" % ("passed" if ok else "FAILED"))
    return 0 if ok else 1


def cmd_exp_start(args, cfg, trk):
    _eid, record_dir = scan.find_experiment(cfg, args.id)
    code, lines = runs.start(cfg, record_dir, detach=args.detach)
    for line in lines:
        _out(line)
    return code


def cmd_exp_harvest(args, cfg, trk):
    rows = runs.harvest(cfg, args.id)
    if args.json:
        _emit_json(rows)
        return 0
    if not rows:
        _out("no running experiments")
    for row in rows:
        _out(row)
    return 0


def cmd_exp_conclude(args, cfg, trk):
    eid, err = runs.conclude(cfg, trk, args.id, args.outcome, args.conclusion)
    _out(f"{eid} concluded: {args.outcome}")
    if err:
        _out(f"tracker error: {err}")
    return 0


def cmd_exp_files(args, cfg, trk):
    groups = runs.files_view(cfg, given=args.id, arc=args.arc, verify=args.verify)
    if args.json:
        _emit_json(groups)
        return 0
    for g in groups:
        _out(f"# {g['id']}")
        _print_table(
            ["role", "path", "size", "state", "shared", "mark"],
            [
                [
                    r["role"],
                    r["path"],
                    "-" if r["size"] is None else str(r["size"]),
                    r["state"],
                    str(r["shared"]),
                    r["mark"],
                ]
                for r in g["rows"]
            ],
        )
        it, ot = g["input_total"], g["output_total"]
        _out(f"  inputs: {it['files']} file(s), {it['bytes']}B")
        _out(f"  outputs: {ot['files']} file(s), {ot['bytes']}B")
    return 0


def cmd_exp_mark(args, cfg, trk):
    scope = "all" if args.all else ("inputs" if args.inputs else "outputs")
    eid, warnings = runs.mark(cfg, args.id, scope, args.reason)
    _out(f"marked {eid}: {scope}")
    for w in warnings:
        _out(f"warning: {w}")
    return 0


def cmd_exp_unmark(args, cfg, trk):
    eid = runs.unmark(cfg, args.id)
    _out(f"unmarked {eid}")
    return 0


def cmd_exp_abandon(args, cfg, trk):
    eid = runs.abandon(cfg, args.id, args.reason)
    _out(f"{eid} abandoned")
    return 0


def cmd_exp_list(args, cfg, trk):
    arc = None
    if args.arc:
        arc, _ = scan.find_arc(cfg, args.arc)
    rows = runs.listing(cfg, arc=arc, status=args.status)
    if args.json:
        _emit_json(rows)
        return 0
    for r in rows:
        _out(f"{r['id']:<28} {r['status']:<10} {r['outcome'] or '-':<12} {r['title']}")
    return 0


# --------------------------------------------------------------------------
# handoffs
# --------------------------------------------------------------------------


def cmd_handoff_new(args, cfg, trk):
    slug, _ = scan.find_arc(cfg, args.arc)
    hid, path = handoffs.new(cfg, trk, slug, private=args.private, session=args.session)
    _out(path)
    return 0


def cmd_handoff_check(args, cfg, trk):
    ok, lines = handoffs.check(cfg, args.id)
    for line in lines:
        _out(line)
    return 0 if ok else 1


def cmd_handoff_close(args, cfg, trk):
    hid = handoffs.close(cfg, args.id, reason=args.reason)
    _out(f"closed {hid}")
    return 0


def cmd_handoffs(args, cfg, trk):
    rows = handoffs.listing(cfg, show_all=args.all)
    if args.json:
        _emit_json(rows)
        return 0
    for r in rows:
        _out(f"{r['id']:<24} {r['age']:<6} {r['status']:<10} {r['first_action']}")
    return 0


def cmd_pickup(args, cfg, trk):
    code, lines = handoffs.pickup(cfg, trk, args.id, dry=args.dry, session=args.session)
    for line in lines:
        _out(line)
    return code


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------


def cmd_decision_ask(args, cfg, trk):
    slug, _ = scan.find_arc(cfg, args.arc)
    did, err = decisions.ask(cfg, trk, slug, args.text, blocking=args.blocking)
    _out(did)
    if err:
        _out(f"tracker error: {err}")
    return 0


def cmd_decision_rule(args, cfg, trk):
    did, err = decisions.rule(cfg, trk, args.id, args.by, args.text)
    _out(f"ruled {did}")
    if err:
        _out(f"tracker error: {err}")
    return 0


def cmd_decisions(args, cfg, trk):
    arc = None
    if args.arc:
        arc, _ = scan.find_arc(cfg, args.arc)
    rows = decisions.queue(cfg, arc=arc, include_all=args.all)
    if args.json:
        _emit_json(
            [
                {
                    "id": e["full_id"],
                    "blocking": e["fields"].get("blocking", "no"),
                    "status": e["fields"].get("status", ""),
                    "age_days": decisions.age_days(e["fields"].get("asked", "")),
                    "question": e["question"],
                }
                for e in rows
            ]
        )
        return 0
    for e in rows:
        blocking = "blocking" if e["fields"].get("blocking") == "yes" else "-"
        age = decisions.age_days(e["fields"].get("asked", ""))
        q = " ".join(e["question"].split())
        if len(q) > 60:
            q = q[:59] + "…"
        _out(f"{e['full_id']:<14} {blocking:<8} {age:>3}d  {q}")
    return 0


# --------------------------------------------------------------------------
# knowledge
# --------------------------------------------------------------------------


def cmd_promote(args, cfg, trk):
    _eid, lines = knowledge.promote(cfg, args.id, args.to, args.purpose)
    for line in lines:
        _out(line)
    return 0


def cmd_findings(args, cfg, trk):
    arc = None
    if args.arc:
        arc, _ = scan.find_arc(cfg, args.arc)
    rows = findings.table(cfg, arc=arc)
    if args.json:
        _emit_json(rows)
        return 0
    _print_table(
        ["id", "outcome", "hypothesis", "conclusion"],
        [[r["id"], r["outcome"], r["hypothesis"], r["conclusion"]] for r in rows],
    )
    return 0


def cmd_findings_check(args, cfg, trk):
    ok, lines = findings.check_file(cfg, args.path)
    for line in lines:
        _out(line)
    return 0 if ok else 1


def cmd_stale(args, cfg, trk):
    rows = knowledge.stale(cfg)
    if args.json:
        _emit_json(rows)
        return 0
    for r in rows:
        _out(
            f"{r['note']:<40} {r['experiment']:<28} {r['path']:<24} "
            f"commits={r['commits']}  age={r['age']}"
        )
    return 0


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def build_parser():
    # The subparser copies use SUPPRESS defaults so a global flag placed before
    # the subcommand (parsed by the main parser) is not clobbered by the
    # subparser's own default when the flag is absent after the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--kb", default=argparse.SUPPRESS, help="knowledge-base path")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="emit JSON")

    parser = argparse.ArgumentParser(prog="slate", description="slate bookkeeping CLI")
    parser.add_argument("--kb", default=None, help="knowledge-base path")
    parser.add_argument("--json", action="store_true", default=False, help="emit JSON")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("init", parents=[common])
    p.add_argument("--arcs")
    p.add_argument("--tracker", choices=["auto", "bd", "none"])
    p.add_argument("--repos-root", dest="repos_root")
    p.set_defaults(func=cmd_init, needs_cfg=False)

    sub.add_parser("where", parents=[common]).set_defaults(func=cmd_where)
    sub.add_parser("status", parents=[common]).set_defaults(func=cmd_status, is_status=True)
    sub.add_parser("snapshot", parents=[common]).set_defaults(func=cmd_snapshot)
    sub.add_parser("index", parents=[common]).set_defaults(func=cmd_index)

    # arc
    arc = sub.add_parser("arc", parents=[common])
    arc_sub = arc.add_subparsers(dest="sub")
    a = arc_sub.add_parser("new", parents=[common])
    a.add_argument("slug")
    a.add_argument("--title", required=True)
    a.add_argument("--directive", required=True)
    a.add_argument("--by")
    a.add_argument("--epic")
    a.set_defaults(func=cmd_arc_new)
    arc_sub.add_parser("list", parents=[common]).set_defaults(func=cmd_arc_list)
    a = arc_sub.add_parser("show", parents=[common])
    a.add_argument("arc")
    a.set_defaults(func=cmd_arc_show)
    a = arc_sub.add_parser("close", parents=[common])
    a.add_argument("arc")
    a.set_defaults(func=cmd_arc_close)

    # exp
    exp = sub.add_parser("exp", parents=[common])
    exp_sub = exp.add_subparsers(dest="sub")
    e = exp_sub.add_parser("new", parents=[common])
    e.add_argument("arc")
    e.add_argument("slug")
    e.add_argument("--title", required=True)
    e.add_argument("--serves")
    e.add_argument("--hypothesis")
    e.set_defaults(func=cmd_exp_new)
    e = exp_sub.add_parser("check", parents=[common])
    e.add_argument("id")
    e.set_defaults(func=cmd_exp_check)
    e = exp_sub.add_parser("start", parents=[common])
    e.add_argument("id")
    e.add_argument("--detach", action="store_true")
    e.set_defaults(func=cmd_exp_start)
    e = exp_sub.add_parser("harvest", parents=[common])
    e.add_argument("id", nargs="?")
    e.set_defaults(func=cmd_exp_harvest)
    e = exp_sub.add_parser("conclude", parents=[common])
    e.add_argument("id")
    e.add_argument(
        "--outcome", required=True, choices=["confirmed", "refuted", "null", "inconclusive"]
    )
    e.add_argument("--conclusion", required=True)
    e.set_defaults(func=cmd_exp_conclude)
    e = exp_sub.add_parser("abandon", parents=[common])
    e.add_argument("id")
    e.add_argument("--reason", required=True)
    e.set_defaults(func=cmd_exp_abandon)
    e = exp_sub.add_parser("list", parents=[common])
    e.add_argument("--arc")
    e.add_argument("--status")
    e.set_defaults(func=cmd_exp_list)
    e = exp_sub.add_parser("files", parents=[common])
    e.add_argument("id", nargs="?")
    e.add_argument("--arc")
    e.add_argument("--verify", action="store_true")
    e.set_defaults(func=cmd_exp_files)
    e = exp_sub.add_parser("mark", parents=[common])
    e.add_argument("id")
    grp = e.add_mutually_exclusive_group(required=True)
    grp.add_argument("--inputs", action="store_true")
    grp.add_argument("--outputs", action="store_true")
    grp.add_argument("--all", action="store_true")
    e.add_argument("--reason", required=True)
    e.set_defaults(func=cmd_exp_mark)
    e = exp_sub.add_parser("unmark", parents=[common])
    e.add_argument("id")
    e.set_defaults(func=cmd_exp_unmark)

    # handoff
    ho = sub.add_parser("handoff", parents=[common])
    ho_sub = ho.add_subparsers(dest="sub")
    h = ho_sub.add_parser("new", parents=[common])
    h.add_argument("arc")
    h.add_argument("--private", action="store_true")
    h.add_argument("--session")
    h.set_defaults(func=cmd_handoff_new)
    h = ho_sub.add_parser("check", parents=[common])
    h.add_argument("id")
    h.set_defaults(func=cmd_handoff_check)
    h = ho_sub.add_parser("close", parents=[common])
    h.add_argument("id")
    h.add_argument("--reason")
    h.set_defaults(func=cmd_handoff_close)

    h = sub.add_parser("handoffs", parents=[common])
    h.add_argument("--all", action="store_true")
    h.set_defaults(func=cmd_handoffs)

    h = sub.add_parser("pickup", parents=[common])
    h.add_argument("id")
    h.add_argument("--dry", action="store_true")
    h.add_argument("--session")
    h.set_defaults(func=cmd_pickup)

    # decision
    dec = sub.add_parser("decision", parents=[common])
    dec_sub = dec.add_subparsers(dest="sub")
    d = dec_sub.add_parser("ask", parents=[common])
    d.add_argument("arc")
    d.add_argument("text")
    d.add_argument("--blocking", action="store_true")
    d.set_defaults(func=cmd_decision_ask)
    d = dec_sub.add_parser("rule", parents=[common])
    d.add_argument("id")
    d.add_argument("text")
    d.add_argument("--by", required=True)
    d.set_defaults(func=cmd_decision_rule)

    d = sub.add_parser("decisions", parents=[common])
    d.add_argument("--arc")
    d.add_argument("--all", action="store_true")
    d.set_defaults(func=cmd_decisions)

    # knowledge
    pr = sub.add_parser("promote", parents=[common])
    pr.add_argument("id")
    pr.add_argument("--to", required=True)
    pr.add_argument("--purpose", required=True)
    pr.set_defaults(func=cmd_promote)

    # findings
    fnd = sub.add_parser("findings", parents=[common])
    fnd.add_argument("--arc")
    fnd.set_defaults(func=cmd_findings)
    fnd_sub = fnd.add_subparsers(dest="sub")
    fc = fnd_sub.add_parser("check", parents=[common])
    fc.add_argument("path")
    fc.set_defaults(func=cmd_findings_check)

    sub.add_parser("stale", parents=[common]).set_defaults(func=cmd_stale)

    # hidden supervisor entry
    sup = sub.add_parser("__supervise")
    sup.add_argument("record_dir")
    sup.set_defaults(func=None, supervise=True)

    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)

    if getattr(args, "supervise", False):
        runs.supervise_main(args.record_dir)
        return 0

    if not getattr(args, "command", None) or not getattr(args, "func", None):
        parser.print_help()
        return 2

    if args.func is cmd_init:
        return cmd_init(args)

    kb_flag = getattr(args, "kb", None)
    env = os.environ.get("SLATE_KB")
    try:
        root = config.find_kb(os.getcwd(), kb_flag, env)
    except config.KbAmbiguous as exc:
        for m in exc.matches:
            sys.stderr.write(f"  {m}\n")
        sys.stderr.write("ambiguous knowledge base; use --kb\n")
        return 2

    if root is None:
        if getattr(args, "is_status", False):
            return 0
        sys.stderr.write("no knowledge base found; run 'slate init'\n")
        return 2

    cfg = config.load(root)
    trk = tracker.build(cfg)

    try:
        return args.func(args, cfg, trk)
    except records.GateError as exc:
        _out(str(exc))
        return 1
    except records.IdError as exc:
        sys.stderr.write(str(exc) + "\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
