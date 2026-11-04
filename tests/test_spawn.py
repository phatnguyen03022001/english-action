"""Public synthetic-only tests for the P0-a draft-only producer."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from english_action.spawn import (RECIPES, SyntheticWorker, generate, load_contract,
                                  persist, run, validate_content)

SENSE = "sns_01940000-0000-7000-8000-000000000002"
ASSET_A = {"logicalId": "synthetic:audio:a", "mediaType": "audio", "sha256": "0" * 64,
           "revision": 1, "provider": "synthetic", "model": "fixture-v1",
           "storage": "local-fixture", "rightsStatus": "unknown", "accessibility": "synthetic text label"}
ASSET_B = {**ASSET_A, "logicalId": "synthetic:audio:b", "sha256": "1" * 64}


def good(recipe):
    return deepcopy({
        "N1-01": {"viCue": "một gợi ý minh hoạ", "enExpressions": ["sample expression"],
                  "senseRefs": [SENSE]},
        "N2-06": {"context": "Example context", "prompt": "Fill the blank",
                  "key": "has", "alternatives": ["had"], "rationale": "Synthetic grammar explanation"},
        "N3-11": {"phonemes": ["/ɪ/", "/iː/"], "labels": ["ship", "sheep"],
                  "assets": [ASSET_A, ASSET_B], "key": "ship"},
        "K1-16": {"transcript": "Example sentence", "acceptedForms": ["Example sentence"], "audio": ASSET_A},
        "K1-17": {"passage": "A short synthetic listening passage", "question": "What is the main idea?",
                  "mainIdea": "A synthetic point", "answerKey": "A synthetic point", "audio": ASSET_A},
        "K2-21": {"passage": "A short synthetic reading passage", "question": "Main idea?",
                  "mainIdea": "A synthetic point", "alternatives": ["A synthetic point", "A different point"],
                  "answerKey": "A synthetic point"},
        "N2-10": {"context": "Example context", "tokens": ["It", "works"], "acceptedOrderings": [["It", "works"]],
                  "constraints": ["Use both tokens"], "explanation": "This is a synthetic ordering"},
    }[recipe])


def seed(recipe, n=1):
    return {"seedId": f"seed-{n}", "recipe": recipe, "sourceRef": f"synthetic:source:{n}",
            "sourceRevision": 1, "request": {"topic": "synthetic"}, "fixture": good(recipe)}


def batch(seeds, batch_id="sample-1"):
    return {"batchId": batch_id, "seeds": seeds}


class SpawnAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_seven_positive_and_negative(self):
        for recipe in RECIPES:
            with self.subTest(recipe=recipe):
                good_value = good(recipe)
                self.assertEqual(validate_content(recipe, good_value), [])
                bad = deepcopy(good_value)
                del bad[next(iter(bad))]
                self.assertTrue(validate_content(recipe, bad))
                result = await generate(batch([seed(recipe)]), SyntheticWorker())
                self.assertEqual(result[0]["status"], "HOLD")
                self.assertEqual(result[0]["recipe"], recipe)
                self.assertEqual(result[0]["provenance"]["mode"], "synthetic")

    async def test_negative_semantic_shapes_each_recipe(self):
        modifications = {
            "N1-01": lambda x: x.update(senseRefs=["sns_not_a_uuid"]),
            "N2-06": lambda x: x.update(alternatives=[x["key"]]),
            "N3-11": lambda x: x.update(phonemes=["/ɪ/", "/ɪ/"]),
            "K1-16": lambda x: x.update(acceptedForms=["not transcript"]),
            "K1-17": lambda x: x.update(mainIdea=""),
            "K2-21": lambda x: x.update(answerKey="not listed"),
            "N2-10": lambda x: x.update(acceptedOrderings=[["works", "works"]]),
        }
        for recipe, modify in modifications.items():
            with self.subTest(recipe=recipe):
                s = seed(recipe)
                modify(s["fixture"])
                item = (await generate(batch([s]), SyntheticWorker()))[0]
                self.assertEqual(item["status"], "REJECTED")
                self.assertTrue(item["issues"])

    async def test_bounded_fanout_and_isolated_retry(self):
        class Flaky:
            provider, model = "test", "flaky"
            def __init__(self):
                self.live, self.maximum, self.calls = 0, 0, {}
            async def generate(self, s):
                n = s["seedId"]
                self.calls[n] = self.calls.get(n, 0) + 1
                self.live += 1
                self.maximum = max(self.maximum, self.live)
                try:
                    await asyncio.sleep(.01)
                    if n == "seed-3":
                        raise RuntimeError("TRANSIENT_FAILURE")
                    if n == "seed-2" and self.calls[n] == 1:
                        raise RuntimeError("TRANSIENT_FAILURE")
                    return s["fixture"]
                finally:
                    self.live -= 1
        worker = Flaky()
        seeds = [seed(RECIPES[n], n+1) for n in range(7)]
        results = await generate(batch(seeds), worker, concurrency=2, retries=1)
        self.assertLessEqual(worker.maximum, 2)
        self.assertEqual(worker.calls["seed-2"], 2)
        self.assertEqual(worker.calls["seed-3"], 2)
        self.assertEqual(sum(r["status"] == "REJECTED" for r in results), 1)
        self.assertEqual(sum(r["status"] == "HOLD" for r in results), 6)

    async def test_timeout_cancellation_and_invalid_config(self):
        class Slow:
            provider, model = "test", "slow"
            async def generate(self, _):
                await asyncio.sleep(5)
        results = await generate(batch([seed("N1-01")]), Slow(), timeout=.01, retries=0)
        self.assertEqual(results[0]["status"], "REJECTED")
        task = asyncio.create_task(generate(batch([seed("N1-01")]), Slow(), timeout=10))
        await asyncio.sleep(.005)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        with self.assertRaises(ValueError):
            await generate(batch([seed("N1-01")]), SyntheticWorker(), concurrency=0)

    async def test_duplicates_and_source_conflicts(self):
        seeds = [seed("N1-01", 1), seed("N1-01", 2)]
        result = await generate(batch(seeds), SyntheticWorker())
        self.assertEqual([r["status"] for r in result], ["HOLD", "REJECTED"])
        seeds[1]["fixture"]["enExpressions"] = ["another expression"]
        seeds[1]["sourceRef"] = seeds[0]["sourceRef"]
        result = await generate(batch(seeds), SyntheticWorker())
        self.assertEqual([r["status"] for r in result], ["HOLD", "REJECTED"])

    async def test_private_contract_rejects_invalid(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "schema.json"
            path.write_text(json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object", "required": ["candidateId", "recipe", "content", "status"],
                "properties": {"status": {"const": "HOLD"}, "content": {"type": "object",
                    "required": ["extraPrivateRequired"]}}}))
            validator = load_contract(str(path))
            result = await generate(batch([seed("N1-01")]), SyntheticWorker(), contract=validator)
            self.assertEqual(result[0]["issues"], ["PRIVATE_CONTRACT_INVALID"])
            path.write_text(json.dumps({"$ref": "https://untrusted.test/private"}))
            with self.assertRaises(ValueError):
                load_contract(str(path))


class StoreTests(unittest.TestCase):
    def test_durable_draft_history_replay_and_conflict(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / "state.json")
            a = batch([seed("N1-01")])
            generated = asyncio.run(generate(a, SyntheticWorker()))
            q = {"status": "NOT_VERIFIED"}
            first = persist(a, generated, path, q)
            self.assertFalse(first["publicationEligible"])
            self.assertEqual(first["drafts"], 1)
            self.assertEqual(first["rejected"], 0)
            second = persist(a, generated, path, q)
            self.assertTrue(second["replayed"])
            state = json.loads(Path(path).read_text())
            self.assertEqual(len(state["history"]), 1)
            b = deepcopy(a)
            b["seeds"][0]["fixture"]["viCue"] = "another cue"
            b["batchId"] = "sample-2"
            without_cas = asyncio.run(generate(b, SyntheticWorker()))
            bad = persist(b, without_cas, path, q)
            self.assertEqual(bad["rejected"], 1)
            self.assertEqual(len(json.loads(Path(path).read_text())["history"]), 1)
            c = deepcopy(b)
            c["batchId"] = "sample-3"
            c["seeds"][0]["expectedRevision"] = 1
            accepted = persist(c, asyncio.run(generate(c, SyntheticWorker())), path, q)
            self.assertEqual(accepted["drafts"], 1)
            self.assertEqual(len(json.loads(Path(path).read_text())["history"]), 2)
            with self.assertRaisesRegex(ValueError, "BATCH_REPLAY_CONFLICT"):
                persist({**a, "seeds": []}, generated, path, q)

    def test_cli_requires_authorized_provider_and_keeps_private_contract(self):
        with tempfile.TemporaryDirectory() as d:
            contract = Path(d) / "producer.json"
            contract.write_text(json.dumps({"type": "object",
                "required": ["candidateId", "recipe", "content", "provenance", "status"],
                "properties": {"status": {"const": "HOLD"}}}))
            inp = Path(d) / "input.json"
            inp.write_text(json.dumps(batch([seed(r, n+1) for n, r in enumerate(RECIPES)])))
            state = Path(d) / "state.json"
            args = ["--input", str(inp), "--contract", str(contract), "--state", str(state)]
            self.assertEqual(run(args), 0)
            saved = json.loads(state.read_text())
            self.assertEqual(len(saved["candidates"]), 7)
            self.assertEqual(len(saved["history"]), 7)
            self.assertTrue(all(v["status"] == "HOLD" for v in saved["candidates"].values()))
            self.assertEqual(run(args), 0)  # exact replay
            self.assertEqual(len(json.loads(state.read_text())["history"]), 7)
            self.assertEqual(run(args + ["--provider", "openai"]), 1)


if __name__ == "__main__":
    unittest.main()
