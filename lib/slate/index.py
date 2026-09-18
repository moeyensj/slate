"""Rewrite the ``slate:index`` blocks in arc READMEs and the arcs README.

Only the text between the index markers changes; everything else is left
byte-for-byte identical.
"""

from __future__ import annotations

import os

from . import arcs, records, scan


def _arc_index_body(cfg, slug):
    lines = ["### Experiments"]
    exps = scan.experiment_dirs(cfg, slug)
    if exps:
        for eid, d in exps:
            text = records.read(os.path.join(d, "README.md"))
            lines.append(
                "- {} [{}] {}".format(
                    eid.split("/")[1],
                    records.get_field(text, "status"),
                    records.get_field(text, "title"),
                )
            )
    else:
        lines.append("- none")
    lines.append("")
    lines.append("### Handoffs")
    hos = scan.handoff_files(cfg, slug)
    if hos:
        for hid, p in hos:
            text = records.read(p)
            lines.append("- {} [{}]".format(hid.split("/")[1], records.get_field(text, "status")))
    else:
        lines.append("- none")
    return "\n".join(lines)


def _arcs_table_body(cfg):
    lines = [
        "| arc | status | open handoffs | running | open decisions |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in arcs.listing(cfg):
        lines.append(
            f"| {row['id']} | {row['status']} | {row['open_handoffs']} "
            f"| {row['running']} | {row['open_decisions']} |"
        )
    return "\n".join(lines)


def rebuild(cfg):
    """Rewrite every index block. Returns the list of files updated."""
    updated = []
    for slug, adir in scan.arc_dirs(cfg):
        path = os.path.join(adir, "README.md")
        text = records.read(path)
        new_text, replaced = records.set_index_block(text, _arc_index_body(cfg, slug))
        if replaced and new_text != text:
            records.write(path, new_text)
            updated.append(path)
    arcs_readme = os.path.join(cfg.arcs_dir, "README.md")
    if os.path.isfile(arcs_readme):
        text = records.read(arcs_readme)
        new_text, replaced = records.set_index_block(text, _arcs_table_body(cfg))
        if replaced and new_text != text:
            records.write(arcs_readme, new_text)
            updated.append(arcs_readme)
    return updated
