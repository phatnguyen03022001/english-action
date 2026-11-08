"""Local, draft-only edge-tts dialogue authoring. Never run synthesis in CI."""
import argparse
from hashlib import sha256
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import wave

from .spawn import _media_bytes_issues, media_binding

_RATE = 24000
_MAX_UTTERANCES = 40
_MAX_SECONDS = 300
_MAX_BYTES = 32 * 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_VOICE = re.compile(r"[a-z]{2}-[A-Z]{2}-[A-Za-z0-9]+Neural\Z")
_LOCALE = re.compile(r"[a-z]{2}-[A-Z]{2}\Z")
_PERCENT = re.compile(r"[+-]\d{1,3}%\Z")
_PITCH = re.compile(r"[+-]\d{1,3}Hz\Z")


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate(spec):
    """Reject ambiguous order, voices, scripts and unbounded authoring requests."""
    if not isinstance(spec, dict) or set(spec) != {"source", "speakers", "utterances", "settings"}:
        raise ValueError("DIALOGUE_FIELDS_INVALID")
    src, speakers, turns, settings = (spec[k] for k in ("source", "speakers", "utterances", "settings"))
    if not isinstance(src, dict) or set(src) != {
            "sourceRef", "sourceRevision", "contentRevision", "objective", "construct", "dependencies", "transcript"}:
        raise ValueError("SOURCE_INVALID")
    if (not all(isinstance(src[k], str) and src[k].strip() for k in
                ("sourceRef", "objective", "construct"))
            or any(type(src[k]) is not int or src[k] < 1
                   for k in ("sourceRevision", "contentRevision"))
            or not isinstance(src["dependencies"], list)
            or any(not isinstance(d, str) or not d.strip() for d in src["dependencies"])
            or not isinstance(src["transcript"], str) or not src["transcript"].strip()):
        raise ValueError("SOURCE_INVALID")
    if not isinstance(speakers, dict) or not 2 <= len(speakers) <= 5:
        raise ValueError("SPEAKERS_INVALID")
    for key, speaker in speakers.items():
        if (not isinstance(key, str) or not _ID.fullmatch(key)
                or not isinstance(speaker, dict) or set(speaker) != {"voice", "locale"}
                or not isinstance(speaker["voice"], str)
                or not isinstance(speaker["locale"], str)
                or not _VOICE.fullmatch(speaker["voice"])
                or not _LOCALE.fullmatch(speaker["locale"])
                or not speaker["voice"].startswith(speaker["locale"] + "-")):
            raise ValueError("SPEAKER_VOICE_INVALID")
    if len({s["voice"] for s in speakers.values()}) != len(speakers):
        raise ValueError("SPEAKER_VOICE_DUPLICATE")
    if not isinstance(settings, dict) or set(settings) != {
            "edgeTtsVersion", "rate", "pitch", "volume"}:
        raise ValueError("SYNTHESIS_SETTINGS_INVALID")
    if (not isinstance(settings["edgeTtsVersion"], str)
            or not 1 <= len(settings["edgeTtsVersion"]) <= 64
            or not _PERCENT.fullmatch(str(settings["rate"]))
            or not _PERCENT.fullmatch(str(settings["volume"]))
            or not _PITCH.fullmatch(str(settings["pitch"]))):
        raise ValueError("SYNTHESIS_SETTINGS_INVALID")
    if not isinstance(turns, list) or not 2 <= len(turns) <= _MAX_UTTERANCES:
        raise ValueError("UTTERANCES_INVALID")
    ids = set()
    for index, turn in enumerate(turns, 1):
        if (not isinstance(turn, dict)
                or set(turn) != {"id", "order", "speaker", "text", "pauseMs"}
                or not isinstance(turn["id"], str) or not _ID.fullmatch(turn["id"])
                or turn["id"] in ids or type(turn["order"]) is not int
                or turn["order"] != index
                or turn["speaker"] not in speakers
                or not isinstance(turn["text"], str)
                or not turn["text"].strip() or len(turn["text"]) > 500
                or type(turn["pauseMs"]) is not int
                or not 0 <= turn["pauseMs"] <= 3000):
            raise ValueError("UTTERANCE_ORDER_OR_FIELDS_INVALID")
        ids.add(turn["id"])
    if sum(len(t["text"]) for t in turns) > 8000:
        raise ValueError("DIALOGUE_TOO_LARGE")
    if src["transcript"] != " ".join(turn["text"] for turn in turns):
        raise ValueError("TRANSCRIPT_UTTERANCES_MISMATCH")
    if len({t["speaker"] for t in turns}) < 2:
        raise ValueError("SPEAKER_TURNS_MISSING")


