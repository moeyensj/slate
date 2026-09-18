"""Decision queue: ask/rule round trip, verbatim rulings, ordering, tracker."""

from __future__ import annotations

import os

from harness import FAKE_BD_FAIL, Base
from slate import arcs, decisions, records, scan, tracker


class DecisionBase(Base):
    def arc(self, kb, tracker_obj=None):
        cfg = self.cfg(kb)
        trk = tracker_obj or tracker.build(cfg)
        arcs.new(cfg, trk, "arc", "Arc title", "Do the thing.")
        return cfg, trk


class TestRoundTrip(DecisionBase):
    def test_ask_then_rule(self):
        kb = self.make_kb()
        cfg, trk = self.arc(kb)
        did, _ = decisions.ask(cfg, trk, "arc", "Where do handoffs live?")
        entry = decisions.find(cfg, did)
        self.assertEqual(entry["fields"]["status"], "open")
        decisions.rule(cfg, trk, did, "owner", "Under the arc folder.")
        ruled = decisions.find(cfg, did)
        self.assertEqual(ruled["fields"]["status"], "ruled")
        self.assertIn("by owner", ruled["fields"]["ruled"])
        self.assertEqual(ruled["ruling"], "Under the arc folder.")

    def test_ruling_verbatim_quotes_and_newlines(self):
        kb = self.make_kb()
        cfg, trk = self.arc(kb)
        did, _ = decisions.ask(cfg, trk, "arc", "Verbatim?")
        text = 'Use "X" not Y.\n\nAnd a second paragraph with a > glyph? no, keep it.'
        decisions.rule(cfg, trk, did, "owner", text)
        self.assertEqual(decisions.find(cfg, did)["ruling"], text)


class TestQueueOrder(DecisionBase):
    def test_blocking_first_then_id(self):
        kb = self.make_kb()
        cfg, trk = self.arc(kb)
        decisions.ask(cfg, trk, "arc", "q1 non")  # D-001
        decisions.ask(cfg, trk, "arc", "q2 blk", blocking=True)  # D-002
        decisions.ask(cfg, trk, "arc", "q3 non")  # D-003
        decisions.ask(cfg, trk, "arc", "q4 blk", blocking=True)  # D-004
        order = [e["id"] for e in decisions.queue(cfg)]
        self.assertEqual(order, ["D-002", "D-004", "D-001", "D-003"])

    def test_oldest_first_by_asked(self):
        import re

        kb = self.make_kb()
        cfg, trk = self.arc(kb)
        decisions.ask(cfg, trk, "arc", "older")  # D-001
        decisions.ask(cfg, trk, "arc", "newer")  # D-002
        path = scan.rulings_path(cfg, "arc")
        # Force D-001 to look newer than D-002 purely by its asked date.
        text = records.read(path)
        text = re.sub(r"(## D-001.*?- asked: )\S+", r"\g<1>2026-09-20", text, flags=re.DOTALL)
        text = re.sub(r"(## D-002.*?- asked: )\S+", r"\g<1>2026-09-01", text, flags=re.DOTALL)
        records.write(path, text)
        order = [e["id"] for e in decisions.queue(cfg)]
        self.assertEqual(order, ["D-002", "D-001"])


class TestTracker(DecisionBase):
    def test_create_and_close_via_bd(self):
        self.install_fake_bd()
        kb = self.make_kb(tracker="auto", tracker_dir=".")
        os.makedirs(os.path.join(kb, ".beads"))
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        arcs.new(cfg, trk, "arc", "Arc title", "Do the thing.")
        did, err = decisions.ask(cfg, trk, "arc", "Tracked question?")
        self.assertIsNone(err)
        entry = decisions.find(cfg, did)
        issue = entry["fields"]["tracker"]
        self.assertTrue(issue.startswith("bd-"))
        log = self.bd_log_text()
        self.assertIn("--type decision", log)
        decisions.rule(cfg, trk, did, "owner", "ruled it")
        self.assertIn(f"close {issue}", self.bd_log_text())

    def test_tracker_failure_does_not_lose_entry(self):
        self.install_fake_bd(script=FAKE_BD_FAIL)
        kb = self.make_kb(tracker="auto", tracker_dir=".")
        os.makedirs(os.path.join(kb, ".beads"))
        cfg = self.cfg(kb)
        trk = tracker.build(cfg)
        arcs.new(cfg, trk, "arc", "Arc title", "Do the thing.")  # epic create fails, arc kept
        did, err = decisions.ask(cfg, trk, "arc", "Still recorded?")
        self.assertIsNotNone(err)
        entry = decisions.find(cfg, did)  # entry exists despite tracker failure
        self.assertEqual(entry["fields"]["status"], "open")
        self.assertEqual(entry["fields"].get("tracker", ""), "")
