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

    def test_read_only_and_bounded_diagnostics(self) -> None:
        def snapshot() -> dict[str, bytes]:
            return {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).digest()
                    for p in self.root.rglob("*") if p.is_file()}
        before = snapshot()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0)
        self.assertEqual(snapshot(), before)
        self.assertLess(len(result.stdout), 1000)

    def test_invalid_root_is_usage_error(self) -> None:
        result = self.run_cli(self.root / "nonexistent")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--root", result.stderr)


if __name__ == "__main__":
    unittest.main()
