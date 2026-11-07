"""Synthetic byte/binding regressions. No reviewer or rights approvals."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
import wave
import zlib

from PIL import Image
from unittest.mock import patch

from english_action.content.spawn import (
    _media_issues, media_binding, verify_media,
)


def wav_bytes(silent=False):
    stream = BytesIO()
    with wave.open(stream, "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * 80 if silent else b"\xe8\x03\x18\xfc" * 40)
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

    def test_wav_resource_limit_applies_before_reading_bytes(self):
        with patch("english_action.content.spawn._MAX_MEDIA_BYTES", len(self.data) - 1):
            report = self.verify()
            self.assertIn("MEDIA_RESOURCE_LIMIT", report["issues"])
            self.assertEqual(report["checks"]["integrity"], "FAIL")

    def test_pcm_constant_dc_and_hard_clipping_are_rejected(self):
        for samples, reason in ((b"\xe8\x03" * 80, "AUDIO_SILENT"),
                                (b"\xff\x7f\x00\x80" * 40, "AUDIO_CLIPPED")):
            stream = BytesIO()
            with wave.open(stream, "wb") as audio:
                audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                audio.writeframes(samples)
            payload = stream.getvalue()
            (self.root / "sample.wav").write_bytes(payload)
            self.assets[0] = asset(payload)
            with self.subTest(reason=reason):
                report = self.verify()
                self.assertIn(reason, report["issues"])
                self.assertEqual(report["checks"]["integrity"], "FAIL")
                self.assertFalse(report["publicationEligible"])

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


    def test_recognized_bytes_must_match_declared_modality_even_with_valid_hash(self):
        for data, locator, wrong_kind in ((SVG, "image.svg", "audio"),
                                         (self.data, "sample.wav", "image"),
                                         (SVG, "image.bin", "audio"),
                                         (self.data, "sample.bin", "image")):
            with self.subTest(locator=locator, wrong_kind=wrong_kind):
                (self.root / locator).write_bytes(data)
                self.assets = [asset(data, locator, wrong_kind)]
                binding = media_binding(self.assets, self.source, self.configuration)
                report = self.verify(reviewed_binding=binding)
                self.assertIn("MEDIA_TYPE_MISMATCH", report["issues"])
                self.assertEqual(report["checks"]["integrity"], "FAIL")
                self.assertEqual(report["assetAction"], "REVALIDATE_AFFECTED_DEPENDENCIES")
                self.assertFalse(report["publicationEligible"])
                self.assertFalse(report["newUseAllowed"])

    def test_unknown_decoder_remote_asset_and_malformed_evidence_cannot_grant_use(self):
        self.assets[0]["storage"] = "remote-reference"
        report = self.verify()
        self.assertEqual(report["checks"]["integrity"], "NOT_VERIFIED")
        self.assertFalse(report["newUseAllowed"])
        self.assets.clear()
        self.assertIn("MEDIA_BINDING_INPUT_INVALID", self.verify()["issues"])



class RasterMediaTests(unittest.TestCase):
    """Real decoder-level tests with tiny generated images, never semantic approval."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = source()
        self.configuration = {"render": "synthetic"}

    def encoded(self, fmt):
        stream = BytesIO()
        Image.new("RGB", (4, 3), (20, 80, 140)).save(
            stream, format=fmt, **({"lossless": True} if fmt == "WEBP" else {}))
        return stream.getvalue()

    def report(self, data, filename, kind="image", digest=None):
        (self.root / filename).write_bytes(data)
        a = asset(data, filename, kind)
        if digest is not None:
            a["sha256"] = digest
        return verify_media([a], self.source, self.configuration, str(self.root))

    def test_valid_png_and_webp_decode_and_never_grant_publication(self):
        for fmt in ("PNG", "WEBP"):
            with self.subTest(fmt=fmt):
                suffix = fmt.lower()
                data = self.encoded(fmt)
                with Image.open(BytesIO(data)) as decoded:
                    self.assertEqual(decoded.format, fmt)
                    self.assertEqual(decoded.size, (4, 3))
                    decoded.load()
                report = self.report(data, "image." + suffix)
                self.assertEqual(report["issues"], [])
                self.assertEqual(report["checks"]["integrity"], "PASS")
                for check in ("actualContent", "rightsClearance", "editorialAuthorization"):
                    self.assertEqual(report["checks"][check], "NOT_VERIFIED")
                self.assertEqual(report["status"], "HOLD")
                self.assertFalse(report["publicationEligible"])
                self.assertFalse(report["newUseAllowed"])

    def test_png_corruption_and_truncation_fail_with_recomputed_hash(self):
        data = self.encoded("PNG")
        # Valid PNG signature/IHDR, but tampered IDAT CRC.
        index = data.index(b"IDAT") + 4
        corrupt = data[:index] + bytes([data[index] ^ 0xff]) + data[index + 1:]
        for payload in (corrupt, data[:-8], data[:16], b"not-a-png"):
            with self.subTest(length=len(payload)):
                report = self.report(payload, "image.png")
                self.assertIn("IMAGE_BYTES_INVALID", report["issues"])
                self.assertEqual(report["checks"]["integrity"], "FAIL")

    def test_webp_corruption_truncation_and_riff_dispatch(self):
        data = self.encoded("WEBP")
        self.assertEqual(data[:4], b"RIFF")
        self.assertEqual(data[8:12], b"WEBP")
        self.assertEqual(self.report(data, "image.webp")["issues"], [])
        for payload in (data[:-6], data[:12], data[:4] + b"\x00" * 4 + data[8:],
                        data[:8] + b"NOPE" + data[12:]):
            with self.subTest(length=len(payload)):
                report = self.report(payload, "image.webp")
                self.assertIn("IMAGE_BYTES_INVALID", report["issues"])
        # Even valid RIFF/WebP is never treated as audio.
        self.assertIn("MEDIA_TYPE_MISMATCH", self.report(data, "image.webp", "audio")["issues"])

    def test_mismatched_hash_extension_and_declared_media_type(self):
        png, webp = self.encoded("PNG"), self.encoded("WEBP")
        self.assertIn("MEDIA_HASH_MISMATCH",
                      self.report(png, "image.png", digest="f" * 64)["issues"])
        for payload, name, kind in ((png, "image.webp", "image"),
                                    (webp, "image.png", "image"),
                                    (png, "image.bin", "image"),
                                    (webp, "image.webp", "audio"),
                                    (wav_bytes(), "image.wav", "image"),
                                    (wav_bytes(), "image.png", "image"),
                                    (SVG, "image.png", "image")):
            with self.subTest(name=name, kind=kind):
                self.assertIn("MEDIA_TYPE_MISMATCH",
                              self.report(payload, name, kind)["issues"])
        for name, payload in (("image.gif", b"GIF89a"), ("image.bin", b"unknown")):
            self.assertIn("MEDIA_ENCODING_UNSUPPORTED",
                          self.report(payload, name)["issues"])

    def test_dimensions_pixels_and_encoded_size_are_bounded(self):
        data = self.encoded("PNG")
        # Only change IHDR dimensions and CRC; no huge allocation in tests.
        def dimensions(width, height):
            header = (data[:16] + width.to_bytes(4, "big") + height.to_bytes(4, "big")
                      + data[24:29])
            crc = zlib.crc32(header[12:29]).to_bytes(4, "big")
            return header + crc + data[33:]
        for width, height in ((10001, 1), (5000, 6000)):
            with self.subTest(size=(width, height)):
                self.assertIn("IMAGE_DIMENSIONS_INVALID",
                              self.report(dimensions(width, height), "image.png")["issues"])
        self.assertTrue({"IMAGE_BYTES_INVALID", "IMAGE_DIMENSIONS_INVALID"} &
                        set(self.report(dimensions(0, 1), "image.png")["issues"]))
        with patch("english_action.content.spawn._MAX_MEDIA_BYTES", 16):
            self.assertIn("MEDIA_RESOURCE_LIMIT",
                          self.report(data, "image.png")["issues"])

    def test_animated_webp_is_not_an_authorized_static_asset(self):
        stream = BytesIO()
        first = Image.new("RGB", (4, 3), "red")
        second = Image.new("RGB", (4, 3), "blue")
        first.save(stream, format="WEBP", save_all=True, append_images=[second],
                   duration=100, loop=0, lossless=True)
        data = stream.getvalue()
        with Image.open(BytesIO(data)) as image:
            self.assertGreater(image.n_frames, 1)
        self.assertIn("IMAGE_ANIMATED_UNSUPPORTED",
                      self.report(data, "animation.webp")["issues"])

    def test_valid_decoded_image_with_missing_review_and_rights_stays_hold(self):
        data = self.encoded("PNG")
        (self.root / "image.png").write_bytes(data)
        a = asset(data, "image.png", "image")
        binding = media_binding([a], self.source, self.configuration)
        report = verify_media([a], self.source, self.configuration, str(self.root),
                              reviewed_binding=binding, rights_status="cleared")
        self.assertEqual(report["checks"]["integrity"], "PASS")
        self.assertEqual(report["checks"]["dependencyBinding"], "PASS")
        self.assertEqual(report["checks"]["actualContent"], "NOT_VERIFIED")
        self.assertEqual(report["checks"]["rightsClearance"], "NOT_VERIFIED")
        self.assertEqual(report["checks"]["editorialAuthorization"], "NOT_VERIFIED")
        self.assertFalse(report["publicationEligible"])
        self.assertFalse(report["newUseAllowed"])

if __name__ == "__main__":
    unittest.main()