def _read_pcm(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_BYTES:
        raise ValueError("AUDIO_NOT_AVAILABLE_OR_OVERSIZE")
    data = path.read_bytes()
    issues = _media_bytes_issues(data, ".wav", "audio")
    if issues:
        raise ValueError("AUDIO_INTEGRITY:" + ",".join(issues))
    with wave.open(BytesIO(data), "rb") as stream:
        if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate(),
            stream.getcomptype()) != (1, 2, _RATE, "NONE"):
            raise ValueError("AUDIO_FORMAT_UNSUPPORTED")
        frames = stream.getnframes()
        pcm = stream.readframes(frames)
    if frames == 0 or frames > _RATE * _MAX_SECONDS:
        raise ValueError("AUDIO_DURATION_INVALID")
    rms = math.sqrt(sum(v[0] * v[0] for v in struct.iter_unpack("<h", pcm)) / frames)
    if rms < 50:
        raise ValueError("AUDIO_TOO_QUIET")
    return data, pcm, frames, rms


def _normalize_mp3(source, target):
    """FFmpeg only decodes and normalizes; it does not synthesize speech."""
    try:
        subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error",
                        "-xerror", "-y", "-i", str(source), "-vn", "-ac", "1",
                        "-ar", str(_RATE), "-c:a", "pcm_s16le", str(target)],
                       check=True, capture_output=True, timeout=75)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        raise ValueError("MP3_NORMALIZATION_FAILED") from error


def _render(turn, speaker, settings, wav_path):
    """Only allowed real renderer: locally installed edge-tts CLI."""
    with tempfile.TemporaryDirectory(prefix="edge-tts-") as tmp:
        mp3 = Path(tmp) / "utterance.mp3"
        try:
            subprocess.run(["edge-tts", "--text", turn["text"],
                            "--voice", speaker["voice"],
                            "--rate=" + settings["rate"], "--pitch=" + settings["pitch"],
                            "--volume=" + settings["volume"], "--write-media", str(mp3)],
                           check=True, capture_output=True, timeout=75)
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise ValueError("EDGE_TTS_UTTERANCE_FAILED") from error
        if (not mp3.is_file() or mp3.stat().st_size < 128
                or mp3.stat().st_size > _MAX_BYTES):
            raise ValueError("EDGE_TTS_OUTPUT_INVALID")
        raw_sha = sha256(mp3.read_bytes()).hexdigest()
        _normalize_mp3(mp3, wav_path)
        return raw_sha


def _write_new(path, data):
    """Never overwrite an existing revision or a different valid asset."""
    if path.exists() or path.is_symlink():
        if path.is_symlink() or path.read_bytes() != data:
            raise ValueError("IMMUTABLE_OUTPUT_CONFLICT")
        return
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".audio-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if path.exists():
            if path.read_bytes() != data:
                raise ValueError("IMMUTABLE_OUTPUT_CONFLICT")
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _utterance_audio(turn, speaker, settings, cache):
    key = _digest({"text": turn["text"], "speaker": turn["speaker"],
                   "voice": speaker["voice"], "locale": speaker["locale"],
                   "settings": settings, "format": "pcm-s16le-24000-mono-v1"})
    wav_path, info_path = cache / (key + ".wav"), cache / (key + ".json")
    if info_path.exists():
        if not wav_path.is_file() or wav_path.is_symlink():
            raise ValueError("CACHED_AUDIO_MISSING")
        info = json.loads(info_path.read_text(encoding="utf-8"))
        if info.get("key") != key or info.get("sha256") != sha256(wav_path.read_bytes()).hexdigest():
            raise ValueError("CACHED_AUDIO_STALE")
        data, pcm, frames, rms = _read_pcm(wav_path)
        return {"key": key, "sha256": sha256(data).hexdigest(),
                "sourceMp3Sha256": info["sourceMp3Sha256"]}, pcm, frames, rms
    if wav_path.exists():
        raise ValueError("CACHED_AUDIO_INCOMPLETE")
    with tempfile.TemporaryDirectory(dir=cache, prefix=".utterance-") as tmp:
        temp_path = Path(tmp) / "utterance.wav"
        mp3_sha = _render(turn, speaker, settings, temp_path)
        data, pcm, frames, rms = _read_pcm(temp_path)
        meta = {"key": key, "sha256": sha256(data).hexdigest(),
                "sourceMp3Sha256": mp3_sha}
        _write_new(wav_path, data)
        _write_new(info_path, (json.dumps(meta, sort_keys=True) + "\n").encode("utf-8"))
    return meta, pcm, frames, rms


