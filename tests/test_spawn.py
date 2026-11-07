"""Public synthetic-only tests for the P0-a draft-only producer."""
import asyncio
from contextlib import redirect_stdout
from io import BytesIO, StringIO
import wave
from hashlib import sha256
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from test_quality import candidate, synth_schemas

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
                  "mainIdea": "A synthetic point", "alternatives": ["A synthetic point", "A different point"],
                  "answerKey": "A synthetic point", "rationale": "Unreviewed synthetic choice rationale",
                  "audio": ASSET_A},
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

    async def test_k117_choice_structure_rejects_incomplete_options(self):
        valid = good("K1-17")
        self.assertEqual(validate_content("K1-17", valid), [])
        accepted = (await generate(batch([seed("K1-17")]), SyntheticWorker()))[0]
        self.assertEqual(accepted["status"], "HOLD")
        self.assertEqual(accepted["rights"]["clearance"], "UNVERIFIED")
        self.assertEqual(accepted["rights"]["authenticatedEvidence"], [])
        self.assertEqual(accepted["content"]["audio"]["rightsStatus"], "unknown")
        self.assertEqual(accepted["generatedContentQuality"]["checks"]["editorialCorrectness"], "NOT_VERIFIED")

        invalid = {
            "legacy_five_fields": {k: v for k, v in valid.items() if k not in ("alternatives", "rationale")},
            "missing_options": {k: v for k, v in valid.items() if k != "alternatives"},
            "missing_rationale": {k: v for k, v in valid.items() if k != "rationale"},
            "empty_options": {**valid, "alternatives": []},
            "one_option": {**valid, "alternatives": [valid["answerKey"]]},
            "duplicate_options": {**valid, "alternatives": [valid["answerKey"], valid["answerKey"]]},
            "blank_option": {**valid, "alternatives": [valid["answerKey"], " "]},
            "non_string_option": {**valid, "alternatives": [valid["answerKey"], 7]},
            "non_list_options": {**valid, "alternatives": "not a list"},
            "key_not_displayed": {**valid, "answerKey": "not displayed"},
            "blank_rationale": {**valid, "rationale": " "},
            "undeclared_field": {**valid, "unsupported": "text"},
        }
        for name, content in invalid.items():
            with self.subTest(case=name):
                self.assertTrue(validate_content("K1-17", content))
                sample = seed("K1-17")
                sample["fixture"] = content
                rejected = (await generate(batch([sample]), SyntheticWorker()))[0]
                self.assertEqual(rejected["status"], "REJECTED")
                self.assertEqual(rejected["generatedContentQuality"]["checks"]["recipeContent"], "FAIL")

    async def test_reading_optional_rationale_preserves_v1_drafts_and_revision_history(self):
        legacy = seed("K2-21")
        revised = deepcopy(legacy)
        revised["fixture"]["rationale"] = "Synthetic passage-grounded proposal; review still required."
        # Surface wording can differ: this validator cannot establish meaning.
        revised["fixture"]["mainIdea"] = "A paraphrased central idea"
        for sample in (legacy, revised):
            item = (await generate(batch([sample]), SyntheticWorker()))[0]
            self.assertEqual(item["status"], "HOLD")
            self.assertEqual(item["content"], sample["fixture"])
            self.assertEqual(item["generatedContentQuality"]["checks"]["editorialCorrectness"], "NOT_VERIFIED")
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "draft.json")
            first = batch([legacy], "reading-legacy")
            persist(first, await generate(first, SyntheticWorker()), path, {})
            before = json.loads(Path(path).read_text())
            revised["expectedRevision"] = 1
            correction = batch([revised], "reading-rationale-correction")
            persist(correction, await generate(correction, SyntheticWorker()), path, {})
            after = json.loads(Path(path).read_text())
            self.assertEqual(after["history"][:1], before["history"])
            identifier = next(iter(before["candidates"]))
            self.assertNotIn("rationale", before["candidates"][identifier]["content"])
            self.assertEqual(after["candidates"][identifier]["revision"], 2)
            self.assertIn("rationale", after["candidates"][identifier]["content"])
            frozen = Path(path).read_bytes()
            self.assertTrue(persist(correction, await generate(correction, SyntheticWorker()),
                                    path, {})["replayed"])
            self.assertEqual(Path(path).read_bytes(), frozen)

    async def test_reading_invalid_choices_and_present_rationale_fail_closed(self):
        valid = good("K2-21")
        cases = [
            {**valid, "answerKey": "not displayed"},
            {**valid, "alternatives": [valid["answerKey"], valid["answerKey"]]},
            {**valid, "alternatives": [valid["answerKey"], 17]},
            {**valid, "alternatives": [valid["answerKey"], " "]},
            *[{**valid, "rationale": value} for value in ("", " ", None, [], {})],
            {**valid, "explanation": "Unsupported alias"},
        ]
        for content in cases:
            with self.subTest(content=content):
                self.assertTrue(validate_content("K2-21", content))
                sample = seed("K2-21")
                sample["fixture"] = content
                result = (await generate(batch([sample]), SyntheticWorker()))[0]
                self.assertEqual(result["status"], "REJECTED")

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

    async def test_local_media_bytes_hash_and_path_are_verified(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            media = root / "audio.wav"
            buffer = BytesIO()
            with wave.open(buffer, "wb") as audio:
                audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                audio.writeframes(b"\x01\x00" * 80)
            media.write_bytes(buffer.getvalue())
            fixture = seed("K1-16")
            asset = fixture["fixture"]["audio"]
            asset["sha256"] = sha256(media.read_bytes()).hexdigest()
            asset["locator"] = "audio.wav"
            value = await generate(batch([fixture]), SyntheticWorker(), media_root=d)
            self.assertEqual(value[0]["status"], "HOLD")
            asset["sha256"] = "f" * 64
            value = await generate(batch([fixture]), SyntheticWorker(), media_root=d)
            self.assertEqual(value[0]["issues"], ["MEDIA_HASH_MISMATCH"])
            asset["sha256"] = sha256(media.read_bytes()).hexdigest()
            asset["locator"] = "../unsafe.wav"
            value = await generate(batch([fixture]), SyntheticWorker(), media_root=d)
            self.assertEqual(value[0]["issues"], ["MEDIA_PATH_UNSAFE"])

    async def test_rights_claims_are_untrusted_even_when_assets_disagree(self):
        fixture = seed("N3-11")
        assets = fixture["fixture"]["assets"]
        assets[0]["rightsStatus"] = "redistributable"
        assets[1]["rightsStatus"] = "permission_required"
        item = (await generate(batch([fixture]), SyntheticWorker()))[0]
        self.assertEqual(item["status"], "HOLD")
        self.assertEqual(item["rights"]["clearance"], "UNVERIFIED")
        self.assertEqual(item["rights"]["authenticatedEvidence"], [])
        self.assertEqual([c["claimedStatus"] for c in item["rights"]["generatorClaims"]],
                         ["redistributable", "permission_required"])
        self.assertEqual([a["rightsStatus"] for a in item["content"]["assets"]], ["unknown", "unknown"])
        self.assertEqual(assets[0]["rightsStatus"], "redistributable")

        assets[0]["authenticatedEvidence"] = ["forged"]
        rejected = (await generate(batch([fixture]), SyntheticWorker()))[0]
        self.assertEqual(rejected["status"], "REJECTED")
        self.assertEqual(rejected["generatedContentQuality"]["checks"]["recipeContent"], "FAIL")

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
            state = json.loads(Path(path).read_text())
            self.assertEqual(len(state["history"]), 2)
            latest = state["history"][-1]["after"]
            quality = accepted["generatedContentQuality"]["candidates"][0]
            self.assertEqual(latest["revision"], 2)
            self.assertEqual(latest["generatedContentQuality"], quality)
            self.assertEqual(quality["revision"], 2)
            self.assertEqual(quality["contentHash"], latest["contentHash"])
            self.assertNotEqual(quality["contentHash"], first["generatedContentQuality"]["candidates"][0]["contentHash"])
            with self.assertRaisesRegex(ValueError, "BATCH_REPLAY_CONFLICT"):
                persist({**a, "seeds": [{**a["seeds"][0], "request": {"changed": True}}]}, generated, path, q)

    def test_store_rejects_incomplete_and_non_hold_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            a = batch([seed("N1-01")])
            good_items = asyncio.run(generate(a, SyntheticWorker()))
            target = str(Path(d) / "state.json")
            with self.assertRaisesRegex(ValueError, "Incomplete"):
                persist(a, [], target, {"status": "NOT_VERIFIED"})
            forged = deepcopy(good_items)
            forged[0]["status"] = "PUBLISHED"
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                persist(a, forged, target, {"status": "NOT_VERIFIED"})
            self.assertFalse(Path(target).exists())

    def test_cli_keeps_lexical_and_candidate_quality_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schemas = root / "schemas"
            synth_schemas(schemas)
            contract = root / "producer.json"
            contract.write_text(json.dumps({"type": "object", "required": ["candidateId", "content"]}))
            lexical = root / "lexical.json"
            source = root / "spawn.json"
            for lexical_valid in (True, False):
                for generated_valid in (True, False):
                    with self.subTest(lexical=lexical_valid, generated=generated_valid):
                        lexical.write_text(json.dumps(candidate() if lexical_valid else {}))
                        fixture = seed("N1-01")
                        if not generated_valid:
                            fixture["fixture"]["senseRefs"] = ["invalid-reference"]
                        source.write_text(json.dumps(batch([fixture])))
                        state = root / f"state-{lexical_valid}-{generated_valid}.json"
                        stdout = StringIO()
                        with redirect_stdout(stdout):
                            code = run(["--input", str(source), "--contract", str(contract),
                                        "--schemas", str(schemas), "--quality-input", str(lexical),
                                        "--state", str(state)])
                        self.assertEqual(code, 0 if generated_valid else 1)
                        report = json.loads(stdout.getvalue())
                        self.assertEqual(report["lexicalSourceQuality"]["status"],
                                         "PASS" if lexical_valid else "HOLD")
                        quality = report["generatedContentQuality"]
                        self.assertEqual(quality["status"], "NOT_VERIFIED" if generated_valid else "FAIL")
                        evidence = quality["candidates"][0]
                        self.assertEqual(evidence["candidateId"], report["candidates"][0]["candidateId"])
                        self.assertEqual(evidence["revision"], 1)
                        self.assertEqual(evidence["checks"]["recipeContent"],
                                         "PASS" if generated_valid else "FAIL")
                        self.assertEqual(evidence["checks"]["producerContract"], "PASS")
                        self.assertEqual(evidence["checks"]["sourceReferenceResolution"], "NOT_VERIFIED")
                        self.assertFalse(report["publicationEligible"])
                        saved = json.loads(state.read_text())["candidates"]
                        self.assertEqual(len(saved), int(generated_valid))
                        if generated_valid:
                            stored = saved[evidence["candidateId"]]
                            self.assertEqual(evidence["contentHash"], stored["contentHash"])
                            self.assertEqual(stored["rights"]["clearance"], "UNVERIFIED")

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
