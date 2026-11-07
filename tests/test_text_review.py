"""Synthetic persisted text handoff; no reviewer or rights authority."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from english_action.content.spawn import SyntheticWorker, generate, persist, prepare_text_review, _hash
from test_spawn import batch, seed


class TextReviewTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = str(Path(self.folder.name) / "state.json")

    def record(self, value):
        generated = asyncio.run(generate(value, SyntheticWorker(), retries=0))
        persist(value, generated, self.path, {"status": "NOT_VERIFIED"})
        state = json.loads(Path(self.path).read_text())
        return state, generated[0]["candidateId"]

    def sample(self, recipe="K2-21"):
        value = batch([seed(recipe)])
        value["seeds"][0]["request"] = {
            "objective": "Solve the synthetic task", "construct": "synthetic construct",
            "responseBranch": "choice" if recipe == "K2-21" else "type_in",
            "constraints": ["Respect the displayed context"],
        }
        return value

    def test_packet_has_complete_stimulus_constraints_and_no_solution(self):
        expected = {"N2-06": {"context", "prompt"},
                    "K2-21": {"passage", "question", "alternatives"},
                    "N2-10": {"context", "tokens", "constraints"}}
        for recipe, fields in expected.items():
            with self.subTest(recipe=recipe):
                value = self.sample(recipe)
                value["batchId"] = recipe
                value["seeds"][0]["seedId"] = recipe
                state, identifier = self.record(value)
                handoff = prepare_text_review(state, value, identifier)
                packet = handoff["blindPacket"]
                item = state["candidates"][identifier]
                self.assertEqual(packet["candidateId"], identifier)
                self.assertEqual(packet["candidateRevision"], item["revision"])
                self.assertEqual(packet["contentHash"], item["contentHash"])
                self.assertEqual(set(packet["stimulus"]), fields)
                self.assertEqual(packet["responseConstraints"], value["seeds"][0]["request"]["constraints"])
                self.assertFalse({"key", "answerKey", "mainIdea", "rationale", "explanation",
                                  "acceptedOrderings"} & packet["stimulus"].keys())
                if recipe == "K2-21":
                    self.assertEqual(packet["stimulus"]["alternatives"], item["content"]["alternatives"])
                if recipe == "N2-06":
                    self.assertNotIn("alternatives", packet["stimulus"])
                self.assertFalse(handoff["mechanicalEvidence"]["publicationEligible"])

    def test_revision_is_persisted_not_expected_plus_one_after_noop_cas(self):
        value = self.sample()
        state, identifier = self.record(value)
        unchanged = deepcopy(value)
        unchanged["batchId"] = "unchanged"
        unchanged["seeds"][0]["expectedRevision"] = 1
        unchanged["seeds"][0]["request"]["unusedMetadata"] = "no content change"
        state, _ = self.record(unchanged)
        self.assertEqual(state["runs"]["unchanged"]["report"]["candidates"][0]["issues"],
                         ["UNCHANGED_REVISION"])
        self.assertEqual(unchanged["seeds"][0]["expectedRevision"] + 1, 2)
        packet = prepare_text_review(state, value, identifier)["blindPacket"]
        self.assertEqual(packet["candidateRevision"], 1)
        with self.assertRaisesRegex(ValueError, "SNAPSHOT_MISMATCH"):
            prepare_text_review(state, unchanged, identifier)

    def test_selective_correction_derives_claim_digests_and_preserves_history(self):
        value = self.sample()
        state, identifier = self.record(value)
        before = deepcopy(state)
        correction = deepcopy(value)
        correction["batchId"] = "correction"
        correction["seeds"][0]["expectedRevision"] = 1
        correction["seeds"][0]["fixture"]["answerKey"] = "A different point"
        state, _ = self.record(correction)
        self.assertEqual(state["history"][:1], before["history"])
        handoff = prepare_text_review(state, correction, identifier)
        evidence = handoff["mechanicalEvidence"]
        self.assertEqual(handoff["blindPacket"]["candidateRevision"], 2)
        self.assertEqual(evidence["changedFields"], ["/content/answerKey"])
        self.assertIn("/content/passage", evidence["unchangedFields"])
        claims = {c["fieldPath"]: c for c in evidence["claims"]}
        self.assertEqual(claims["/content/answerKey"]["valueDigest"], _hash("A different point"))
        self.assertEqual(claims["/content/answerKey"]["previousValueDigest"], _hash("A synthetic point"))
        self.assertEqual(claims["/content/passage"]["valueDigest"],
                         claims["/content/passage"]["previousValueDigest"])
        with self.assertRaisesRegex(ValueError, "SNAPSHOT_MISMATCH"):
            prepare_text_review(state, value, identifier)
        self.assertEqual(evidence["qualityBinding"], "PASS")
        self.assertIn("answer_key_review", evidence["unresolvedEvidence"])
        self.assertFalse(evidence["publicationEligible"])

    def test_removed_optional_explanation_is_an_affected_claim(self):
        value = self.sample()
        value["seeds"][0]["fixture"]["rationale"] = "Synthetic explanation"
        state, identifier = self.record(value)
        correction = deepcopy(value)
        correction["batchId"] = "remove-rationale"
        correction["seeds"][0]["expectedRevision"] = 1
        del correction["seeds"][0]["fixture"]["rationale"]
        state, _ = self.record(correction)
        evidence = prepare_text_review(state, correction, identifier)["mechanicalEvidence"]
        self.assertEqual(evidence["changedFields"], ["/content/rationale"])
        claim = next(c for c in evidence["claims"] if c["fieldPath"] == "/content/rationale")
        self.assertFalse(claim["present"])
        self.assertIsNone(claim["valueDigest"])
        self.assertEqual(claim["previousValueDigest"], _hash("Synthetic explanation"))

    def test_stale_or_wrong_candidate_quality_is_not_reused(self):
        value = self.sample()
        state, identifier = self.record(value)
        for field, bad in (("candidateId", "spn_wrong"), ("revision", 99), ("contentHash", "0" * 64)):
            changed = deepcopy(state)
            changed["candidates"][identifier]["generatedContentQuality"][field] = bad
            changed["history"][-1]["after"] = deepcopy(changed["candidates"][identifier])
            evidence = prepare_text_review(changed, value, identifier)["mechanicalEvidence"]
            self.assertEqual(evidence["qualityBinding"], "NOT_VERIFIED")
            self.assertEqual(evidence["issues"], ["GENERATED_QUALITY_BINDING_STALE"])
            self.assertEqual(evidence["generatedContentQuality"]["checks"], {"generation": "NOT_VERIFIED"})
            self.assertFalse(evidence["publicationEligible"])

    def test_wrong_batch_changed_snapshot_and_unbound_brief_fail_closed(self):
        value = self.sample()
        state, identifier = self.record(value)
        for field in ("objective", "constraints"):
            wrong = deepcopy(value)
            wrong["seeds"][0]["request"][field] = "tampered"
            with self.assertRaisesRegex(ValueError, "SNAPSHOT_MISMATCH"):
                prepare_text_review(state, wrong, identifier)
        wrong = deepcopy(state)
        wrong["candidates"][identifier]["revision"] = 99
        with self.assertRaisesRegex(ValueError, "SNAPSHOT_MISMATCH"):
            prepare_text_review(wrong, value, identifier)
        wrong = deepcopy(state)
        wrong["candidates"][identifier]["content"]["answerKey"] = "A different point"
        with self.assertRaisesRegex(ValueError, "SNAPSHOT_MISMATCH"):
            prepare_text_review(wrong, value, identifier)
        with self.assertRaisesRegex(ValueError, "SNAPSHOT_MISMATCH"):
            prepare_text_review(state, value, "unknown")
        incomplete = self.sample()
        incomplete["batchId"] = "no-brief"
        incomplete["seeds"][0]["seedId"] = "no-brief"
        incomplete["seeds"][0]["request"] = {}
        state, identifier = self.record(incomplete)
        with self.assertRaisesRegex(ValueError, "BRIEF_INCOMPLETE"):
            prepare_text_review(state, incomplete, identifier)

    def test_preparation_is_deterministic_detached_and_read_only(self):
        value = self.sample()
        state, identifier = self.record(value)
        frozen, source = deepcopy(state), deepcopy(value)
        disk = Path(self.path).read_bytes()
        first = prepare_text_review(state, value, identifier)
        self.assertEqual(first, prepare_text_review(state, value, identifier))
        first["blindPacket"]["stimulus"]["alternatives"].append("mutated output")
        first["blindPacket"]["responseConstraints"].append("mutated constraint")
        first["mechanicalEvidence"]["generatedContentQuality"]["checks"]["editorialCorrectness"] = "PASS"
        self.assertEqual(state, frozen)
        self.assertEqual(value, source)
        self.assertEqual(Path(self.path).read_bytes(), disk)

    def test_authority_claims_in_brief_cannot_clear_any_gate(self):
        value = self.sample()
        value["seeds"][0]["request"].update(
            reviewerId="writer", appointment="self", rights="cleared", publicationEligible=True)
        state, identifier = self.record(value)
        result = prepare_text_review(state, value, identifier)
        evidence = result["mechanicalEvidence"]
        self.assertEqual(evidence["status"], "HOLD")
        self.assertFalse(evidence["publicationEligible"])
        self.assertIn("independent_editorial_authority", evidence["unresolvedEvidence"])
        self.assertIn("usage_rights_clearance", evidence["unresolvedEvidence"])
        self.assertNotIn("reviewerId", result["blindPacket"])
        self.assertNotIn("appointment", result["blindPacket"])


if __name__ == "__main__":
    unittest.main()
