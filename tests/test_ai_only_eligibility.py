"""Synthetic authority-adapter tests; never evidence of real QA or rights."""
from copy import deepcopy
import unittest
from english_action.content.eligibility import assess_n2_cloze, record_n2_decision
from english_action.content.spawn import _hash, prepare_text_review


class N2EligibilityTests(unittest.TestCase):
    def setUp(self):
        seed = {"seedId": "test", "recipe": "N2-06", "sourceRef": "synthetic:n2",
                "sourceRevision": 1, "variantKey": "test",
                "request": {"objective": "Third-person present simple",
                            "construct": "agreement", "responseBranch": "type_in",
                            "constraints": ["Use the present simple of take"]}}
        self.batch = {"batchId": "synthetic-batch", "seeds": [seed]}
        self.id = "spn_" + _hash(["N2-06", "synthetic:n2", "test", "test"])[:24]
        content = {"context": "Use present simple of take.",
                   "prompt": "Mina ___ the bus every weekday.", "key": "takes",
                   "alternatives": [], "rationale": "Mina is third person singular."}
        item = {"candidateId": self.id, "seedId": "test", "recipe": "N2-06",
                "sourceRef": seed["sourceRef"], "sourceRevision": 1,
                "variantKey": "test", "batchId": "synthetic-batch",
                "revision": 1, "status": "HOLD", "content": content,
                "contentHash": _hash(content), "provenance": {
                    "mode": "real_ai", "provider": "SYNTHETIC_TEST_ONLY"}}
        item["generatedContentQuality"] = {"candidateId": self.id, "revision": 1,
                                           "contentHash": item["contentHash"], "issues": []}
        self.state = {"candidates": {self.id: item},
                      "runs": {"synthetic-batch": {"inputHash": _hash(self.batch)}},
                      "history": [{"candidateId": self.id, "before": None, "after": deepcopy(item)}]}
        packet = prepare_text_review(self.state, self.batch, self.id)["blindPacket"]
        self.policy = {"version": "synthetic-policy-v1", "recipe": "N2-06",
                       "objective": packet["objective"], "status": "active",
                       "useScope": "foundation_p0a_text"}
        self.qa = {"runId": "synthetic-qa", "actorId": "SYNTHETIC_ONLY", "actorKind": "ai",
                   "independence": "separate_run", "procedureVersion": "test",
                   "candidateId": self.id, "candidateRevision": 1,
                   "contentHash": item["contentHash"], "sourceRevision": 1,
                   "blindPacketDigest": _hash(packet),
                   "blind": {"answers": ["takes"], "uncertain": False,
                             "reasoning": "Third person singular."},
                   "comparison": {"status": "supported", "key": "takes",
                                  "alternatives": [], "reasoning": "Matches."}}
        expected = {"/content/" + k: _hash(v) for k, v in content.items()}
        expected.update({"/objective": _hash(packet["objective"]),
                         "/construct": _hash(packet["construct"]),
                         "/responseConditions": _hash([packet["responseBranch"], packet["responseConstraints"]])})
        self.claims = [{"evidenceId": f"synthetic-{n}", "fieldPath": path, "valueDigest": digest,
                        "candidateRevision": 1, "contentHash": item["contentHash"],
                        "policyVersion": self.policy["version"], "sourceId": "synthetic",
                        "sourceVersion": "fixture", "locator": "synthetic",
                        "claim": "SYNTHETIC_ONLY", "limitations": "not real evidence",
                        "verifiedAt": "2026-01-01T00:00:00Z",
                        "expiresAt": "2099-01-01T00:00:00Z"}
                       for n, (path, digest) in enumerate(expected.items())]
        self.rights = {"status": "redistributable", "candidateId": self.id,
                       "candidateRevision": 1, "contentHash": item["contentHash"],
                       "sourceRevision": 1, "policyVersion": self.policy["version"],
                       "useScope": "foundation_p0a_text", "evidenceId": "synthetic-rights",
                       "origin": "SYNTHETIC_ONLY", "rightsHolder": "SYNTHETIC_ONLY",
                       "rightsBasis": "synthetic-fixture", "territory": "synthetic",
                       "verifiedAt": "2026-01-01T00:00:00Z",
                       "expiresAt": "2099-01-01T00:00:00Z"}
        self.verifiers = {k: (lambda **kw: True) for k in
                          ("verify_qa", "verify_generation", "verify_closure",
                           "verify_claim", "verify_rights", "verify_policy", "verify_dependency")}

    def assess(self, **changes):
        values = {"qa_run": self.qa, "claims": self.claims, "rights": self.rights,
                  "policy": self.policy, "dependencies": [], **self.verifiers}
        values.update(changes)
        return assess_n2_cloze(self.state, self.batch, self.id, **values)

    def test_synthetic_eligible_without_human_item_appointment(self):
        result = self.assess()
        self.assertEqual(result["outcome"], "ELIGIBLE")
        self.assertEqual(result["consumerSnapshot"]["content"]["key"], "takes")
        self.assertEqual(len(result["evidenceIds"]), 8)
        self.assertEqual(self.assess(), result)

    def test_missing_trust_fails_closed(self):
        result = self.assess(**{key: None for key in self.verifiers})
        self.assertEqual(result["outcome"], "HOLD")
        self.assertIsNone(result["consumerSnapshot"])

    def test_adversarial_inputs_block_all_consumer_handoffs(self):
        for name, modify in [
            ("no QA", lambda x: x.update(qa_run=None)),
            ("wrong QA revision", lambda x: x["qa_run"].update(candidateRevision=2)),
            ("incorrect blind binding", lambda x: x["qa_run"].update(blindPacketDigest="wrong")),
            ("missing valid alternative", lambda x: x["qa_run"]["blind"].update(answers=["takes", "has taken"])),
            ("disputed key", lambda x: x["qa_run"]["comparison"].update(status="disputed")),
            ("false grammar agreed by models", lambda x: x.update(verify_claim=lambda **kw: False)),
            ("fake citation", lambda x: x.update(verify_claim=lambda **kw: False)),
            ("stale evidence", lambda x: x["claims"][0].update(expiresAt="2026-01-02T00:00:00Z")),
            ("wrong content digest", lambda x: x["claims"][0].update(valueDigest="wrong")),
            ("missing claim", lambda x: x["claims"].pop()),
            ("misleading explanation", lambda x: x.update(verify_claim=lambda **kw: False)),
            ("rights unknown", lambda x: x["rights"].update(status="unknown")),
            ("AI claims license", lambda x: x.update(verify_rights=lambda **kw: False)),
            ("policy invalid", lambda x: x.update(verify_policy=lambda **kw: False)),
            ("dependency closure skipped", lambda x: x.update(verify_closure=None)),
            ("generator unavailable", lambda x: x.update(verify_generation=None)),
        ]:
            values = {"qa_run": deepcopy(self.qa), "claims": deepcopy(self.claims),
                      "rights": deepcopy(self.rights), "policy": deepcopy(self.policy),
                      "dependencies": [], **self.verifiers}
            modify(values)
            with self.subTest(case=name):
                result = self.assess(**values)
                self.assertEqual(result["outcome"], "HOLD")
                self.assertIsNone(result["consumerSnapshot"])

    def test_withdrawn_and_missing_dependencies_fail_closed(self):
        self.assertEqual(self.assess(dependencies=[{"id": "missing", "revision": 1}],
                                     verify_dependency=lambda **kw: False)["outcome"], "HOLD")
        self.state["candidates"][self.id]["status"] = "WITHDRAWN"
        self.assertEqual(self.assess()["outcome"], "HOLD")

    def test_immutable_decision_replay_and_conflict(self):
        accepted = self.assess()
        updated, first = record_n2_decision(self.state, "synthetic-1", accepted)
        self.assertEqual(first["status"], "recorded")
        _, again = record_n2_decision(updated, "synthetic-1", accepted)
        self.assertEqual(again["status"], "replayed")
        _, conflict = record_n2_decision(updated, "synthetic-1",
                                         {**accepted, "outcome": "HOLD"})
        self.assertEqual(conflict["code"], "DECISION_ID_CONFLICT")


if __name__ == "__main__":
    unittest.main()
