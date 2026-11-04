"""Public deterministic schema generation and independent editorial QA regressions."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from english_action.schema_generation import (
    check_schemas, generate_schema_pairs, generate_v1, generate_v2, serialize_schema,
)
from english_action.review import QUALITY_DIMENSIONS, assess_golden_qa, migrate_v1_records


def registry_v1():
    return {
        "registryVersion": "1.0.0",
        "identifiers": {
            "allRegisteredPrefixes": ["lex", "sns", "evd", "dec"],
            "activeV1Prefixes": ["lex", "sns", "evd", "dec"],
            "reservedInactiveV1Prefixes": ["zxx"],
            "uuidV7Pattern": "[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
            "globalPattern": "^[a-z]{3}_.+$",
        },
        "enums": {"retirementMode": ["semantic_split", "semantic_merge"],
                  "editorialStatus": ["draft", "verified"]},
        "retirementRequirements": {
            "semantic_split": {"successorMin": 2},
            "semantic_merge": {"successorMin": 1, "successorMax": 1},
        },
        "relationTypes": [{
            "type": "SYNONYM_OF", "sourceType": "LexicalSense",
            "targetType": "LexicalSense", "symmetric": True,
        }],
    }


class SchemaGenerationTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        for version, registry in (("v1", registry_v1()),
                                  ("v2", {"registryVersion": "2.0.0",
                                          "enums": {"retirementMode": ["semantic_split", "semantic_merge"]}})):
            directory = self.root / version
            directory.mkdir()
            (directory / "vocabularies.json").write_text(json.dumps(registry), encoding="utf-8")

    def test_pure_generator_does_not_mutate_registry_or_v1(self):
        registry = registry_v1()
        original = deepcopy(registry)
        output = generate_v1(registry)
        assert set(output) == {
            "common.schema.json", "lexical-entry.schema.json", "lexical-sense.schema.json",
            "translation-equivalence.schema.json", "lexical-relation.schema.json",
            "word-family.schema.json", "topic.schema.json", "construction.schema.json",
            "collocation.schema.json", "lexical-form.schema.json", "morpheme.schema.json",
            "etymology.schema.json", "evidence.schema.json", "source.schema.json",
            "editorial-review.schema.json", "editorial-decision.schema.json"}
        upgraded = generate_v2(output["lexical-sense.schema.json"],
                               {"registryVersion": "2.0.0", "enums": {}})
        self.assertEqual(registry, original)
        self.assertEqual(output["lexical-sense.schema.json"]["version"], "1.0.0")
        self.assertEqual(upgraded["version"], "2.0.0")
        self.assertEqual(upgraded["properties"]["schemaVersion"], {"const": "2.0.0"})
        self.assertIn("examples", upgraded["properties"])
        self.assertTrue(serialize_schema(upgraded).endswith("\n"))

    def test_read_only_generation_check_detects_inventory_and_content_drift(self):
        expected = generate_schema_pairs(self.root)
        for version, items in expected.items():
            for name, content in items.items():
                (self.root / version / name).write_text(content, encoding="utf-8")
        check_schemas(self.root)
        cmd = [sys.executable, "-m", "english_action", "schemas-check", "--schemas", str(self.root)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["schemasValid"])
        path = self.root / "v2" / "lexical-sense.schema.json"
        before = path.read_bytes()
        path.write_text(path.read_text() + " ", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "schema drift"):
            check_schemas(self.root)
        self.assertEqual(path.read_bytes(), before + b" ")


class ReviewMigrationTest(unittest.TestCase):
    def test_v1_migration_preserves_identity_without_granting_review(self):
        identifier = "lex_01940000-0000-7000-8000-000000000001"
        old = {"_id": identifier, "schemaVersion": "1.0.0", "editorialStatus": "draft"}
        result = migrate_v1_records([old])
        self.assertEqual(result["records"][0]["_id"], identifier)
        self.assertEqual(result["records"][0]["schemaVersion"], "2.0.0")
        self.assertEqual(old["schemaVersion"], "1.0.0")
        self.assertEqual(result["migration"]["rightsStatus"], "not_assessed")
        with self.assertRaisesRegex(ValueError, "MIGRATION_DUPLICATE_ID"):
            migrate_v1_records([old, old])
        with self.assertRaisesRegex(ValueError, "MIGRATION_INVALID_V1_RECORD"):
            migrate_v1_records([{**old, "schemaVersion": "2.0.0"}])

    def test_eight_axis_qa_accepts_only_complete_independently_authenticated_synthetic(self):
        benchmark = {"families": [{"pilotFile": "synthetic.json"}]}
        submission = {"caseId": "synthetic",
                      "dimensions": {axis: "pass" for axis in QUALITY_DIMENSIONS},
                      "reviewerId": "test-only", "authorityReference": "synthetic",
                      "evidenceReferences": ["synthetic"]}
        denied = assess_golden_qa(benchmark, [submission])
        self.assertEqual(denied["qualityAcceptedCount"], 0)
        self.assertIn("GOLDEN_QA_AUTHORITY_UNVERIFIED", [x["code"] for x in denied["issues"]])
        accepted = assess_golden_qa(
            benchmark, [submission],
            verify_independent_review=lambda **_: True,
            verify_rights=lambda **_: True)
        self.assertEqual(accepted["qualityAcceptedCount"], 1)
        self.assertEqual(accepted["publicationApprovedCount"], 0)
        self.assertFalse(accepted["results"][0]["publicationEligible"])
        rejected = deepcopy(submission)
        rejected["dimensions"]["grammar_accuracy"] = "fail"
        self.assertEqual(assess_golden_qa(benchmark, [rejected])["qualityRejectedCount"], 1)
        failed = assess_golden_qa(benchmark, [submission],
                                  verify_independent_review=lambda **_: True,
                                  verify_rights=lambda **_: (_ for _ in ()).throw(RuntimeError()))
        self.assertEqual(failed["qualityAcceptedCount"], 0)

    def test_qa_duplicates_unknown_families_and_incomplete_review_fail_closed(self):
        benchmark = {"families": [{"pilotFile": "synthetic.json"}]}
        partial = {"caseId": "synthetic", "dimensions": {"grammar_accuracy": "pass"}}
        unknown = {"caseId": "unknown"}
        result = assess_golden_qa(benchmark, [partial, partial, unknown])
        self.assertEqual([x["code"] for x in result["issues"]],
                         ["GOLDEN_QA_DUPLICATE_SUBMISSION", "GOLDEN_QA_INCOMPLETE",
                          "GOLDEN_QA_UNKNOWN_FAMILY"])
        self.assertEqual(result["qualityAcceptedCount"], 0)


if __name__ == "__main__":
    unittest.main()
