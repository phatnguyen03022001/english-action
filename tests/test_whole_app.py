"""Synthetic DATA-05 contract/graph regression; no private datasets or scorer."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from english_action.content.whole_app import WholeAppSchemas, validate_whole_app

IDS = {"core": "urn:english-content:core:v1",
       "assessment": "urn:english-content:assessment:v1",
       "exam": "urn:english-content:exam:v1",
       "media": "urn:english-content:media:v1"}


def ref(kind, id, rev=1):
    return {"scope": "canonical", "targetKind": kind, "id": id, "revision": rev}


def item():
    return {"kind": "foundation_item", "schemaVersion": 1, "id": "itm_test",
            "revision": 1, "lifecycle": {"state": "current"},
            "response": {"interactionKind": "choice", "variant": "single"},
            "sources": [{"binding": "shared", "ref": ref("stimulus", "st_test")}],
            "dependencies": [], "answerSpecRef": {"id": "ast_test", "revision": 1}}


def stimulus(state="current", revision=1):
    return {"kind": "stimulus", "schemaVersion": 1, "id": "st_test",
            "revision": revision, "lifecycle": {"state": state}, "dependencies": []}


def answer():
    return {"kind": "answer_spec", "schemaVersion": 1, "id": "ast_test", "revision": 1,
            "itemRef": ref("foundation_item", "itm_test"),
            "response": {"interactionKind": "choice", "variant": "single"},
            "sourceBindings": [{"binding": "shared", "ref": ref("stimulus", "st_test")}],
            "answer": {"kind": "choice", "mode": "single",
                       "options": {"a": "A", "b": "B"}, "correctKeys": ["a"],
                       "minSelections": 1, "maxSelections": 1}}


def codes(result):
    return {issue["code"] for issue in result["diagnostics"]}


class WholeAppTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for area, id in IDS.items():
            folder = self.root / area
            folder.mkdir()
            content = {"$schema": "https://json-schema.org/draft/2020-12/schema",
                       "$id": id, "type": "object", "additionalProperties": True,
                       "required": ["kind", "schemaVersion"],
                       "properties": {"kind": {"type": "string"},
                                      "schemaVersion": {"const": 1}}}
            (folder / f"{area}-v1.schema.json").write_text(json.dumps(content))
        self.schemas = WholeAppSchemas(self.root)

    def check(self, records, **kwargs):
        return validate_whole_app(records, schemas=self.schemas, **kwargs)

    def test_complete_exact_revision_graph_passes_contract_validation(self):
        outcome = self.check([stimulus(), item(), answer()])
        self.assertEqual((outcome["status"], outcome["diagnostics"]), ("PASS", []))

    def test_missing_wrong_revision_and_withdrawn_references(self):
        self.assertIn("REFERENCE_MISSING", codes(self.check([item(), answer()])))
        self.assertIn("REFERENCE_REVISION", codes(self.check([stimulus(revision=2), item(), answer()])))
        self.assertIn("DEPENDENCY_WITHDRAWN", codes(self.check([stimulus("withdrawn"), item(), answer()])))
        self.assertEqual(self.check([stimulus(), item(), answer()])["status"], "PASS")

    def test_wrong_type_and_duplicate_canonical_identity(self):
        data = [stimulus(), item(), answer()]
        bad = deepcopy(data)
        bad[1]["sources"][0]["ref"]["targetKind"] = "exam_item"
        self.assertIn("REFERENCE_TYPE", codes(self.check(bad)))
        self.assertIn("IDENTITY_CONFLICT", codes(self.check(data + [deepcopy(data[0])])))

    def test_answer_binding_and_option_cardinality(self):
        data = [stimulus(), item(), answer()]
        bad = deepcopy(data)
        bad[2]["answer"]["correctKeys"] = ["z"]
        self.assertIn("ANSWER_OPTION_MISSING", codes(self.check(bad)))
        bad = deepcopy(data)
        bad[2]["response"]["variant"] = "multiple"
        self.assertIn("ANSWER_RESPONSE_MISMATCH", codes(self.check(bad)))
        bad = deepcopy(data)
        bad[2]["itemRef"]["revision"] = 2
        self.assertIn("REFERENCE_REVISION", codes(self.check(bad)))

    def test_transitive_invalidation_without_unrelated_items(self):
        other = stimulus()
        other["id"] = "st_other"
        dependent = item()
        dependent["dependencies"] = [ref("stimulus", "st_test")]
        dependent["sources"][0]["ref"] = ref("stimulus", "st_other")
        result = self.check([stimulus("withdrawn"), other, dependent, answer()])
        self.assertIn("DEPENDENCY_WITHDRAWN", codes(result))
        self.assertIn("itm_test", result["affected"])
        self.assertNotIn("st_other", result["affected"])

    def test_reject_unresolved_and_remote_schema_refs(self):
        path = self.root / "assessment" / "assessment-v1.schema.json"
        schema = json.loads(path.read_text())
        schema["properties"]["source"] = {"$ref": "https://malicious.example/schema"}
        path.write_text(json.dumps(schema))
        with self.assertRaises(ValueError):
            WholeAppSchemas(self.root)
        schema["properties"]["source"]["$ref"] = "urn:english-content:missing:v1#/$defs/unknown"
        path.write_text(json.dumps(schema))
        with self.assertRaises(ValueError):
            WholeAppSchemas(self.root)

    def test_asset_spec_never_implies_asset_exists(self):
        media = {"kind": "audio_source", "schemaVersion": 1,
                 "sourceBinding": {"binding": "shared", "ref": ref("stimulus", "st_test")},
                 "reviewedText": "Hello.", "transcript": "Hello.",
                 "turns": [{"position": 1, "speakerKey": "A", "text": "Hello."}],
                 "speakers": [{"key": "A"}], "assetState": "not_generated"}
        result = self.check([stimulus(), media], require_eligibility=True)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("MEDIA_NOT_GENERATED", codes(result))
        invalid = deepcopy(media)
        invalid["turns"][0]["speakerKey"] = "Z"
        self.assertIn("MEDIA_SPEAKER_MISSING", codes(self.check([stimulus(), invalid])))

    def test_evidence_unavailable_is_blocked_and_never_pass(self):
        result = self.check([stimulus(), item(), answer()], require_eligibility=True)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("EVIDENCE_UNVERIFIED", codes(result))
        self.assertEqual(self.check([stimulus(), item(), answer()])["status"], "PASS")

    def test_exam_form_bindings_are_exact(self):
        version = {"kind": "exam_version", "schemaVersion": 1,
                   "family": "PTE", "formatVariant": "core", "versionTag": "v1",
                   "formatRef": {"owner": "english", "sourcePath": "x",
                                 "sourceCommit": "a" * 40, "locator": "PTE Core"}}
        binding = {k: version[k] for k in ("family", "formatVariant", "versionTag", "formatRef")}
        blueprint = {"kind": "blueprint", "schemaVersion": 1, "compositionKey": "b",
                     "revision": 1, "examVersion": binding,
                     "completeness": "illustrative_unresolved", "sections": []}
        form = {"kind": "exam_form", "schemaVersion": 1, "formKey": "f",
                "revision": 1, "examVersion": binding,
                "blueprintRef": {"compositionKey": "b", "revision": 2},
                "sections": [], "mediaDependencies": []}
        result = self.check([version, blueprint, form])
        self.assertIn("BLUEPRINT_REVISION", codes(result))
        form["blueprintRef"]["revision"] = 1
        self.assertNotIn("BLUEPRINT_REVISION", codes(self.check([version, blueprint, form])))

    def test_same_batch_candidate_is_not_global_canonical(self):
        data = [stimulus(), item(), answer()]
        data[1]["dependencies"] = [{"scope": "candidate", "targetKind": "sns", "id": "candidate:local"}]
        entry = {"candidateId": "candidate:local", "batchId": "batch-A", "targetKind": "sns"}
        self.assertEqual(self.check(data, batch_id="batch-A", candidate_records=[entry])["status"], "PASS")
        self.assertIn("CANDIDATE_SCOPE_INVALID", codes(self.check(
            data, batch_id="batch-B", candidate_records=[entry])))
        self.assertIn("CANDIDATE_SCOPE_INVALID", codes(self.check(data)))

    def test_non_cyclic_dependency_and_invalid_cycle(self):
        source_a, source_b = stimulus(), stimulus()
        source_b["id"] = "st_other"
        source_a["dependencies"] = [ref("stimulus", "st_other")]
        source_b["dependencies"] = [ref("stimulus", "st_test")]
        self.assertIn("DEPENDENCY_CYCLE", codes(self.check([source_a, source_b])))
        source_b["dependencies"] = []
        self.assertNotIn("DEPENDENCY_CYCLE", codes(self.check([source_a, source_b])))

    def test_external_canonical_revision_is_not_silently_upgraded(self):
        data = [stimulus(), item(), answer()]
        data[1]["dependencies"] = [ref("sns", "sns_test", 2)]
        canonical = {"_id": "sns_test", "revision": 2, "lifecycleStatus": "active"}
        self.assertEqual(self.check(data, canonical_records=[canonical])["status"], "PASS")
        canonical["revision"] = 3
        self.assertIn("REFERENCE_REVISION", codes(self.check(data, canonical_records=[canonical])))

    def test_deep_authority_payload_remains_bounded_and_no_recursion_crash(self):
        record = stimulus()
        current = record
        for _ in range(1200):
            next_value = {}
            current["nested"] = next_value
            current = next_value
        result = self.check([record], require_eligibility=True)
        self.assertEqual(result["status"], "BLOCKED")

    def test_revision_specific_invalidation_does_not_spread_to_other_revision(self):
        source = stimulus("withdrawn")
        item_v1 = item()
        item_v2 = deepcopy(item())
        item_v2["revision"] = 2
        item_v2["sources"] = [{"binding": "shared", "ref": ref("stimulus", "st_other")}]
        other = stimulus()
        other["id"] = "st_other"
        answer_v2 = deepcopy(answer())
        answer_v2["revision"] = 2
        answer_v2["itemRef"]["revision"] = 2
        answer_v2["sourceBindings"] = item_v2["sources"]
        item_v2["answerSpecRef"]["revision"] = 2
        downstream = stimulus()
        downstream["id"] = "st_downstream"
        downstream["dependencies"] = [ref("foundation_item", "itm_test", 2)]
        result = self.check([source, other, item_v1, item_v2, answer(), answer_v2, downstream])
        self.assertEqual(result["status"], "FAIL")
        self.assertNotIn("st_downstream", result["affected"])

    def test_diagnostics_include_stage_and_safely_bounded_explanation(self):
        result = self.check([item()])
        self.assertEqual(result["status"], "FAIL")
        for diagnostic in result["diagnostics"]:
            self.assertTrue({"status", "stage", "code", "entity", "path",
                             "required", "message"}.issubset(diagnostic))
            self.assertLessEqual(len(diagnostic["message"]), 160)

    def test_diagnostic_truncation_is_explicit(self):
        records = []
        for n in range(10):
            candidate = deepcopy(item())
            candidate["id"] = f"itm_{n}"
            records.append(candidate)
        result = self.check(records, limit=2)
        self.assertEqual(len(result["diagnostics"]), 2)
        self.assertTrue(result["truncated"])

    def test_missing_batch_context_cannot_resolve_candidate(self):
        data = [stimulus(), item(), answer()]
        data[1]["dependencies"] = [{"scope": "candidate", "targetKind": "sns", "id": "c"}]
        candidate_record = {"candidateId": "c", "targetKind": "sns"}
        self.assertIn("CANDIDATE_SCOPE_INVALID",
                      codes(self.check(data, candidate_records=[candidate_record])))

    def test_caller_supplied_canonical_malformed_record_fails_closed(self):
        result = self.check([stimulus()], canonical_records=["invalid"])
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("CANONICAL_INVALID", codes(result))

    def test_invalid_records_and_limits_never_succeed(self):
        self.assertIn("SCHEMA_INVALID", codes(self.check([{"kind": "stimulus"}])))
        self.assertIn("INPUT_LIMIT", codes(self.check([stimulus(), item(), answer()], limit=0)))
        self.assertIn("INPUT_LIMIT", codes(self.check([stimulus()] * 10001)))


if __name__ == "__main__":
    unittest.main()
