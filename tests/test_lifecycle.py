"""Public synthetic lifecycle regression cases adapted from observable Node rules."""
from __future__ import annotations

import unittest

from english_action import SchemaDirectory, validate_dataset
from english_action.contracts import lifecycle_v2_issues
from test_quality import lex, synth_schemas
from pathlib import Path
import tempfile


def ident(prefix: str, number: int) -> str:
    return f"{prefix}_01940000-0000-7000-8000-{number:012x}"


class LifecycleV2Tests(unittest.TestCase):
    def test_transition_requires_retired_source_and_incoming_adjudication(self):
        previous = {"_id": ident("sns", 1), "lifecycleStatus": "active"}
        edge = {"_id": ident("con", 2), "senseId": previous["_id"]}
        decision = {"_id": ident("dec", 3), "decisionEffect": "lifecycle_transition",
                    "previousSenseIds": [previous["_id"]],
                    "transitionAudit": {"incomingEdges": []}}
        codes = [i["code"] for i in lifecycle_v2_issues([previous, edge, decision])]
        self.assertEqual(codes, ["LIFECYCLE_INCOMING_EDGE_UNRESOLVED",
                                 "LIFECYCLE_SOURCE_NOT_RETIRED"])

    def test_duplicate_foreign_missing_and_targetless_adjudications(self):
        edge_id = ident("rel", 9)
        decision_id = ident("dec", 10)
        edge = {"edgeId": edge_id, "decisionId": ident("dec", 11),
                "resolution": "reattached"}
        decision = {"_id": decision_id, "decisionEffect": "lifecycle_transition",
                    "transitionAudit": {"incomingEdges": [edge, dict(edge)]}}
        codes = [i["code"] for i in lifecycle_v2_issues([decision])]
        self.assertEqual(codes.count("LIFECYCLE_EDGE_DUPLICATE"), 1)
        self.assertEqual(codes.count("LIFECYCLE_EDGE_DECISION_MISMATCH"), 2)
        self.assertEqual(codes.count("LIFECYCLE_EDGE_TARGET_MISSING"), 2)
        self.assertEqual(codes.count("LIFECYCLE_EDGE_HISTORY_MISSING"), 2)

    def test_disconnected_supersession_cycles_report_all_reaching_decisions(self):
        def decision(number, parent=None):
            return {"_id": ident("dec", number),
                    **({"supersedesDecisionId": ident("dec", parent)} if parent else {})}
        records = [decision(1601, 1602), decision(1602), decision(1610, 1610),
                   decision(1620, 1621), decision(1621, 1622), decision(1622, 1621),
                   decision(1623, 1620), decision(1624, 1620),
                   decision(1630, 9999), decision(1640, 1602), decision(1641, 1602)]
        got = lifecycle_v2_issues(records)
        self.assertEqual([x["entityId"] for x in got],
                         [ident("dec", x) for x in (1610, 1620, 1621, 1622, 1623, 1624)])
        self.assertTrue(all(x["code"] == "DECISION_SUPERSESSION_CYCLE" for x in got))

    def test_long_chain_uses_iterative_traversal(self):
        records = [{"_id": ident("dec", 20000 + i),
                    **({"supersedesDecisionId": ident("dec", 20001 + i)}
                       if i < 19999 else {})} for i in range(20000)]
        self.assertEqual(lifecycle_v2_issues(records), [])

    def test_v2_dataset_validation_denies_unresolved_lifecycle_transition(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            synth_schemas(root)
            schema = SchemaDirectory(root)
            entry = lex()
            sense_id = ident("sns", 1)
            sense = {"_id": sense_id, "schemaVersion": "2.0.0",
                     "lifecycleStatus": "active", "editorialStatus": "draft",
                     "entryId": entry["_id"], "definition": "Synthetic meaning"}
            decision = {"_id": ident("dec", 2), "schemaVersion": "2.0.0",
                        "lifecycleStatus": "active", "editorialStatus": "draft",
                        "decisionEffect": "lifecycle_transition",
                        "previousSenseIds": [sense_id],
                        "transitionAudit": {"incomingEdges": []}}
            report = validate_dataset([entry, sense, decision], schema)
            self.assertFalse(report["valid"])
            self.assertIn("LIFECYCLE_SOURCE_NOT_RETIRED", [x["code"] for x in report["issues"]])


if __name__ == "__main__":
    unittest.main()
