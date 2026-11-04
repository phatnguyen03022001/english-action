"""Public-safe synthetic acceptance checks for the read-only Spec Kit validator."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/spec-validation"
SPEC_REL = Path("specs/001-synthetic-flow/spec.md")


class SpecificationValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory(prefix="synthetic-spec-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "caller"
        shutil.copytree(FIXTURE, self.root)
        self.spec = self.root / SPEC_REL

    def replace(self, old: str, new: str) -> None:
        current = self.spec.read_text(encoding="utf-8")
        self.assertIn(old, current)
        self.spec.write_text(current.replace(old, new, 1), encoding="utf-8")

    def run_cli(self, root: Path | None = None) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, "-m", "english_action.spec_validate", "--root", str(root or self.root)],
            cwd=ROOT, env=environment, text=True, capture_output=True, check=False, timeout=30,
        )

    def assert_failure(self, expected: str) -> None:
        result = self.run_cli()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"ERROR {expected}:", result.stdout)
        self.assertIn(str(SPEC_REL), result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_valid_synthetic_spec_passes(self) -> None:
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS; 0 structural error(s)", result.stdout)
        self.assertIn("NOT VERIFIED", result.stdout)

    def test_missing_mandatory_section_fails(self) -> None:
        self.replace("## Success Criteria", "## Optional Outcomes")
        self.assert_failure("NO_SECTION")

    def test_duplicate_requirement_fails(self) -> None:
        self.replace("### Non-Functional Requirements",
                     "- **FR-001**: Duplicate synthetic requirement. [Trace: US1.1.]\n\n### Non-Functional Requirements")
        self.assert_failure("DUPLICATE_ID")

    def test_nonsequential_requirement_fails(self) -> None:
        self.replace("**FR-001**:", "**FR-002**:")
        self.assert_failure("NONSEQUENTIAL_ID")

    def test_missing_acceptance_scenario_fails(self) -> None:
        self.replace("1. **Given** an eligible", "1. Given an eligible")
        self.assert_failure("BAD_SCENARIO")

    def test_no_independent_test_fails(self) -> None:
        self.replace("**Independent Test**:", "**Illustration**:")
        self.assert_failure("NO_INDEPENDENT_TEST")

    def test_mismatched_feature_id_fails(self) -> None:
        self.replace("**Feature ID**: 001", "**Feature ID**: 099")
        self.assert_failure("FEATURE_ID")

    def test_invalid_spec_directory_fails(self) -> None:
        other = self.root / "specs/001_not-kebab"
        self.spec.parent.rename(other)
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR BAD_PATH:", result.stdout)

    def test_broken_relative_markdown_link_fails(self) -> None:
        self.replace("reference.md#quality-contract", "missing.md#quality-contract")
        self.assert_failure("BROKEN_LINK")

    def test_broken_heading_anchor_fails(self) -> None:
        self.replace("reference.md#quality-contract", "reference.md#absent-heading")
        self.assert_failure("BROKEN_ANCHOR")

    def test_unknown_inventory_capability_fails(self) -> None:
        self.replace("**Related Capability IDs**: D01-01", "**Related Capability IDs**: D99-99")
        self.assert_failure("UNKNOWN_CAPABILITY")

    def test_approved_without_human_owner_provenance_fails(self) -> None:
        self.replace("PROPOSED — synthetic fixture, not approved", "APPROVED")
        self.assert_failure("NO_APPROVAL_PROVENANCE")

    def test_approved_with_identifiable_provenance_passes(self) -> None:
        self.replace("PROPOSED — synthetic fixture, not approved",
                     "APPROVED\n\n**Gate 2 Acceptance Provenance**: Human Owner, TASK 99, 2026-11-04")
        self.assertEqual(self.run_cli().returncode, 0)

    def test_proposed_without_approval_provenance_passes(self) -> None:
        self.assertEqual(self.run_cli().returncode, 0)

    def test_unrecognized_status_fails(self) -> None:
        self.replace("PROPOSED — synthetic fixture, not approved", "RELEASED")
        self.assert_failure("BAD_STATUS")

    def test_malformed_trace_fails(self) -> None:
        self.replace("[Trace: US1.1; D01-01.]", "[Trace: ???]")
        self.assert_failure("MALFORMED_TRACE")

    def test_nonexistent_local_requirement_trace_fails(self) -> None:
        self.replace("[Trace: FR-001; D01-01.]", "[Trace: FR-999; D01-01.]")
        self.assert_failure("UNKNOWN_TRACE")

    def test_valid_cross_feature_requirement_trace_passes(self) -> None:
        other = self.root / "specs/002-synthetic-companion"
        other.mkdir()
        copy = self.spec.read_text(encoding="utf-8").replace(
            "**Feature ID**: 001", "**Feature ID**: 002", 1)
        (other / "spec.md").write_text(copy, encoding="utf-8")
        self.replace("[Trace: US1.1; D01-01.]", "[Trace: US1.1; F002:FR-001; D01-01.]")
        self.assertEqual(self.run_cli().returncode, 0)

    def test_cross_feature_unknown_requirement_fails(self) -> None:
        self.replace("[Trace: US1.1; D01-01.]", "[Trace: US1.1; F002:FR-001; D01-01.]")
        self.assert_failure("UNKNOWN_TRACE")

    def test_fenced_and_inline_code_links_not_flagged(self) -> None:
        tick = chr(96)
        addition = ("\n" + tick * 3 + "md\n[example](../../absent.md)\n" +
                    tick * 3 + "\n" + tick + "[example](../../absent.md)" + tick + "\n")
        self.spec.write_text(self.spec.read_text(encoding="utf-8") + addition,
                             encoding="utf-8")
        self.assertEqual(self.run_cli().returncode, 0)

    def test_no_pedagogical_or_privacy_semantics_inferred(self) -> None:
        self.replace("The system MUST show one synthetic result.",
                     "The system MUST show a fictional score and claim mastery.")
        self.assertEqual(self.run_cli().returncode, 0)

    def test_inventory_required(self) -> None:
        (self.root / "docs/product/capabilities.md").unlink()
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR NO_INVENTORY:", result.stdout)

    def test_knowledge_layout_inventory_and_references_pass(self) -> None:
        modern = self.document(
            "knowledge/product/capability-map.md",
            (self.root / "docs/product/capabilities.md").read_text(encoding="utf-8"),
        )
        (self.root / "docs/product/capabilities.md").unlink()
        self.document("knowledge/README.md", "# Knowledge Base\n\n"
                      "[Capability map](product/capability-map.md)\n"
                      "[Practice](features/learning/practice-methods/spec.md#ownership)\n")
        self.document("knowledge/features/learning/practice-methods/spec.md",
                      "# Practice methods\n\n## Ownership\n"
                      "[Inventory](../../../product/capability-map.md)\n")
        self.document("knowledge/architecture/decisions/0001-synthetic-boundary.md",
                      "# Synthetic ADR\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(modern.is_file())
        self.assertIn("PASS; 0 structural error(s)", result.stdout)

    def test_knowledge_links_are_actually_scanned(self) -> None:
        self.document("knowledge/product/capability-map.md",
                      (self.root / "docs/product/capabilities.md").read_text(encoding="utf-8"))
        (self.root / "docs/product/capabilities.md").unlink()
        self.document("knowledge/features/learning/practice-methods/spec.md",
                      "# Practice\n[Missing](../../../product/absent.md)\n"
                      "[No anchor](../../../product/capability-map.md#absent)\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("knowledge/features/learning/practice-methods/spec.md:2: ERROR BROKEN_LINK:", result.stdout)
        self.assertIn("knowledge/features/learning/practice-methods/spec.md:3: ERROR BROKEN_ANCHOR:", result.stdout)

    def test_migrated_product_directory_cannot_silently_use_old_inventory(self) -> None:
        self.document("knowledge/product/overview.md", "# Product\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("knowledge/product/capability-map.md:1: ERROR NO_INVENTORY:", result.stdout)

    def test_duplicate_inventories_fail(self) -> None:
        self.document("knowledge/product/capability-map.md",
                      (self.root / "docs/product/capabilities.md").read_text(encoding="utf-8"))
        result = self.run_cli()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("ERROR DUPLICATE_INVENTORY:", result.stdout)

    def test_invalid_knowledge_feature_depth_and_naming_fail(self) -> None:
        self.document("knowledge/features/Learning/bad/spec.md", "# Invalid domain\n")
        self.document("knowledge/features/learning/practice/too/deep/spec.md", "# Invalid depth\n")
        self.document("knowledge/architecture/decisions/unnumbered.md", "# Invalid ADR\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(result.stdout.count("ERROR BAD_DOC_PATH:"), 3, result.stdout)

    def test_new_feature_loc_budget_remains_advisory(self) -> None:
        self.document("knowledge/features/learning/practice/spec.md",
                      "# Methods\n" + "\n" * 401)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("knowledge/features/learning/practice/spec.md:1: WARNING LOC_PREFERRED:", result.stdout)

    def test_read_only_and_bounded_diagnostics(self) -> None:
        def snapshot() -> dict[str, bytes]:
            return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).digest()
                    for p in self.root.rglob("*") if p.is_file()}
        before = snapshot()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(snapshot(), before)
        self.assertLess(len(result.stdout), 1000)


    def document(self, name: str, content: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_multidocument_links_anchors_and_existing_compatibility_pass(self) -> None:
        self.document("README.md",
                      "# Navigation\n\n[Quality](docs/reference.md#quality-contract) "
                      "[Repeated](docs/reference.md#repeated-1) "
                      "[Explicit](docs/reference.md#stable-id) "
                      "[Encoded](docs/a%2Db.md#caf%C3%A9) "
                      "[Directory](docs/) "
                      "[External](https://example.org/absent.md)\n"
                      "[Reference][reference]\n\n[reference]: docs/reference.md#quality-contract\n")
        self.document("docs/a-b.md", "# Café\n")
        self.document("docs/reference.md",
                      "# Reference\n\n## Quality Contract\n\n## Repeated\n"
                      "## Repeated\n\n<a id=\"stable-id\"></a>\n")
        self.document("docs/product/feature-spec-template.md",
                      "# Compatibility\n\n[Canonical](../../specs/001-synthetic-flow/spec.md)\n")
        self.document("docs/adr/0001-synthetic-decision.md", "# Synthetic decision\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("0 advisory warning(s)", result.stdout)

    def test_other_spec_kit_artifacts_are_scanned_without_gate_2_path_rejection(self) -> None:
        self.document("specs/001-synthetic-flow/plan.md",
                      "# Plan\n\n[Reference](../../docs/reference.md)\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.document("specs/001-synthetic-flow/plan.md",
                      "# Plan\n\n[Missing](../../docs/nonexistent.md)\n")
        self.assertIn("ERROR BROKEN_LINK:", self.run_cli().stdout)

    def test_broken_local_image_link_fails(self) -> None:
        self.document("README.md", "# Navigation\n\n![Missing visual](docs/absent.png)\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR BROKEN_LINK:", result.stdout)

    def test_broken_root_readme_link_fails(self) -> None:
        self.document("README.md", "# Navigation\n\n[Missing](docs/absent.md)\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("README.md:3: ERROR BROKEN_LINK:", result.stdout)

    def test_broken_specialist_anchor_fails(self) -> None:
        self.document("docs/specialist.md", "# Actual Heading\n")
        self.document("docs/reference.md", "# Reference\n\n[Missing](specialist.md#absent)\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("docs/reference.md:3: ERROR BROKEN_ANCHOR:", result.stdout)

    def test_broken_reference_definition_fails(self) -> None:
        self.document("README.md", "# Navigation\n\n[See][contract]\n\n"
                      "[contract]: docs/no-contract.md\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("README.md:5: ERROR BROKEN_LINK:", result.stdout)

    def test_broken_cross_feature_markdown_reference_fails(self) -> None:
        self.document("README.md", "# Features\n\n"
                      "[Companion](specs/002-absent/spec.md)\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("README.md:3: ERROR BROKEN_LINK:", result.stdout)

    def test_encoded_repository_escape_and_invalid_local_path_fail(self) -> None:
        self.document("README.md", "# Navigation\n"
                      "[Outside](%2e%2e/%2e%2e/private.md)\n"
                      "[Invalid](docs/%00.md)\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR LINK_ESCAPE:", result.stdout)
        self.assertIn("ERROR INVALID_LINK:", result.stdout)

    def test_symlink_escape_is_rejected(self) -> None:
        outside = self.root.parent / "external.md"
        outside.write_text("# Outside\n", encoding="utf-8")
        (self.root / "docs/outside.md").symlink_to(outside)
        self.document("README.md", "# Navigation\n[Outside](docs/outside.md)\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR LINK_ESCAPE:", result.stdout)

    def test_project_owned_spec_kit_navigation_links_are_validated(self) -> None:
        path = ".specify/memory/constitution.md"
        self.document(path, "# Compatibility\n\n[Valid](../../docs/product/capabilities.md)\n")
        ok = self.run_cli()
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.document(path, "# Compatibility\n\n[Missing](../../docs/absent.md)\n")
        broken = self.run_cli()
        self.assertEqual(broken.returncode, 1)
        self.assertIn(f"{path}:3: ERROR BROKEN_LINK:", broken.stdout)

    def test_generated_spec_template_links_use_destination_context(self) -> None:
        self.document("PRODUCT.md", "# Product\n\n## Human Owner Decisions\n")
        self.document(".specify/templates/overrides/spec-template.md",
                      "# Spec Template\n\n"
                      "[Product](../../PRODUCT.md#human-owner-decisions)\n"
                      "[Inventory](../../docs/product/capabilities.md)\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.document(".specify/templates/overrides/spec-template.md",
                      "# Spec Template\n\n[Broken](../../absent.md)\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR BROKEN_LINK:", result.stdout)

    def test_ordinary_document_does_not_receive_template_exception(self) -> None:
        self.document("PRODUCT.md", "# Product\n")
        self.document("docs/reference.md", "# Reference\n"
                      "[Invalid here](../../PRODUCT.md)\n")
        # docs/reference.md -> ../../PRODUCT.md escapes repo.
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("ERROR LINK_ESCAPE:", result.stdout)

    def test_invalid_specialist_and_adr_naming_fail(self) -> None:
        self.document("docs/Bad_Name.md", "# Invalid naming\n")
        self.document("docs/adr/decision.md", "# Unnumbered ADR\n")
        result = self.run_cli()
        self.assertEqual(result.returncode, 1)
        self.assertIn("docs/Bad_Name.md:1: ERROR BAD_DOC_PATH:", result.stdout)
        self.assertIn("docs/adr/decision.md:1: ERROR BAD_DOC_PATH:", result.stdout)

    def test_preferred_loc_budgets_warn_without_failing(self) -> None:
        self.spec.write_text(self.spec.read_text(encoding="utf-8") + "\n" * 250,
                             encoding="utf-8")
        self.document("PRODUCT.md", "# Product\n" + "\n" * 300)
        self.document("docs/large-contract.md", "# Domain\n" + "\n" * 400)
        self.document("docs/adr/0001-a-decision.md", "# ADR\n" + "\n" * 200)
        self.document(".specify/templates/overrides/spec-template.md",
                      "# Spec Template\n" + "\n" * 150)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("WARNING LOC_PREFERRED:"), 5, result.stdout)
        self.assertIn("PASS; 0 structural error(s); 5 advisory warning(s)", result.stdout)

    def test_review_loc_budgets_warn_without_failing(self) -> None:
        self.spec.write_text(self.spec.read_text(encoding="utf-8") + "\n" * 350,
                             encoding="utf-8")
        self.document("PRODUCT.md", "# Product\n" + "\n" * 450)
        self.document("docs/large-contract.md", "# Domain\n" + "\n" * 600)
        self.document("docs/adr/0001-a-decision.md", "# ADR\n" + "\n" * 300)
        self.document(".specify/templates/overrides/spec-template.md",
                      "# Spec Template\n" + "\n" * 200)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("WARNING LOC_REVIEW:"), 5, result.stdout)
        self.assertIn("PASS; 0 structural error(s); 5 advisory warning(s)", result.stdout)

    def test_advisory_output_is_bounded_and_sorted(self) -> None:
        for index in range(80):
            self.document(f"docs/contract-{index:03d}.md",
                          "# Synthetic contract\n" + "\n" * 401)
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("80 advisory warning(s)", result.stdout)
        self.assertIn("further diagnostics suppressed", result.stdout)
        self.assertLessEqual(result.stdout.count("WARNING"), 60)
        self.assertEqual(result.stdout, self.run_cli().stdout)

    def test_documentation_is_not_mutated_by_validation(self) -> None:
        self.document("README.md", "# Navigation\n\n[Contract](docs/reference.md)\n")
        self.document("docs/specialist.md", "# Specialist\n")
        def digest() -> dict[str, bytes]:
            return {p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).digest()
                    for p in self.root.rglob("*.md") if p.is_file()}
        before = digest()
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual(digest(), before)

    def test_invalid_root_is_usage_error(self) -> None:
        result = self.run_cli(self.root / "nonexistent")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--root", result.stderr)


if __name__ == "__main__":
    unittest.main()
