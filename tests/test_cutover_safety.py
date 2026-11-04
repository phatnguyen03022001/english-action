"""Cutover safety regression checks using synthetic records and isolated filesystem state.

These tests cannot authorize real publication or imply Node parity completeness.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from unittest import mock
import subprocess
import sys
import tempfile
import unittest

from english_action import SchemaDirectory, digest, evaluate_publication
from english_action.cli import _atomic_save, _read_state, _state_lock
from english_action.ledger import blank_state, prepare_batch, promote_candidates
from test_quality import candidate, lex, synth_schemas


class CutoverSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        synth_schemas(self.root)
        self.schemas = SchemaDirectory(self.root)

    def cli(self, operation: str, state: Path, input_path: Path | None = None, *options: str):
        args = [sys.executable, "-m", "english_action", operation, "--schemas", str(self.root),
                "--state", str(state)]
        if input_path:
            args.extend(["--input", str(input_path)])
        return subprocess.run([*args, *options], text=True, capture_output=True, check=False)

    def test_missing_invalid_and_corrupt_state_fail_without_writes(self):
        absent = self.root / "not-created" / "state.json"
        result = self.cli("check", absent)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stdout)["issues"][0]["code"], "STATE_NOT_FOUND")
        self.assertFalse(absent.parent.exists())

        path = self.root / "state.json"
        for payload in ({}, {"records": []}, {"formatVersion": 1, "records": []},
                        {**blank_state(), "history": None}):
            original = json.dumps(payload)
            path.write_text(original)
            result = self.cli("check", path)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["issues"][0]["code"], "STATE_INVALID")
            self.assertEqual(path.read_text(), original)
            self.assertFalse(Path(str(path) + ".lock").exists())

        corrupt = "{bad json"
        path.write_text(corrupt)
        result = self.cli("check", path)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(json.loads(result.stderr)["code"], "STATE_PARSE_ERROR")
        self.assertEqual(path.read_text(), corrupt)

    def test_valid_empty_state_is_read_only_and_not_publishable(self):
        path = self.root / "state.json"
        original = json.dumps(blank_state(), indent=2) + "\n"
        path.write_text(original)
        result = self.cli("check", path)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "valid")
        self.assertTrue(report["storageValid"])
        self.assertFalse(report["publication"]["eligible"])
        self.assertEqual(path.read_text(), original)
        self.assertEqual(sorted(p.name for p in self.root.iterdir() if p.is_file()), ["state.json"])

    def test_failed_atomic_replace_preserves_snapshot_and_retries(self):
        state = self.root / "state.json"
        initial = blank_state()
        initial["history"].append({"action": "synthetic-original"})
        with _state_lock(state):
            _atomic_save(state, initial)
        before = state.read_bytes()
        changed = deepcopy(initial)
        changed["history"].append({"action": "synthetic-next"})
        with mock.patch("english_action.cli.os.replace", side_effect=OSError("injected replacement fault")):
            with self.assertRaisesRegex(OSError, "injected replacement fault"):
                with _state_lock(state):
                    _atomic_save(state, changed)
        self.assertEqual(state.read_bytes(), before)
        self.assertFalse(Path(str(state) + ".lock").exists())
        self.assertEqual(list(self.root.glob("state.json.*.tmp")), [])
        with _state_lock(state):
            _atomic_save(state, changed)
        self.assertEqual(_read_state(state), changed)

    def test_competing_writer_fails_closed_then_retries(self):
        state = self.root / "locked.json"
        input_path = self.root / "batch.json"
        input_path.write_text(json.dumps(candidate()), encoding="utf-8")
        with _state_lock(state):
            contender = self.cli("prepare", state, input_path)
            self.assertEqual(contender.returncode, 1)
            self.assertEqual(json.loads(contender.stderr)["status"], "error")
            self.assertFalse(state.exists())
        result = self.cli("prepare", state, input_path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "prepared")
        self.assertEqual(len(_read_state(state)["history"]), 1)

    def synthetic_authorization(self):
        """Positive proof is valid only inside this isolated test, never a real authority."""
        subject = lex(editorialStatus="verified")
        source = {"_id": "src_01940000-0000-7000-8000-000000000010",
                  "lifecycleStatus": "active", "editorialStatus": "draft",
                  "rights": {"status": "redistributable", "scopes": ["dictionary_publication"],
                             "reviewerId": "synthetic-rights", "permissionReference": "rights-key"}}
        paths = ["/language", "/lemma", "/pos", "/entryKind"]
        evidence = [
            {"_id": f"evd_01940000-0000-7000-8000-{n + 20:012x}",
             "sourceId": source["_id"], "assertionIds": [subject["_id"]],
             "lifecycleStatus": "active", "reviewStatus": "accepted",
             "independence": "independent_reviewed",
             "claim": {"subjectId": subject["_id"], "fieldPath": path,
                       "valueDigest": digest(subject[path[1:]]), "specificity": "exact_value"}}
            for n, path in enumerate(paths)
        ]
        subject["evidenceIds"] = [e["_id"] for e in evidence]
        review = {"_id": "rev_01940000-0000-7000-8000-000000000030",
                  "lifecycleStatus": "active", "outcome": "accepted",
                  "reviewerId": "synthetic-lead", "role": "lead_editorial",
                  "appointmentReference": "lead-key", "subjectIds": [subject["_id"]],
                  "claims": [deepcopy(e["claim"]) for e in evidence]}
        def appointment(key, reviewer, role, identifier, scoped_paths=None):
            return {"appointmentKey": key, "reviewerId": reviewer, "role": role,
                    "appointedByOwnerId": "synthetic-owner", "ownerAttestationRef": "synthetic-only",
                    "effectiveFrom": "2026-01-01T00:00:00Z",
                    "scope": {"subjectIds": [identifier],
                              **({"fieldPaths": scoped_paths} if scoped_paths else {})}}
        trust = {"owner_id": "synthetic-owner", "effective_at": "2026-11-04T00:00:00Z",
                 "appointments": [
                     appointment("lead-key", "synthetic-lead", "lead_editorial", subject["_id"], paths),
                     appointment("rights-key", "synthetic-rights", "rights", source["_id"])],
                 "verify_owner_attestation": lambda **_: True,
                 "verify_evidence": lambda **_: True,
                 "verify_rights": lambda **_: True}
        return [subject, source, *evidence, review], trust

    def test_synthetic_positive_authorization_is_separate_from_migration_readiness(self):
        records, trust = self.synthetic_authorization()
        report = evaluate_publication(records, trust)
        self.assertEqual([i["code"] for i in report["issues"]], ["QUALITY_PARITY_NOT_VERIFIED"])
        self.assertFalse(report["eligible"], "Synthetic authority must never enable real publication")
        state, prepared = prepare_batch(candidate(), {}, self.schemas)
        self.assertEqual(prepared["status"], "prepared")
        subject = state["records"][0]
        requested = {"promotionId": "cutover-synthetic", "subjects": [
            {"canonicalId": subject["_id"], "expectedRevision": 1}]}
        unchanged, result = promote_candidates(state, requested, self.schemas, trust=trust)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("CLAIM_PATH_UNVERIFIED", [i["code"] for i in result["issues"]])
        self.assertNotIn("PUBLICATION_GATE_FAILED", [i["code"] for i in result["issues"]])
        self.assertEqual(unchanged, state)

    def test_synthetic_revocation_scope_and_unavailable_attestations_are_denied(self):
        records, trust = self.synthetic_authorization()
        cases = []
        revoked = deepcopy(records)
        revoked[1]["rights"]["revokedAt"] = "2026-11-03T00:00:00Z"
        cases.append((revoked, trust, "SOURCE_RIGHTS_UNVERIFIED"))
        wrong_scope = deepcopy(trust)
        wrong_scope["appointments"][0]["scope"]["fieldPaths"] = ["/lemma"]
        cases.append((records, wrong_scope, "CLAIM_PATH_UNVERIFIED"))
        unverified = deepcopy(trust)
        unverified["verify_owner_attestation"] = lambda **_: False
        cases.append((records, unverified, "LER_AUTHORITY_UNVERIFIED"))
        unavailable = deepcopy(trust)
        def broken(**_):
            raise ConnectionError("synthetic attestation service offline")
        unavailable["verify_rights"] = broken
        cases.append((records, unavailable, "SOURCE_RIGHTS_UNVERIFIED"))
        false_evidence = deepcopy(trust)
        false_evidence["verify_evidence"] = lambda **_: False
        cases.append((records, false_evidence, "CLAIM_EVIDENCE_INSUFFICIENT"))
        for sample, provider, required_code in cases:
            with self.subTest(code=required_code):
                report = evaluate_publication(sample, provider)
                self.assertFalse(report["eligible"])
                self.assertIn(required_code, [i["code"] for i in report["issues"]])
                self.assertIn("QUALITY_PARITY_NOT_VERIFIED", [i["code"] for i in report["issues"]])



    def test_scoped_sense_publication_follows_inbound_construction_and_requires_approval(self):
        root = lex(editorialStatus="verified")
        sense = {"_id": "sns_01940000-0000-7000-8000-000000000121",
                 "schemaVersion": "2.0.0", "lifecycleStatus": "active",
                 "editorialStatus": "verified", "entryId": root["_id"],
                 "definition": "Synthetic meaning"}
        con = {"_id": "con_01940000-0000-7000-8000-000000000122",
               "schemaVersion": "2.0.0", "lifecycleStatus": "active",
               "editorialStatus": "draft", "senseId": sense["_id"],
               "pattern": "synthetic"}
        other = lex("unrelated", idx="000000000123", editorialStatus="disputed")
        report = evaluate_publication([root, sense, con, other],
                                      {"subject_ids": [sense["_id"]]})
        self.assertFalse(report["eligible"])
        self.assertTrue(any(i["entityId"] == con["_id"] and i["code"] == "PUBLICATION_NOT_VERIFIED"
                            for i in report["issues"]))
        self.assertFalse(any(i["entityId"] == other["_id"] for i in report["issues"]))

    def test_lifecycle_publication_never_trusts_self_declared_editorial_decision(self):
        root = lex(editorialStatus="verified")
        decision = {"_id": "dec_01940000-0000-7000-8000-000000000124",
                    "lifecycleStatus": "active", "editorialStatus": "draft",
                    "decisionEffect": "lifecycle_transition",
                    "subjectIds": [root["_id"]], "reviewerId": "unappointed",
                    "appointmentReference": "unverified"}
        report = evaluate_publication([root, decision], {"subject_ids": [root["_id"]]})
        codes = [i["code"] for i in report["issues"]]
        self.assertIn("LIFECYCLE_AUDIT_INCOMPLETE", codes)
        self.assertIn("LIFECYCLE_AUTHORITY_UNVERIFIED", codes)
        self.assertFalse(report["eligible"])

if __name__ == "__main__":
    unittest.main()
