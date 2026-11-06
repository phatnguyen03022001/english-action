"""Synthetic byte/binding regressions. No reviewer or rights approvals."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
import wave

from english_action.content.spawn import (
    _media_issues, media_binding, verify_media,
)


def wav_bytes(silent=False):
    stream = BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes((b"\x00\x00" if silent else b"\x01\x00") * 80)
    return stream.getvalue()


def asset(data, locator="sample.wav", kind="audio"):
    return {"logicalId": "synthetic:asset", "revision": 1, "sha256": sha256(data).hexdigest(),
            "mediaType": kind, "storage": "local-fixture", "locator": locator,
            "provider": "synthetic", "model": "test-only", "rightsStatus": "unknown",
            "accessibility": "Synthetic fixture; not learning content"}


def source():
    return {"sourceRef": "synthetic:source", "sourceRevision": 1, "contentRevision": 1,
            "objective": "synthetic binding test", "construct": "not a learning claim",
            "content": {"transcript": "Example", "acceptedForms": ["Example"]},
            "dependencies": ["synthetic:ancestor"]}


SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40"><text x="2" y="20">A</text></svg>'


class MediaVerificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.data = wav_bytes()
        (self.root / "sample.wav").write_bytes(self.data)
        self.assets, self.source = [asset(self.data)], source()
        self.configuration = {"voice": None, "language": None, "rate": None}

    def verify(self, **options):
        return verify_media(self.assets, self.source, self.configuration, str(self.root), **options)

    def test_matching_bytes_and_binding_are_not_speech_rights_or_publication(self):
        binding = media_binding(self.assets, self.source, self.configuration)
        report = self.verify(reviewed_binding=binding, rights_status="cleared")
        self.assertEqual(report["checks"]["integrity"], "PASS")
        self.assertEqual(report["checks"]["dependencyBinding"], "PASS")
        for check in ("actualContent", "rightsClearance", "editorialAuthorization"):
            self.assertEqual(report["checks"][check], "NOT_VERIFIED")
        self.assertEqual(report["assetAction"], "REUSE_BYTES_PENDING_GATES")
        self.assertFalse(report["publicationEligible"])
        self.assertFalse(report["newUseAllowed"])
        self.assertEqual(report["status"], "HOLD")

    def test_matching_hash_does_not_hide_corrupt_truncated_or_silent_wav(self):
        for data, reason in ((b"broken", "AUDIO_BYTES_INVALID"),
                             (self.data[:-2], "AUDIO_BYTES_INVALID"),
                             (wav_bytes(silent=True), "AUDIO_SILENT")):
            with self.subTest(reason=reason):
                (self.root / "sample.wav").write_bytes(data)
                self.assets[0] = asset(data)
                self.assertIn(reason, self.verify()["issues"])

    def test_bad_locator_missing_bytes_hash_and_symlink_fail(self):
        for locator, reason in (("", "MEDIA_LOCATOR_INVALID"),
                                ("/absolute.wav", "MEDIA_LOCATOR_INVALID"),
                                ("../outside.wav", "MEDIA_PATH_UNSAFE"),
                                ("missing.wav", "MEDIA_NOT_AVAILABLE")):
            with self.subTest(locator=locator):
                self.assets[0]["locator"] = locator
                self.assertIn(reason, self.verify()["issues"])
        self.assets[0]["locator"] = "sample.wav"
        self.assets[0]["sha256"] = "f" * 64
        self.assertIn("MEDIA_HASH_MISMATCH", self.verify()["issues"])
        link = self.root / "link.wav"
        link.symlink_to(self.root / "sample.wav")
        self.assets[0] = asset(self.data, "link.wav")
        self.assertIn("MEDIA_PATH_UNSAFE", self.verify()["issues"])

    def test_source_key_revision_and_configuration_changes_invalidate_binding(self):
        binding = media_binding(self.assets, self.source, self.configuration)
        cases = [("content", {"transcript": "Changed", "acceptedForms": ["Changed"]}),
                 ("sourceRevision", 2), ("contentRevision", 2),
                 ("construct", "changed"), ("dependencies", ["new:source"])]
        original = deepcopy(self.source)
        for key, value in cases:
            self.source = {**original, key: value}
            self.assertIn("MEDIA_BINDING_STALE", self.verify(reviewed_binding=binding)["issues"])
        self.source = original
        self.configuration["voice"] = "different"
        self.assertIn("MEDIA_BINDING_STALE", self.verify(reviewed_binding=binding)["issues"])

    def test_swapped_asset_order_and_new_asset_revision_are_detected(self):
        other = {**self.assets[0], "logicalId": "synthetic:other"}
        self.assets.append(other)
        binding = media_binding(self.assets, self.source, self.configuration)
        self.assets.reverse()
        self.assertIn("MEDIA_BINDING_STALE", self.verify(reviewed_binding=binding)["issues"])
        self.assets.reverse()
        self.assets[0]["revision"] += 1
        self.assertIn("MEDIA_BINDING_STALE", self.verify(reviewed_binding=binding)["issues"])

    def test_metadata_only_locator_change_reuses_bytes_without_writing(self):
        binding = media_binding(self.assets, self.source, self.configuration)
        before = (self.root / "sample.wav").stat().st_mtime_ns
        self.assets[0]["rightsStatus"] = "redistributable"
        self.assets[0]["locator"] = "./sample.wav"
        report = self.verify(reviewed_binding=binding)
        self.assertEqual(report["assetAction"], "REUSE_BYTES_PENDING_GATES")
        self.assertEqual((self.root / "sample.wav").stat().st_mtime_ns, before)
        self.assertEqual((self.root / "sample.wav").read_bytes(), self.data)

    def test_reported_content_defects_rights_denials_and_dependency_withdrawal(self):
        binding = media_binding(self.assets, self.source, self.configuration)
        self.assertIn("MEDIA_REPORTED_CONTENT_DEFECT",
                      self.verify(reviewed_binding=binding, findings=("wrong spoken key",))["issues"])
        for status in ("unknown", "revoked", "expired", "insufficient", "permission_required"):
            report = self.verify(reviewed_binding=binding, rights_status=status)
            self.assertFalse(report["newUseAllowed"])
            if status != "unknown":
                self.assertIn("MEDIA_RIGHTS_DENIED", report["issues"])
        for ref in ("synthetic:asset", "synthetic:source", "synthetic:ancestor"):
            report = self.verify(reviewed_binding=binding, withdrawn=(ref,))
            self.assertIn("MEDIA_DEPENDENCY_WITHDRAWN", report["issues"])
        self.assertEqual(self.verify(reviewed_binding=binding, withdrawn=("unrelated",))["issues"], [])

    def test_static_svg_safe_profile_rejects_active_and_external_resources(self):
        variants = [SVG.replace(b"</svg>", extra + b"</svg>") for extra in (
            b"<script>alert(1)</script>", b'<image href="https://invalid.example/a"/>',
            b'<foreignObject/>', b'<animate attributeName="x"/>',
            b'<rect width="1" height="1" style="fill:red"/>',
            b'<rect width="1" height="1" fill="url(#remote)"/>',
            b'<text onclick="alert(1)">x</text>')]
        variants += [b'<?xml-stylesheet href="https://invalid.example/a"?>' + SVG,
                     b'<!DOCTYPE svg [<!ENTITY x "test">]>' + SVG,
                     SVG[:-5], SVG.replace(b'width="80"', b'width="0"')]
        path = self.root / "image.svg"
        path.write_bytes(SVG)
        self.assertEqual(_media_issues([asset(SVG, "image.svg", "image")], self.root), [])
        for data in variants:
            path.write_bytes(data)
            self.assertTrue(_media_issues([asset(data, "image.svg", "image")], self.root))

    def test_unknown_decoder_remote_asset_and_malformed_evidence_cannot_grant_use(self):
        self.assets[0]["storage"] = "remote-reference"
        report = self.verify()
        self.assertEqual(report["checks"]["integrity"], "NOT_VERIFIED")
        self.assertFalse(report["newUseAllowed"])
        self.assets.clear()
        self.assertIn("MEDIA_BINDING_INPUT_INVALID", self.verify()["issues"])


if __name__ == "__main__":
    unittest.main()
