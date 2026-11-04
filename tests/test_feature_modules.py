"""Public compatibility and isolation checks for the two feature modules."""
from __future__ import annotations

import ast
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/spec-validation"


class FeatureModuleTests(unittest.TestCase):
    def command(self, module: str, *arguments: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run(
            [sys.executable, "-m", module, *arguments], cwd=ROOT, env=env,
            capture_output=True, text=True, check=False, timeout=30,
        )

    def test_feature_imports_do_not_load_the_other_feature(self):
        for feature, entry, other in (
            ("english", "english_action.english.spec_validate", "content"),
            ("content", "english_action.content.spawn", "english"),
        ):
            with self.subTest(feature=feature):
                script = ("import importlib, sys; "
                          f"importlib.import_module({entry!r}); "
                          "assert not any(key == 'english_action.' + "
                          f"{other!r} or key.startswith('english_action.' + {other!r} + '.') "
                          "for key in sys.modules), 'cross-feature dependency loaded'")
                result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                        capture_output=True, text=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_feature_source_has_no_cross_feature_imports(self):
        base = ROOT / "english_action"
        for feature, other in (("english", "content"), ("content", "english")):
            for file in (base / feature).glob("*.py"):
                with self.subTest(file=file.name, feature=feature):
                    tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
                    for node in ast.walk(tree):
                        if isinstance(node, ast.ImportFrom):
                            target = node.module or ""
                            self.assertFalse(
                                target == other or target.startswith(other + ".") or
                                target == "english_action." + other or
                                target.startswith("english_action." + other + "."),
                                f"{file}: imports {target}",
                            )
                        elif isinstance(node, ast.Import):
                            for alias in node.names:
                                self.assertFalse(
                                    alias.name == "english_action." + other or
                                    alias.name.startswith("english_action." + other + "."),
                                    f"{file}: imports {alias.name}",
                                )

    def test_legacy_python_imports_refer_to_moved_implementations(self):
        from english_action import SchemaDirectory, digest, prepare_batch, validate_dataset
        from english_action.cli import _atomic_save, _path_safe, _read_state, _state_lock
        from english_action.content import cli, contracts, ledger

        self.assertIs(SchemaDirectory, contracts.SchemaDirectory)
        self.assertIs(digest, contracts.digest)
        self.assertIs(validate_dataset, contracts.validate_dataset)
        self.assertIs(prepare_batch, ledger.prepare_batch)
        for original in (_atomic_save, _path_safe, _read_state, _state_lock):
            self.assertIs(original, getattr(cli, original.__name__))
        for module, symbol in (("cli", "run"), ("contracts", "validate_dataset"),
                               ("ledger", "blank_state"), ("review", "assess_golden_qa"),
                               ("schema_generation", "write_schemas"),
                               ("spawn", "generate"), ("v2_validation", "validate_v2_graph")):
            with self.subTest(module=module):
                old = importlib.import_module("english_action." + module)
                new = importlib.import_module("english_action.content." + module)
                self.assertIs(getattr(old, symbol), getattr(new, symbol))
        self.assertIs(importlib.import_module("english_action.spec_validate").Validator,
                      importlib.import_module("english_action.english.spec_validate").Validator)

    def test_legacy_and_feature_cli_surfaces_agree(self):
        checks = (
            (("english_action.spec_validate", "english_action.english.spec_validate"),
             ("--root", str(FIXTURE)), 0),
            (("english_action.spec_validate", "english_action.english.spec_validate"),
             ("--root", str(FIXTURE / "absent")), 2),
            (("english_action.spawn", "english_action.content.spawn"), ("--help",), 0),
            (("english_action", "english_action.content.cli"), ("--help",), 0),
        )
        for (legacy, direct), args, expected_code in checks:
            with self.subTest(legacy=legacy, arguments=args):
                before = self.command(legacy, *args)
                after = self.command(direct, *args)
                self.assertEqual(before.returncode, expected_code, before.stderr)
                self.assertEqual((before.returncode, before.stdout, before.stderr),
                                 (after.returncode, after.stdout, after.stderr))

    def test_console_script_contract_targets_content_feature(self):
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        scripts = pyproject["project"]["scripts"]
        self.assertEqual(scripts, {
            "english-quality": "english_action.content.cli:main",
            "english-spawn": "english_action.content.spawn:main",
        })
        for target in scripts.values():
            module, name = target.split(":")
            self.assertTrue(callable(getattr(importlib.import_module(module), name)))


if __name__ == "__main__":
    unittest.main()
