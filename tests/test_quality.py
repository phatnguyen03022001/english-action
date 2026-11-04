"""Public synthetic-only behavioral and security regression tests."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

from english_action import SchemaDirectory, digest, evaluate_publication, validate_dataset
from english_action.cli import _path_safe, _read_state, _state_lock, _atomic_save
from english_action.contracts import PREFIXES, reviewer_appointed, value_at, MISSING, js_json
from english_action.ledger import (blank_state, bind_evidence, prepare_batch,
                                  promote_candidates, revise_candidates, uuidv7)


def synth_schemas(root: Path):
    for version in ("v1", "v2"):
        folder = root / version
        folder.mkdir(parents=True)
        for prefix, name in PREFIXES.items():
            required = ["_id", "schemaVersion", "lifecycleStatus", "editorialStatus"]
            if prefix == "lex":
                required += ["language", "lemma", "pos", "entryKind"]
            schema = {"$schema": "https://json-schema.org/draft/2020-12/schema",
                      "$id": f"https://synthetic.invalid/{version}/{name}.schema.json",
                      "type": "object", "required": required,
                      "properties": {"_id": {"type": "string", "pattern": rf"^{prefix}_[0-9a-f]{{8}}-[0-9a-f]{{4}}-7[0-9a-f]{{3}}-[89ab][0-9a-f]{{3}}-[0-9a-f]{{12}}$"},
                                     "schemaVersion": {"const": "2.0.0" if version == "v2" else "1.0.0"},
                                     "lifecycleStatus": {"enum": ["active", "retired"]},
                                     "editorialStatus": {"enum": ["draft", "verified", "disputed", "rejected"]},
                                     "lemma": {"type": "string"}, "language": {"type": "string"},
                                     "pos": {"enum": ["NOUN", "VERB"]}, "entryKind": {"type": "string"}}}
            (folder / f"{name}.schema.json").write_text(json.dumps(schema), encoding="utf-8")
        if version == "v1":
            # Public synthetic vocabulary: only metadata needed by isolated tests.
            vocabulary = {
                "identifiers": {"activeV1Prefixes": list(PREFIXES)},
                "relationTypes": [
                    {"type": "DERIVED_FROM", "sourceType": "LexicalEntry",
                     "targetType": "LexicalEntry", "symmetric": False, "cyclePolicy": "reject"},
                    {"type": "HYPERNYM_OF", "sourceType": "LexicalSense",
                     "targetType": "LexicalSense", "symmetric": False, "cyclePolicy": "reject"},
                    {"type": "SYNONYM_OF", "sourceType": "LexicalSense",
                     "targetType": "LexicalSense", "symmetric": True, "cyclePolicy": "allow"},
                ],
                "relationModel": {"qualifierFieldsInOrder": ["context", "register"]},
                "retirementRequirements": {"semantic_merge": {"retiredSourceMin": 2}},
            }
            (folder / "vocabularies.json").write_text(json.dumps(vocabulary), encoding="utf-8")


def lex(lemma="tiếng", *, idx="000000000001", **additional):
    return {"_id": "lex_01940000-0000-7000-8000-" + idx, "schemaVersion": "2.0.0", "language": "vi", "lemma": lemma,
            "pos": "NOUN", "entryKind": "word", "lifecycleStatus": "active", "editorialStatus": "draft", **additional}


def candidate(lemma="tiếng", *, batch="first"):
    item = lex(lemma)
    item.pop("_id"); item.pop("schemaVersion")
    return {"batchId": batch, "records": [{"candidateId": "a", "sourceItemKey": "source/001", "entity": item}]}


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        synth_schemas(self.root)
        self.schemas = SchemaDirectory(self.root)

    def test_v1_wrong_type_and_cycle_regressions(self):
        schemas = SchemaDirectory(self.root, "v1")
        def entity(prefix, n, **fields):
            return {"_id": f"{prefix}_01940000-0000-7000-8000-{n:012x}",
                    "schemaVersion": "1.0.0", "lifecycleStatus": "active",
                    "editorialStatus": "draft", **fields}
        first, second = entity("top", 1), entity("top", 2)
        first["parentTopicId"] = second["_id"]
        second["parentTopicId"] = first["_id"]
        cycles = validate_dataset([first, second], schemas, version="v1")["issues"]
        self.assertEqual([x["code"] for x in cycles], ["CYCLE_DETECTED"])
        self.assertEqual(cycles[0]["path"], "TOPIC_PARENT")
        first["parentTopicId"] = first["_id"]
        second.pop("parentTopicId")
        self.assertEqual(len([x for x in validate_dataset([first, second], schemas, version="v1")["issues"]
                              if x["code"] == "CYCLE_DETECTED"]), 1)
        sense = entity("sns", 3, entryId=first["_id"])
        mismatch = validate_dataset([first, second, sense], schemas, version="v1")["issues"]
        self.assertTrue(any(x["code"] == "REFERENCE_TYPE_MISMATCH" and
                            x["message"] == "Expected lex, found top" for x in mismatch))
        entry_a = entity("lex", 4, language="en", lemma="alpha", pos="NOUN", entryKind="word")
        entry_b = entity("lex", 5, language="en", lemma="beta", pos="NOUN", entryKind="word")
        rel_a = entity("rel", 6, sourceId=entry_a["_id"], targetId=entry_b["_id"], relationType="DERIVED_FROM")
        rel_b = entity("rel", 7, sourceId=entry_b["_id"], targetId=entry_a["_id"], relationType="DERIVED_FROM")
        self.assertEqual(len([x for x in validate_dataset([entry_a, entry_b, rel_a, rel_b], schemas, version="v1")["issues"]
                              if x["code"] == "CYCLE_DETECTED"]), 1)

    def test_v1_unicode_qualifiers_and_publication_diagnostics(self):
        schemas = SchemaDirectory(self.root, "v1")
        def item(prefix, n, **fields):
            return {"_id": f"{prefix}_01940000-0000-7000-8000-{n:012x}",
                    "schemaVersion": "1.0.0", "lifecycleStatus": "active",
                    "editorialStatus": "draft", **fields}
        entry = item("lex", 1, language="en", lemma="synthetic", pos="NOUN", entryKind="word")
        sense_a = item("sns", 2, entryId=entry["_id"])
        sense_b = item("sns", 3, entryId=entry["_id"])
        rel_a = item("rel", 4, relationType="SYNONYM_OF", sourceId=sense_a["_id"],
                     targetId=sense_b["_id"], qualifiers={"context": "café"})
        rel_b = item("rel", 5, relationType="SYNONYM_OF", sourceId=sense_a["_id"],
                     targetId=sense_b["_id"], qualifiers={"context": "cafe\u0301"})
        result = validate_dataset([entry, sense_a, sense_b, rel_a, rel_b], schemas, version="v1")
        self.assertEqual([x["code"] for x in result["issues"]], ["DUPLICATE_SEMANTIC_EDGE"])
        verified = dict(entry, editorialStatus="verified")
        pub = validate_dataset([verified], schemas, version="v1", publication=True)
        self.assertFalse(pub["valid"])
        self.assertEqual([x["code"] for x in pub["issues"]],
                         ["INSUFFICIENT_EVIDENCE", "PUBLICATION_REVIEW_MISSING",
                          "PUBLICATION_EVIDENCE_MISSING"])
        self.assertEqual(len(pub["publication"]["issues"]), 3)
        self.assertFalse(pub["publication"]["eligible"])

    def test_v1_schema_pattern_matches_ajv_message(self):
        path = self.root / "v1" / "lexical-sense.schema.json"
        definition = json.loads(path.read_text(encoding="utf-8"))
        definition["properties"]["entryId"] = {"type": "string", "pattern": "^lex_"}
        path.write_text(json.dumps(definition), encoding="utf-8")
        schemas = SchemaDirectory(self.root, "v1")
        sense = {"_id": "sns_01940000-0000-7000-8000-000000000002",
                 "schemaVersion": "1.0.0", "lifecycleStatus": "active",
                 "editorialStatus": "draft",
                 "entryId": "sns_01940000-0000-7000-8000-000000000001"}
        result = validate_dataset([sense], schemas, version="v1")
        self.assertEqual(result["stage"], "schema")
        self.assertEqual(result["issues"][0]["message"], 'pattern: must match pattern "^lex_"')

    def test_authenticated_synthetic_claims_reach_only_migration_guard(self):
        # This test authenticates a synthetic domain decision, NEVER a real publication.
        subject = lex(editorialStatus="verified")
        source = {"_id": "src_01940000-0000-7000-8000-000000000010",
                  "lifecycleStatus": "active", "editorialStatus": "draft",
                  "rights": {"status": "redistributable", "scopes": ["dictionary_publication"],
                             "reviewerId": "rights-reviewer", "permissionReference": "rights-appointment"}}
        paths = ["/language", "/lemma", "/pos", "/entryKind"]
        evidence = []
        for n, path in enumerate(paths):
            evidence.append({"_id": f"evd_01940000-0000-7000-8000-{n+20:012x}",
                             "sourceId": source["_id"], "assertionIds": [subject["_id"]],
                             "lifecycleStatus": "active", "reviewStatus": "accepted",
                             "independence": "independent_reviewed",
                             "claim": {"subjectId": subject["_id"], "fieldPath": path,
                                       "valueDigest": digest(value_at(subject, path)),
                                       "specificity": "exact_value"}})
        subject["evidenceIds"] = [item["_id"] for item in evidence]
        review = {"_id": "rev_01940000-0000-7000-8000-000000000030",
                  "lifecycleStatus": "active", "outcome": "accepted",
                  "reviewerId": "lead", "role": "lead_editorial",
                  "appointmentReference": "lead-appointment", "subjectIds": [subject["_id"]],
                  "claims": [item["claim"] for item in evidence]}
        def appointment(key, reviewer, role, subject_id, field_paths=None):
            return {"appointmentKey": key, "reviewerId": reviewer, "role": role,
                    "appointedByOwnerId": "owner", "ownerAttestationRef": "synthetic-test",
                    "effectiveFrom": "2026-01-01T00:00:00Z",
                    "scope": {"subjectIds": [subject_id],
                              **({"fieldPaths": field_paths} if field_paths else {})}}
        trust = {"owner_id": "owner", "effective_at": "2026-11-04T00:00:00Z",
                 "appointments": [appointment("lead-appointment", "lead", "lead_editorial", subject["_id"], paths),
                                  appointment("rights-appointment", "rights-reviewer", "rights", source["_id"])],
                 "verify_owner_attestation": lambda **kwargs: True,
                 "verify_evidence": lambda **kwargs: True,
                 "verify_rights": lambda **kwargs: True}
        records = [subject, source, *evidence, review]
        ok = evaluate_publication(records, trust)
        self.assertFalse(ok["eligible"])
        self.assertEqual([x["code"] for x in ok["issues"]], ["QUALITY_PARITY_NOT_VERIFIED"])
        self.assertEqual(ok["issueCount"], 1)
        source["rights"]["revokedAt"] = "2026-11-03T00:00:00Z"
        revoked = evaluate_publication(records, trust)
        self.assertIn("SOURCE_RIGHTS_UNVERIFIED", [x["code"] for x in revoked["issues"]])
        self.assertFalse(revoked["eligible"])
        source["rights"].pop("revokedAt")
        trust["verify_owner_attestation"] = lambda **kwargs: False
        invalid = evaluate_publication(records, trust)
        self.assertIn("LER_AUTHORITY_UNVERIFIED", [x["code"] for x in invalid["issues"]])
        self.assertFalse(invalid["eligible"])

    def test_nullable_topic_parent_is_not_a_reference(self):
        from english_action.contracts import graph_issues
        topic = {"_id": "top_01940000-0000-7000-8000-000000000001",
                 "schemaVersion": "2.0.0", "lifecycleStatus": "active",
                 "editorialStatus": "draft", "parentTopicId": None}
        self.assertEqual(graph_issues([topic]), [])

    def test_js_digest_golden_vectors(self):
        self.assertEqual(digest({"lemma": "tiếng", "pos": "NOUN"}), "a3c9f60a2e7ee14bbb734c9794908e2434afb3c3331351800c37e0d903699b1f")
        self.assertEqual(js_json({"10": "a", "2": "b", "z": 1}), '{"2":"b","10":"a","z":1}')
        self.assertEqual(js_json([1.0, 0.000001, 1.2]), '[1,0.000001,1.2]')

    def test_json_digest_large_integer_uses_javascript_double_semantics(self):
        self.assertEqual(js_json([10**20, 10**21]), '[100000000000000000000,1e+21]')
        self.assertEqual(js_json(9007199254740993), '9007199254740992')
        self.assertEqual(digest({"number": 10**20}),
                         hashlib.sha256(b'{"number":100000000000000000000}').hexdigest())

    def test_conflict_report_preserves_input_digest_for_node_compatibility(self):
        bad = candidate(batch="not allowed spaces")
        _, report = prepare_batch(bad, {}, self.schemas, dry_run=True)
        self.assertEqual(report["status"], "conflict")
        self.assertEqual(report["issues"][0]["code"], "BATCH_INVALID")
        self.assertEqual(report["digest"], digest(bad))

    def test_uuidv7_identity_and_nfc_replay(self):
        self.assertRegex(uuidv7(), r"^[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
        first, report = prepare_batch(candidate("tie\u0302\u0301ng"), {}, self.schemas)
        self.assertEqual(report["status"], "prepared", report)
        self.assertEqual(first["records"][0]["lemma"], "tiếng")
        replay, response = prepare_batch(candidate("tiếng"), first, self.schemas)
        self.assertEqual(response["status"], "replayed", response)
        self.assertEqual(first, replay)

    def test_validation_and_failed_replay_are_immutable(self):
        bad = candidate()
        bad["records"][0]["entity"]["pos"] = "INVALID"
        original = {}
        state, response = prepare_batch(bad, original, self.schemas, dry_run=True)
        self.assertEqual(response["status"], "conflict", response)
        self.assertEqual(response["issues"][0]["code"], "VALIDATION_FAILED")
        self.assertEqual(state, original)
        state, response = prepare_batch(candidate(), {}, self.schemas)
        self.assertEqual(response["status"], "prepared")
        changed = candidate("khác")
        unchanged, response = prepare_batch(changed, state, self.schemas)
        self.assertEqual(response["issues"][0]["code"], "CHANGED_BATCH_CONFLICT")
        self.assertEqual(unchanged, state)

    def test_dry_run_is_deterministic_and_no_ledger_write(self):
        a = candidate()
        first, ra = prepare_batch(a, {}, self.schemas, dry_run=True)
        second, rb = prepare_batch(a, {}, self.schemas, dry_run=True)
        self.assertEqual(first, {})
        self.assertEqual(ra, rb)
        self.assertEqual(ra["status"], "dry-run")
        self.assertNotIn("recordIds", ra)

    def test_schema_graph_and_evidence_digest(self):
        r = lex()
        self.assertTrue(validate_dataset([r], self.schemas)["valid"])
        self.assertFalse(validate_dataset([r, r], self.schemas)["valid"])
        missing = deepcopy(r); missing["pos"] = "NOT_A_POS"
        self.assertEqual(validate_dataset([missing], self.schemas)["stage"], "schema")
        ev = {"_id": "evd_01940000-0000-7000-8000-000000000002", "schemaVersion": "2.0.0", "lifecycleStatus": "active", "editorialStatus": "draft", "sourceId": "src_01940000-0000-7000-8000-000000000003", "assertionIds": [r["_id"]], "claim": {"subjectId": r["_id"], "fieldPath": "/lemma", "valueDigest": digest("not the claim"), "specificity": "exact_value"}}
        src = {"_id": ev["sourceId"], "schemaVersion": "2.0.0", "lifecycleStatus": "active", "editorialStatus": "draft", "title": "synthetic"}
        val = validate_dataset([r, ev, src], self.schemas)
        self.assertFalse(val["valid"])
        self.assertIn("EVIDENCE_CLAIM_MISMATCH", [i["code"] for i in val["issues"]])
        ev["claim"]["valueDigest"] = digest("tiếng")
        self.assertTrue(validate_dataset([r, ev, src], self.schemas)["valid"])
        self.assertEqual(value_at(r, "/nonexistent"), MISSING)

    def test_revise_cas_history_forbidden_fields(self):
        state, _ = prepare_batch(candidate(), {}, self.schemas)
        entity = state["records"][0]
        req = {"revisionId": "fix-1", "changes": [{"canonicalId": entity["_id"], "expectedRevision": 1, "set": {"usageNote": "synthetic"}}]}
        updated, report = revise_candidates(state, req, self.schemas)
        self.assertEqual(report["status"], "revised", report)
        self.assertEqual(updated["records"][0]["revision"], 2)
        self.assertEqual(updated["history"][-1]["before"][0]["_id"], entity["_id"])
        self.assertEqual(updated["history"][-1]["after"][0]["usageNote"], "synthetic")
        replay, rep = revise_candidates(updated, req, self.schemas)
        self.assertEqual(rep["status"], "replayed")
        self.assertEqual(replay, updated)
        stale = deepcopy(req); stale["revisionId"] = "fix-2"
        self.assertEqual(revise_candidates(updated, stale, self.schemas)[1]["issues"][0]["code"], "REVISION_CAS_CONFLICT")
        forbidden = deepcopy(req); forbidden["revisionId"] = "fix-3"; forbidden["changes"][0]["set"] = {"_id": "inject"}
        self.assertEqual(revise_candidates(state, forbidden, self.schemas)[1]["issues"][0]["code"], "REVISION_FIELD_FORBIDDEN")

    def test_evidence_binding_and_stale_digest(self):
        state, _ = prepare_batch(candidate(), {}, self.schemas)
        subject = state["records"][0]
        evidence_id = "evd_01940000-0000-7000-8000-000000000004"
        source_id = "src_01940000-0000-7000-8000-000000000005"
        state["records"].extend([{"_id": source_id, "schemaVersion": "2.0.0", "lifecycleStatus": "active", "editorialStatus": "draft", "title": "Synthetic source"},
                                 {"_id": evidence_id, "schemaVersion": "2.0.0", "lifecycleStatus": "active", "editorialStatus": "draft", "sourceId": source_id, "assertionIds": [subject["_id"]], "claim": {"subjectId": subject["_id"], "fieldPath": "/lemma", "valueDigest": digest("tiếng"), "specificity": "exact_value"}}])
        request = {"bindingId": "bind-1", "subjectId": subject["_id"], "expectedRevision": 1, "evidenceIds": [evidence_id]}
        state2, report = bind_evidence(state, request, self.schemas)
        self.assertEqual(report["status"], "bound", report)
        self.assertEqual(state2["records"][0]["evidenceIds"], [evidence_id])
        self.assertEqual(bind_evidence(state2, request, self.schemas)[1]["status"], "replayed")
        stale = deepcopy(request); stale["bindingId"] = "bind-2"
        self.assertEqual(bind_evidence(state2, stale, self.schemas)[1]["issues"][0]["code"], "EVIDENCE_BINDING_CAS_CONFLICT")
        self.assertEqual(len(state2["history"]), 2)

    def test_publication_denies_forged_claims_and_promotion(self):
        r = lex(editorialStatus="verified")
        response = evaluate_publication([r])
        self.assertFalse(response["eligible"])
        self.assertIn("LER_AUTHORITY_UNVERIFIED", [i["code"] for i in response["issues"]])
        self.assertFalse(reviewer_appointed({"appointments": [{"reviewerId": "forged", "role": "lead_editorial"}]}, "forged", "lead_editorial", "forged", r["_id"]))
        state, _ = prepare_batch(candidate(), {}, self.schemas)
        subject_id = state["records"][0]["_id"]
        before, report = promote_candidates(state, {"promotionId": "promote-1", "subjects": [{"canonicalId": subject_id, "expectedRevision": 1}]}, self.schemas)
        self.assertEqual(report["status"], "blocked", report)
        self.assertEqual(before, state)

    def test_cli_and_state_file_safety(self):
        work = self.root / "workspace"; work.mkdir(mode=0o700)
        state = work / ".draft" / "state.json"
        cmd = [sys.executable, "-m", "english_action", "prepare", "--schemas", str(self.root), "--state", str(state), "--input", str(work / "batch.json")]
        (work / "batch.json").write_text(json.dumps(candidate()), encoding="utf-8")
        env = os.environ | {"PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        run = subprocess.run(cmd, cwd=work, env=env, text=True, capture_output=True)
        self.assertEqual(run.returncode, 0, run.stderr + run.stdout)
        self.assertEqual(json.loads(run.stdout)["status"], "prepared")
        self.assertEqual(stat_mode(state.parent), 0o700)
        self.assertTrue(_read_state(state)["records"])
        check = subprocess.run([sys.executable, "-m", "english_action", "check", "--schemas", str(self.root), "--state", str(state)], cwd=work, env=env, text=True, capture_output=True)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertEqual(json.loads(check.stdout)["status"], "valid")
        # A state-file symlink is rejected even before JSON parsing.
        fake = work / "shortcut.json"
        fake.symlink_to(state)
        with self.assertRaises(ValueError):
            _path_safe(str(fake))
        lock = Path(str(state) + ".lock")
        with _state_lock(state):
            self.assertTrue(lock.exists())
            blocked = subprocess.run(cmd, cwd=work, env=env, text=True, capture_output=True)
            self.assertEqual(blocked.returncode, 1)
        self.assertFalse(lock.exists())

    def test_missing_schemas_fails_closed(self):
        (self.root / "v2" / "lexical-entry.schema.json").unlink()
        with self.assertRaises(ValueError):
            SchemaDirectory(self.root)


def stat_mode(path):
    import stat
    return stat.S_IMODE(path.stat().st_mode)


if __name__ == "__main__":
    unittest.main()
