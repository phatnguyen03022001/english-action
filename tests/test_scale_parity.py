"""Synthetic scale and publication diagnostics parity gates (no private data)."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from time import perf_counter
import unittest

from english_action.content.contracts import SchemaDirectory, evaluate_publication, validate_dataset
from english_action.content.ledger import prepare_batch
from test_quality import synth_schemas


def _id(prefix: str, number: int) -> str:
    return f"{prefix}_01940000-0000-7000-8000-{number:012x}"


def _draft_lexicals(count: int) -> list[dict]:
    return [
        {"_id": _id("lex", i + 50000), "schemaVersion": "2.0.0",
         "lifecycleStatus": "active", "editorialStatus": "draft",
         "language": "en", "lemma": f"synthetic-{i}",
         "pos": "NOUN", "entryKind": "word"}
        for i in range(count)
    ]


def _mixed_batch(size: int) -> dict:
    if size <= 0 or size % 10:
        raise ValueError("mixed batch size must be a positive multiple of ten")
    records = []
    for i in range(size // 10):
        prefix = f"mixed-{i}"
        ref = lambda suffix: "candidate:" + prefix + "-" + suffix
        base = {"lifecycleStatus": "active", "editorialStatus": "draft"}
        entities = [
            ("en", "lex", {"language": "en", "lemma": f"synthetic-english-{i}", "pos": "VERB", "entryKind": "word"}),
            ("vi", "lex", {"language": "vi", "lemma": f"từ-giả-{i}", "pos": "VERB", "entryKind": "word"}),
            ("s1", "sns", {"entryId": ref("en"), "definition": "Synthetic English first sense"}),
            ("s2", "sns", {"entryId": ref("en"), "definition": "Synthetic English second sense"}),
            ("sv", "sns", {"entryId": ref("vi"), "definition": "Synthetic Vietnamese target sense"}),
            ("c", "con", {"senseId": ref("s1"), "constructionType": "valency", "pattern": "verb + object"}),
            ("eq", "eqv", {"sourceSenseId": ref("s1"), "targetSenseId": ref("sv"),
                           "direction": "en_to_vi", "alignmentMode": "sense_aligned",
                           "equivalenceType": "approximate", "translationEvidenceStatus": "evidence_pending", "evidenceIds": []}),
            ("rel", "rel", {"relationType": "NEAR_SYNONYM_OF", "sourceId": ref("s1"), "targetId": ref("s2")}),
            ("src", "src", {"title": "Synthetic source; not independently authoritative"}),
            ("ev", "evd", {"sourceId": ref("src"), "assertionIds": [ref("s1")]}),
        ]
        records.extend({"candidateId": prefix + "-" + suffix, "type": kind,
                        "entity": base | fields} for suffix, kind, fields in entities)
    return {"batchId": f"benchmark-mixed-{size}", "records": records}


class ScaleParityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        synth_schemas(root)
        registry = root / "v1" / "vocabularies.json"
        data = json.loads(registry.read_text())
        data["relationTypes"].append({
            "type": "NEAR_SYNONYM_OF", "sourceType": "LexicalSense",
            "targetType": "LexicalSense", "symmetric": True, "cyclePolicy": "allow"})
        registry.write_text(json.dumps(data))
        self.schemas = SchemaDirectory(root)

    def test_mixed_entity_1000_graph_replay_conflict_and_isolation(self):
        batch = _mixed_batch(1000)
        original = deepcopy(batch)
        start = perf_counter()
        state, report = prepare_batch(batch, {}, self.schemas)
        print(f"PY_MIXED_1000_SECONDS={perf_counter()-start:.3f}")
        self.assertEqual(report["status"], "prepared", report["issues"][:4])
        self.assertEqual(len(state["records"]), 1000)
        self.assertEqual(len({r["_id"] for r in state["records"]}), 1000)
        self.assertEqual(Counter(r["_id"].split("_", 1)[0] for r in state["records"]),
                         {"lex": 200, "sns": 300, "con": 100, "eqv": 100,
                          "rel": 100, "src": 100, "evd": 100})
        self.assertEqual(len(state["history"]), 1)
        self.assertEqual(len(state["mappings"]), 1000)
        self.assertEqual(batch, original)
        validation = validate_dataset(state["records"], self.schemas)
        self.assertTrue(validation["valid"], validation["issues"][:5])
        self.assertFalse(validation["publication"]["eligible"])
        self.assertIn("QUALITY_PARITY_NOT_VERIFIED",
                      {i["code"] for i in validation["publication"]["issues"]})
        self.assertEqual(validate_dataset(state["records"][::-1], self.schemas)["issues"], validation["issues"])

        replay_state, replay_report = prepare_batch(batch, state, self.schemas)
        self.assertEqual(replay_report["status"], "replayed")
        self.assertEqual(replay_state, state)
        self.assertEqual(len(replay_state["history"]), 1)

        changed = deepcopy(batch)
        changed["records"][0]["entity"]["lemma"] += "-changed"
        conflict_state, conflict = prepare_batch(changed, state, self.schemas)
        self.assertEqual(conflict["status"], "conflict")
        self.assertEqual(conflict["issues"][0]["code"], "CHANGED_BATCH_CONFLICT")
        self.assertEqual(conflict_state, state)

        orphan = {"batchId": "mixed-invalid", "records": [{
            "candidateId": "orphan", "type": "sns",
            "entity": {"entryId": _id("lex", 990001), "definition": "Orphan synthetic sense",
                       "lifecycleStatus": "active", "editorialStatus": "draft"}}]}
        failed_state, failure = prepare_batch(orphan, state, self.schemas)
        self.assertEqual(failure["status"], "conflict")
        self.assertIn("REFERENCE_NOT_FOUND", {i["code"] for i in failure["issues"]})
        self.assertEqual(failed_state, state)

        duplicate = deepcopy(batch)
        duplicate["batchId"] = "mixed-duplicate"
        duplicate["records"][1]["candidateId"] = duplicate["records"][0]["candidateId"]
        failed_state, failure = prepare_batch(duplicate, state, self.schemas)
        self.assertEqual(failure["issues"][0]["code"], "DUPLICATE_CANDIDATE_ID")
        self.assertEqual(failed_state, state)

    def test_30000_publication_diagnostics_exact_counts_with_bounded_retention(self):
        records = _draft_lexicals(30000)
        start = perf_counter()
        result = evaluate_publication(records)
        print(f"PY_PUBLICATION_30000_SECONDS={perf_counter()-start:.3f}")
        # Existing engine deliberately adds one QUALITY_PARITY_NOT_VERIFIED
        # compared with the historical Node oracle's 300,000 field issues.
        self.assertEqual(result["issueCount"], 300001)
        self.assertEqual(len(result["issues"]), 1000)
        self.assertEqual(result["omittedIssueCount"], 299001)
        self.assertFalse(result["eligible"])
        self.assertEqual(result["issues"], sorted(
            result["issues"], key=lambda i: (i["entityId"], i["path"], i["code"], i["message"])))
        self.assertIn("QUALITY_PARITY_NOT_VERIFIED",
                      {issue["code"] for issue in result["issues"]})

        zero = evaluate_publication(records, limit=0)
        self.assertEqual(zero["issueCount"], result["issueCount"])
        self.assertEqual(zero["issues"], [])
        self.assertEqual(zero["omittedIssueCount"], result["issueCount"])
        self.assertFalse(zero["eligible"])

    def test_diagnostic_cap_matches_full_sorted_issues_on_small_inputs(self):
        records = _draft_lexicals(101)
        full = evaluate_publication(records, limit=None)
        self.assertEqual(full["issueCount"], len(full["issues"]))
        for cap in (0, 1, 19, 1000, 2000):
            limited = evaluate_publication(records[::-1], limit=cap)
            self.assertEqual(limited["issues"], full["issues"][:cap])
            self.assertEqual(limited["issueCount"], full["issueCount"])
            self.assertEqual(limited["omittedIssueCount"], full["issueCount"] - min(cap, full["issueCount"]))
            self.assertFalse(limited["eligible"])


    def test_10000_topic_nodes_iterative_graph_and_cycle_rejection(self):
        from english_action.content.contracts import validate_v1_graph
        def identifier(i):
            return f"top_01940000-0000-7000-8000-{i:012x}"
        records = [
            {"_id": identifier(i+1), "schemaVersion": "1.0.0", "lifecycleStatus": "active",
             "editorialStatus": "draft", "label": "synthetic topic",
             "parentTopicId": identifier(i) if i else None}
            for i in range(10000)
        ]
        vocab = self.schemas.v1_vocabulary
        start = perf_counter()
        accepted = validate_v1_graph(records, vocab)
        print(f"PY_TOPIC_CHAIN_10000_SECONDS={perf_counter()-start:.3f}")
        self.assertTrue(accepted["valid"], accepted["issues"][:2])
        self.assertEqual(accepted["issues"], [])
        short_cycle = deepcopy(records[:30])
        short_cycle[0]["parentTopicId"] = short_cycle[-1]["_id"]
        rejected = validate_v1_graph(short_cycle, vocab)
        self.assertFalse(rejected["valid"])
        self.assertIn("CYCLE_DETECTED", {x["code"] for x in rejected["issues"]})
        reversed_result = validate_v1_graph(short_cycle[::-1], vocab)
        self.assertEqual(rejected["issues"], reversed_result["issues"])

    def test_1200_duplicate_ids_preserve_all_storage_errors_with_bounded_publication(self):
        from english_action.content.contracts import graph_issues
        records = [_draft_lexicals(1)[0] for _ in range(1200)]
        self.assertEqual(len(graph_issues(records)), 1199)
        report = evaluate_publication(records)
        self.assertFalse(report["eligible"])
        self.assertEqual(len(report["issues"]), 1000)
        self.assertEqual(report["issueCount"], 13200)
        self.assertEqual(report["omittedIssueCount"], 12200)
        storage = validate_dataset(records, self.schemas, publication_issue_limit=0)
        self.assertFalse(storage["valid"])
        self.assertEqual(storage["stage"], "graph")
        self.assertEqual(len(storage["issues"]), 1199)
        self.assertEqual(storage["publication"]["issues"], [])
        self.assertEqual(storage["publication"]["issueCount"], 13200)
        self.assertFalse(storage["publication"]["eligible"])


if __name__ == "__main__":
    unittest.main()
