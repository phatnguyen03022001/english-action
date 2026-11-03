"""Independent synthetic regression cases, including negative and distrust paths."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from english_action import SchemaDirectory, digest, evaluate_publication, validate_dataset
from english_action.contracts import MISSING, js_json, reviewer_appointed, value_at
from english_action.ledger import bind_evidence, prepare_batch, revise_candidates, promote_candidates
from test_quality import candidate, lex, synth_schemas


class EdgeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        synth_schemas(self.base)
        self.schemas = SchemaDirectory(self.base)

    def test_node_json_stringify_oracle_for_public_synthetic_values(self):
        values = [{"lemma": "tiếng", "pos": "NOUN"}, {"b": [1, None, True], "a": {"x": "café"}},
                  {"10": "a", "2": "b", "z": 1}, [1.0, 0.000001, 1.2], {"v": "é/\n\\", "n": -0.0}]
        script = "const crypto=require('crypto'); const vals=" + json.dumps(values, ensure_ascii=False) + "; process.stdout.write(JSON.stringify(vals.map(v=>crypto.createHash('sha256').update(JSON.stringify(v)).digest('hex'))));"
        try:
            result = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
        except FileNotFoundError:
            self.skipTest("Node unavailable: committed SHA-256 vectors remain mandatory")
        self.assertEqual([digest(v) for v in values], json.loads(result.stdout))

    def test_malformed_schema_and_unresolved_network_reference_denied(self):
        entry = self.base / "v2" / "lexical-entry.schema.json"
        schema = json.loads(entry.read_text())
        schema["properties"]["lemma"] = {"$ref": "https://unavailable.invalid/foreign.json"}
        entry.write_text(json.dumps(schema))
        schemas = SchemaDirectory(self.base)
        result = validate_dataset([lex()], schemas)
        self.assertFalse(result["valid"])
        self.assertEqual(result["stage"], "schema")
        self.assertEqual(result["issues"][0]["code"], "SCHEMA_INVALID")

    def test_v1_schemas_validate_but_never_authorize_publication(self):
        schemas = SchemaDirectory(self.base, "v1")
        example = lex(); example["schemaVersion"] = "1.0.0"
        valid = validate_dataset([example], schemas, version="v1")
        self.assertTrue(valid["valid"], valid)
        self.assertFalse(valid["publication"]["eligible"])

    def test_bad_reference_and_retired_reference(self):
        parent = lex()
        sense = {"_id": "sns_01940000-0000-7000-8000-000000000002", "schemaVersion": "2.0.0", "lifecycleStatus": "active", "editorialStatus": "draft", "entryId": "lex_01940000-0000-7000-8000-000000000099"}
        got = validate_dataset([parent, sense], self.schemas)
        self.assertIn("REFERENCE_NOT_FOUND", [x["code"] for x in got["issues"]])
        sense["entryId"] = parent["_id"]
        parent["lifecycleStatus"] = "retired"
        got = validate_dataset([parent, sense], self.schemas)
        self.assertIn("RETIRED_LIVE_REFERENCE", [x["code"] for x in got["issues"]])

    def test_cycle_and_duplicate_edges(self):
        a = {"_id": "top_01940000-0000-7000-8000-000000000001", "schemaVersion": "2.0.0", "lifecycleStatus": "active", "editorialStatus": "draft"}
        b = deepcopy(a); b["_id"] = b["_id"][:-1] + "2"
        a["parentTopicId"] = b["_id"]; b["parentTopicId"] = a["_id"]
        issues = validate_dataset([a, b], self.schemas)["issues"]
        self.assertIn("CYCLE_DETECTED", [x["code"] for x in issues])
        left = lex(); right = lex("other", idx="000000000003")
        rel = {"_id": "rel_01940000-0000-7000-8000-000000000004", "schemaVersion": "2.0.0", "lifecycleStatus": "active", "editorialStatus": "draft", "sourceId": left["_id"], "targetId": right["_id"], "relationType": "SYNONYM_OF"}
        duplicate = deepcopy(rel); duplicate["_id"] = duplicate["_id"][:-1] + "5"
        self.assertIn("DUPLICATE_SEMANTIC_EDGE", [x["code"] for x in validate_dataset([left, right, rel, duplicate], self.schemas)["issues"]])

    def test_claim_pointer_null_and_escaping(self):
        self.assertIsNone(value_at({"a/b": {"~": None}}, "/a~1b/~0"))
        self.assertIs(value_at({"a": [1]}, "/a/01"), MISSING)
        self.assertIs(value_at({"a": [1]}, "/a/2"), MISSING)

    def test_stale_or_forged_reviewer_attestation(self):
        subject = lex()
        appointment = {"appointmentKey": "a", "reviewerId": "editor", "role": "lead_editorial", "appointedByOwnerId": "owner", "ownerAttestationRef": "fake", "scope": {"subjectIds": [subject["_id"]], "fieldPaths": ["/lemma"]}, "effectiveFrom": "2026-01-01T00:00:00Z", "revokedAt": "2026-10-01T00:00:00Z"}
        opts = {"appointments": [appointment], "owner_id": "owner", "effective_at": "2026-11-04T01:00:00Z", "verify_owner_attestation": lambda **_kw: True}
        self.assertFalse(reviewer_appointed(opts, "editor", "lead_editorial", "a", subject["_id"], "/lemma"))
        appointment.pop("revokedAt")
        self.assertFalse(reviewer_appointed(opts, "editor", "lead_editorial", "a", subject["_id"], "/pos"))
        self.assertTrue(reviewer_appointed(opts, "editor", "lead_editorial", "a", subject["_id"], "/lemma"))
        opts["verify_owner_attestation"] = lambda **_kw: (_ for _ in ()).throw(RuntimeError("auth offline"))
        self.assertFalse(reviewer_appointed(opts, "editor", "lead_editorial", "a", subject["_id"], "/lemma"))

    def test_diagnostics_stable_and_capped(self):
        items = [lex(idx=f"{i:012x}", editorialStatus="disputed") for i in range(1, 5)]
        a = evaluate_publication(items, limit=3)
        b = evaluate_publication(list(reversed(items)), limit=3)
        self.assertEqual(a, b)
        self.assertFalse(a["eligible"])
        self.assertEqual(len(a["issues"]), 3)
        self.assertGreater(a["omittedIssueCount"], 0)
        self.assertIn("QUALITY_PARITY_NOT_VERIFIED", [x["code"] for x in evaluate_publication(items)["issues"]])

    def test_preparation_denies_embedded_identity_and_claimed_verification(self):
        x = candidate()
        x["records"][0]["entity"]["_id"] = lex()["_id"]
        self.assertEqual(prepare_batch(x, {}, self.schemas)[1]["issues"][0]["code"], "CANONICAL_ID_IN_CANDIDATE")
        x["records"][0]["entity"].pop("_id")
        x["records"][0]["entity"]["editorialStatus"] = "verified"
        self.assertEqual(prepare_batch(x, {}, self.schemas)[1]["issues"][0]["code"], "SELF_VERIFICATION_FORBIDDEN")

    def test_unresolved_candidate_reference_never_persists(self):
        x = candidate()
        x["records"][0]["entity"]["topicIds"] = ["candidate:nope"]
        state, report = prepare_batch(x, {}, self.schemas)
        self.assertEqual(report["issues"][0]["code"], "UNRESOLVED_REFERENCE")
        self.assertEqual(state, {})

    def test_conflicting_source_key_on_different_batch(self):
        state, _ = prepare_batch(candidate(), {}, self.schemas)
        next_request = candidate("wrong", batch="second")
        self.assertEqual(prepare_batch(next_request, state, self.schemas)[1]["issues"][0]["code"], "SOURCE_ITEM_CHANGED")

    def test_promotion_dry_run_cannot_bypass_authority(self):
        state, _ = prepare_batch(candidate(), {}, self.schemas)
        ref = state["records"][0]["_id"]
        req = {"promotionId": "p", "subjects": [{"canonicalId": ref, "expectedRevision": 1}]}
        _, response = promote_candidates(state, req, self.schemas, dry_run=True, trust={"verify_rights": lambda **_: True, "verify_evidence": lambda **_: True})
        self.assertEqual(response["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