def _wav_bytes(pcm):
    output = BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(_RATE)
        stream.writeframes(pcm)
    return output.getvalue()


def assemble_dialogue(spec, output, cache_dir):
    """Create immutable WAV + evidence sidecar; no publication decision."""
    _validate(spec)
    output, cache = Path(output), Path(cache_dir)
    if output.suffix.lower() != ".wav" or output.is_symlink() or cache.is_symlink():
        raise ValueError("OUTPUT_PATH_INVALID")
    output.parent.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    if output.resolve().is_relative_to(cache.resolve()) or cache.resolve().is_relative_to(output.resolve()):
        raise ValueError("OUTPUT_CACHE_COLLISION")
    timeline, samples, levels = [], [], []
    cursor = 0
    for turn in spec["utterances"]:
        speaker = spec["speakers"][turn["speaker"]]
        meta, pcm, frames, rms = _utterance_audio(turn, speaker, spec["settings"], cache)
        levels.append(rms)
        pause_frames = round(turn["pauseMs"] * _RATE / 1000)
        timeline.append({"id": turn["id"], "order": turn["order"],
                         "speaker": turn["speaker"], "voice": speaker["voice"],
                         "text": turn["text"], "segment": meta,
                         "startSample": cursor, "speechSamples": frames,
                         "pauseSamples": pause_frames})
        samples.append(pcm)
        samples.append(b"\x00\x00" * pause_frames)
        cursor += frames + pause_frames
        if cursor > _RATE * _MAX_SECONDS or cursor * 2 + 44 > _MAX_BYTES:
            raise ValueError("ASSEMBLED_DURATION_OR_SIZE_LIMIT")
    if 20 * math.log10(max(levels) / min(levels)) > 12:
        raise ValueError("SPEAKER_LOUDNESS_IMBALANCE")
    final_data = _wav_bytes(b"".join(samples))
    issues = _media_bytes_issues(final_data, ".wav", "audio")
    if issues:
        raise ValueError("ASSEMBLED_AUDIO_INVALID:" + ",".join(issues))
    src = {k: v for k, v in spec["source"].items() if k != "transcript"}
    src["content"] = {"transcript": spec["source"]["transcript"],
                      "speakers": spec["speakers"], "utterances": spec["utterances"]}
    asset = {"logicalId": "dialogue:" + _digest({"source": src, "settings": spec["settings"]})[:24],
             "revision": spec["source"]["contentRevision"], "mediaType": "audio",
             "sha256": sha256(final_data).hexdigest(), "storage": "local-fixture",
             "locator": output.name, "provider": "edge-tts",
             "model": spec["settings"]["edgeTtsVersion"], "rightsStatus": "unknown",
             "accessibility": src["content"]["transcript"]}
    config = {**spec["settings"], "speakers": spec["speakers"],
              "assembly": "pcm-s16le-24000-mono-sequential-v1"}
    result = {"asset": asset, "source": src, "configuration": config,
              "binding": media_binding([asset], src, config),
              "timeline": timeline, "durationSamples": cursor,
              "checks": {"audioIntegrity": "PASS", "actualSpeech": "NOT_VERIFIED",
                         "editorial": "NOT_VERIFIED", "publication": "HOLD"}}
    manifest_path = output.with_suffix(".json")
    manifest = (json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    _write_new(output, final_data)
    _write_new(manifest_path, manifest)
    return result


def main():
    parser = argparse.ArgumentParser(description="Local edge-tts multi-speaker draft authoring")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    args = parser.parse_args()
    result = assemble_dialogue(json.loads(args.spec.read_text(encoding="utf-8")),
                               args.output, args.cache_dir)
    print(json.dumps({"audio": str(args.output), "sha256": result["asset"]["sha256"],
                      "binding": result["binding"], "status": "HOLD"}))


if __name__ == "__main__":
    main()
