"""Synthetic multi-speaker authoring regressions. Never invokes edge-tts."""
from copy import deepcopy
from hashlib import sha256
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from english_action.content import multi_speaker as ms
from english_action.content.spawn import media_binding, verify_media

VOICES = {"A": {"voice": "en-GB-RyanNeural", "locale": "en-GB"},
          "B": {"voice": "en-GB-SoniaNeural", "locale": "en-GB"},
          "C": {"voice": "en-US-GuyNeural", "locale": "en-US"}}


def dialogue(turns, three=False):
    speakers = {k: deepcopy(v) for k, v in VOICES.items() if three or k != "C"}
    return {"source": {"sourceRef": "synthetic:reviewed-script", "sourceRevision": 1,
                       "contentRevision": 1, "objective": "synthetic ordering",
                       "construct": "multi-speaker test only",
                       "dependencies": ["synthetic:ancestor"],
                       "transcript": " ".join(text for _, text, _ in turns)},
            "speakers": speakers,
            "settings": {"edgeTtsVersion": "test-pinned", "rate": "+0%",
                         "pitch": "+0Hz", "volume": "+0%"},
            "utterances": [{"id": f"u{i}", "order": i, "speaker": who,
                            "text": text, "pauseMs": pause}
                           for i, (who, text, pause) in enumerate(turns, 1)]}


def tones(voice, duration=2400, amplitude=6000):
    hz = {"en-GB-RyanNeural": 400, "en-GB-SoniaNeural": 600,
          "en-US-GuyNeural": 800}[voice]
    return b"".join(struct.pack("<h", round(amplitude * math.sin(
        n * 2 * math.pi * hz / ms._RATE))) for n in range(duration))


def write_wav(path, pcm, rate=ms._RATE, channels=1):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(pcm)


class DialogueAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / "published" / "draft.wav"
        self.cache = self.root / "cache"
        self.calls = []

        def render(turn, speaker, settings, dest):
            self.calls.append((turn["id"], turn["text"], speaker["voice"]))
            write_wav(dest, tones(speaker["voice"]))
            return sha256(("synthetic-mp3:" + turn["text"]).encode()).hexdigest()

        patcher = patch.object(ms, "_render", side_effect=render)
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, spec, name="draft"):
        return ms.assemble_dialogue(spec, self.root / "published" / (name + ".wav"), self.cache)

    def test_two_speakers_order_pause_duration_and_integrity(self):
        spec = dialogue([("A", "Hello.", 250), ("B", "Goodbye.", 0)])
        result = self.build(spec)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual([t["speaker"] for t in result["timeline"]], ["A", "B"])
        self.assertEqual(result["durationSamples"], 2400 * 2 + 6000)
        self.assertEqual(result["timeline"][1]["startSample"], 8400)
        with wave.open(str(self.output)) as track:
            pcm = track.readframes(track.getnframes())
            self.assertEqual(track.getframerate(), 24000)
            self.assertEqual(track.getnchannels(), 1)
        self.assertEqual(pcm[:4800], tones(VOICES["A"]["voice"]))
        self.assertEqual(pcm[4800:16800], b"\x00\x00" * 6000)
        self.assertEqual(pcm[16800:], tones(VOICES["B"]["voice"]))
        self.assertEqual(result["asset"]["sha256"], sha256(self.output.read_bytes()).hexdigest())
        self.assertEqual(json.loads(self.output.with_suffix(".json").read_text())["binding"],
                         result["binding"])
        verification = verify_media([result["asset"]], result["source"],
                                    result["configuration"], str(self.output.parent),
                                    reviewed_binding=result["binding"])
        self.assertEqual(verification["checks"]["integrity"], "PASS")
        self.assertEqual(verification["checks"]["dependencyBinding"], "PASS")
        self.assertFalse(verification["publicationEligible"])

    def test_three_speakers_repeated_turns_and_timings(self):
        spec = dialogue([("A", "One", 80), ("B", "Two", 150),
                         ("A", "Three", 90), ("C", "Four", 0)], three=True)
        result = self.build(spec)
        self.assertEqual([t["speaker"] for t in result["timeline"]], ["A", "B", "A", "C"])
        self.assertEqual([t["voice"] for t in result["timeline"]],
                         [VOICES[s]["voice"] for s in ("A", "B", "A", "C")])
        self.assertEqual(result["durationSamples"], 9600 + (80 + 150 + 90) * 24)
        self.assertEqual([t["startSample"] for t in result["timeline"]],
                         [0, 4320, 10320, 14880])
        self.assertEqual(result["source"]["content"]["transcript"], "One Two Three Four")

    def test_same_spec_is_immutable_and_reuses_every_utterance(self):
        spec = dialogue([("A", "One", 100), ("B", "Two", 0)])
        first = self.build(spec)
        second = self.build(spec)
        self.assertEqual(first, second)
        self.assertEqual(len(self.calls), 2)

    def test_text_change_invalidates_only_one_segment(self):
        spec = dialogue([("A", "One", 0), ("B", "Two", 0)])
        first = self.build(spec)
        changed = deepcopy(spec)
        changed["source"]["contentRevision"] += 1
        changed["utterances"][1]["text"] = "Two revised"
        changed["source"]["transcript"] = "One Two revised"
        second = self.build(changed, "revision2")
        self.assertEqual(len(self.calls), 3)
        self.assertNotEqual(first["binding"], second["binding"])
        self.assertEqual(first["timeline"][0]["segment"], second["timeline"][0]["segment"])
        self.assertNotEqual(first["asset"]["logicalId"], second["asset"]["logicalId"])

    def test_voice_change_invalidates_only_affected_speaker(self):
        spec = dialogue([("A", "One", 0), ("B", "Two", 0)])
        original = self.build(spec)
        changed = deepcopy(spec)
        changed["speakers"]["B"] = deepcopy(VOICES["C"])
        changed["source"]["contentRevision"] += 1
        updated = self.build(changed, "voice2")
        self.assertEqual(len(self.calls), 3)
        self.assertNotEqual(original["binding"], updated["binding"])
        self.assertNotEqual(original["timeline"][1]["voice"], updated["timeline"][1]["voice"])

    def test_source_revision_change_changes_binding_not_reusable_utterance(self):
        spec = dialogue([("A", "One", 0), ("B", "Two", 0)])
        first = self.build(spec)
        spec["source"]["sourceRevision"] += 1
        second = self.build(spec, "source2")
        self.assertEqual(len(self.calls), 2)
        self.assertNotEqual(first["binding"], second["binding"])
        self.assertEqual(first["timeline"][0]["segment"], second["timeline"][0]["segment"])

    def test_cached_corruption_is_rejected(self):
        spec = dialogue([("A", "One", 0), ("B", "Two", 0)])
        self.build(spec)
        file = next(self.cache.glob("*.wav"))
        file.write_bytes(b"broken")
        with self.assertRaisesRegex(ValueError, "CACHED_AUDIO_STALE"):
            self.build(spec, "corrupt")
        self.assertFalse((self.output.parent / "corrupt.wav").exists())

    def test_missing_cached_segment_is_rejected(self):
        spec = dialogue([("A", "One", 0), ("B", "Two", 0)])
        self.build(spec)
        next(self.cache.glob("*.wav")).unlink()
        with self.assertRaisesRegex(ValueError, "CACHED_AUDIO_MISSING"):
            self.build(spec, "missing")

    def test_removed_middle_turn_rejected_against_reviewed_transcript(self):
        spec = dialogue([("A", "First", 0), ("B", "Second", 0),
                         ("A", "Third", 0)])
        removed = spec["utterances"].pop(1)
        self.assertEqual(removed["text"], "Second")
        spec["utterances"][1]["order"] = 2
        with self.assertRaisesRegex(ValueError, "TRANSCRIPT_UTTERANCES_MISMATCH"):
            self.build(spec, "missing-turn")
        self.assertFalse(self.calls)

    def test_modified_utterance_without_reviewed_transcript_change_rejected(self):
        spec = dialogue([("A", "First", 0), ("B", "Second", 0)])
        spec["utterances"][0]["text"] = "Wrong"
        with self.assertRaisesRegex(ValueError, "TRANSCRIPT_UTTERANCES_MISMATCH"):
            self.build(spec, "unreviewed-turn")
        self.assertFalse(self.calls)

    def test_duplicate_id_and_wrong_sequence_rejected(self):
        spec = dialogue([("A", "One", 0), ("B", "Two", 0)])
        for mutate in ("duplicate", "sequence", "speaker"):
            bad = deepcopy(spec)
            if mutate == "duplicate":
                bad["utterances"][1]["id"] = "u1"
            elif mutate == "sequence":
                bad["utterances"][1]["order"] = 3
            else:
                bad["utterances"][1]["speaker"] = "unknown"
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                self.build(bad, mutate)
        self.assertFalse(self.calls)

    def test_duplicate_voice_or_invalid_locale_rejected(self):
        spec = dialogue([("A", "One", 0), ("B", "Two", 0)])
        spec["speakers"]["B"] = deepcopy(spec["speakers"]["A"])
        with self.assertRaisesRegex(ValueError, "SPEAKER_VOICE_DUPLICATE"):
            self.build(spec)
        spec["speakers"]["B"]["locale"] = "en-US"
        with self.assertRaisesRegex(ValueError, "SPEAKER_VOICE_INVALID"):
            self.build(spec)

    def test_long_text_and_long_pause_rejected(self):
        spec = dialogue([("A", "x" * 501, 0), ("B", "Two", 0)])
        with self.assertRaises(ValueError):
            self.build(spec)
        spec["utterances"][0]["text"] = "One"
        spec["utterances"][0]["pauseMs"] = 4000
        with self.assertRaises(ValueError):
            self.build(spec)

    def test_bad_audio_format_rejected(self):
        def wrong_rate(turn, speaker, settings, dest):
            write_wav(dest, tones(speaker["voice"]), rate=16000)
            return "f" * 64
        spec = dialogue([("A", "One", 0), ("B", "Two", 0)])
        with patch.object(ms, "_render", side_effect=wrong_rate), self.assertRaisesRegex(
                ValueError, "AUDIO_FORMAT_UNSUPPORTED"):
            self.build(spec)
        self.assertFalse(self.output.exists())

    def test_silence_and_clipping_rejected(self):
        spec = dialogue([("A", "One", 0), ("B", "Two", 0)])
        for case, pcm in (("silent", b"\x00\x00" * 2400),
                          ("clipped", b"\xff\x7f\x00\x80" * 1200)):
            def corrupt(turn, speaker, settings, dest):
                write_wav(dest, pcm)
                return "f" * 64
            with self.subTest(case=case), patch.object(ms, "_render", side_effect=corrupt):
                with self.assertRaisesRegex(ValueError, "AUDIO_INTEGRITY"):
                    self.build(spec, case)
            self.assertFalse((self.output.parent / (case + ".wav")).exists())

    def test_excessive_speaker_loudness_difference_rejected(self):
        spec = dialogue([("A", "One", 0), ("B", "Two", 0)])
        def unbalanced(turn, speaker, settings, dest):
            amplitude = 6000 if turn["speaker"] == "A" else 500
            write_wav(dest, tones(speaker["voice"], amplitude=amplitude))
            return "a" * 64
        with patch.object(ms, "_render", side_effect=unbalanced):
            with self.assertRaisesRegex(ValueError, "SPEAKER_LOUDNESS_IMBALANCE"):
                self.build(spec)
        self.assertFalse(self.output.exists())

    def test_failure_does_not_clobber_existing_valid_output(self):
        original = dialogue([("A", "Old one", 0), ("B", "Old two", 0)])
        before = self.build(original)
        original_bytes = self.output.read_bytes()
        revised = deepcopy(original)
        revised["source"]["contentRevision"] += 1
        revised["utterances"][1]["text"] = "New two"
        revised["source"]["transcript"] = "Old one New two"
        def fail(turn, speaker, settings, dest):
            raise ValueError("mock provider failure")
        with patch.object(ms, "_render", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "mock provider failure"):
                self.build(revised)
        self.assertEqual(self.output.read_bytes(), original_bytes)
        self.assertEqual(json.loads(self.output.with_suffix(".json").read_text())["binding"],
                         before["binding"])

    def test_incomplete_cache_and_immutable_output_conflict_fail_closed(self):
        spec = dialogue([("A", "One", 0), ("B", "Two", 0)])
        first = self.build(spec)
        for f in self.cache.glob("*.json"):
            f.unlink()
            break
        with self.assertRaisesRegex(ValueError, "CACHED_AUDIO_INCOMPLETE"):
            self.build(spec)
        self.assertEqual(first["asset"]["sha256"], sha256(self.output.read_bytes()).hexdigest())

    def test_changed_transcript_and_voice_are_in_binding(self):
        spec = dialogue([("A", "One", 0), ("B", "Two", 0)])
        result = self.build(spec)
        src = deepcopy(result["source"])
        src["content"]["utterances"][0]["text"] = "changed"
        self.assertNotEqual(media_binding([result["asset"]], src, result["configuration"]),
                            result["binding"])
        settings = deepcopy(result["configuration"])
        settings["rate"] = "+25%"
        self.assertNotEqual(media_binding([result["asset"]], result["source"], settings),
                            result["binding"])

    def test_local_ffmpeg_mp3_to_wav_normalization_when_available(self):
        if shutil.which("ffmpeg") is None:
            self.skipTest("ffmpeg not present; actual normalization NOT VERIFIED")
        source = self.root / "tone.wav"
        mp3 = self.root / "tone.mp3"
        decoded = self.root / "decoded.wav"
        write_wav(source, tones(VOICES["A"]["voice"], duration=4800), rate=24000)
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i",
                        str(source), "-c:a", "libmp3lame", str(mp3)],
                       check=True, timeout=30, capture_output=True)
        ms._normalize_mp3(mp3, decoded)
        _, pcm, frames, rms = ms._read_pcm(decoded)
        self.assertGreater(frames, 0)
        self.assertGreater(rms, 50)
        self.assertTrue(pcm)


if __name__ == "__main__":
    unittest.main()
