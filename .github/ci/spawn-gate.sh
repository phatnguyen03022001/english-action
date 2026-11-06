#!/usr/bin/env bash
# Existing synthetic Spawn assertions migrated without private fixtures.
set -euo pipefail
mkdir -p "$RUNNER_TEMP/spawn-media/assets/synthetic" "$RUNNER_TEMP/spawn-private"
chmod 700 "$RUNNER_TEMP/spawn-private"
python - <<'PY'
import hashlib
import math
import os
from pathlib import Path
import struct
import wave

directory = Path(os.environ["RUNNER_TEMP"]) / "spawn-media/assets/synthetic"
expected = {
    440: "3264a7d28bf425a32453c379237defe696b79f93de45988b7cb0391bf7aa212c",
    660: "9c21e39474073e196835204795078f327ca4bf65833a08266543afa12c9ded06",
}
for hz, digest in expected.items():
    path = directory / f"{hz}.wav"
    frames = b"".join(struct.pack("<h", int(2000 * math.sin(
        i * 2 * math.pi * hz / 8000))) for i in range(160))
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(8000)
        stream.writeframes(frames)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
print("Synthetic test tones verified; NOT pronunciation assets or licensed audio.")
PY

set -euo pipefail
python -m english_action.content.cli schemas-check --schemas schemas
python -m english_action.content.spawn \
  --input data/spawn/seeds/foundation-p0a-synthetic.json \
  --contract schemas/spawn/foundation-p0a.schema.json \
  --schemas schemas \
  --quality-input data/candidates/pilot-fanout-001.json \
  --media-root "$RUNNER_TEMP/spawn-media" \
  --state "$RUNNER_TEMP/spawn-private/state.json" \
  --concurrency 3 --timeout 15 --retries 1 > "$RUNNER_TEMP/spawn-private/report.json"
python - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["RUNNER_TEMP"]) / "spawn-private"
state = json.loads((root / "state.json").read_text())
report = json.loads((root / "report.json").read_text())
recipes = {"N1-01", "N2-06", "N3-11", "K1-16", "K1-17", "K2-21", "N2-10"}
assert report["generationMode"] == "synthetic"
assert report["quality"] == report["lexicalSourceQuality"]
assert report["lexicalSourceQuality"]["status"] == "PASS"
generated = report["generatedContentQuality"]
assert generated["status"] == "NOT_VERIFIED"
assert len(generated["candidates"]) == 7
for evidence in generated["candidates"]:
    candidate = state["candidates"][evidence["candidateId"]]
    assert candidate["generatedContentQuality"] == evidence
    assert candidate["revision"] == evidence["revision"] == 1
    assert candidate["contentHash"] == evidence["contentHash"]
    assert evidence["checks"]["recipeContent"] == "PASS"
    assert evidence["checks"]["producerContract"] == "PASS"
    assert evidence["checks"]["sourceReferenceResolution"] == "NOT_VERIFIED"
    assert evidence["checks"]["editorialCorrectness"] == "NOT_VERIFIED"
    if candidate["recipe"] in {"N3-11", "K1-16", "K1-17"}:
        assert evidence["checks"]["mediaMetadata"] == "PASS"
        assert evidence["checks"]["localMediaIntegrity"] == "PASS"
    assert candidate["rights"]["clearance"] == "UNVERIFIED"
    assert candidate["rights"]["authenticatedEvidence"] == []
    media = candidate["content"].get("assets", [])
    if "audio" in candidate["content"]:
        media = [candidate["content"]["audio"]]
    assert all(asset["rightsStatus"] == "unknown" for asset in media)
assert report["generated"] == report["drafts"] == 7 and report["rejected"] == 0
assert not report["publicationEligible"]
assert len(state["history"]) == len(state["candidates"]) == 7
assert {r["recipe"] for r in state["candidates"].values()} == recipes
assert all(r["status"] == "HOLD" and r["unresolvedEvidence"]
           and r["provenance"]["mode"] == "synthetic"
           for r in state["candidates"].values())
print("PRIVATE SYNTHETIC DRAFT PASS: 7/7 HOLD, independent publication blocked")
PY

set -euo pipefail
python -m english_action.content.spawn \
  --input data/spawn/seeds/foundation-p0a-synthetic.json \
  --contract schemas/spawn/foundation-p0a.schema.json \
  --state "$RUNNER_TEMP/spawn-private/state.json" > "$RUNNER_TEMP/spawn-private/replay.json"
python - <<'PY'
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
from english_action.content.spawn import generate, load_contract, SyntheticWorker
root = Path(os.environ["RUNNER_TEMP"]) / "spawn-private"
original = json.loads(Path("data/spawn/seeds/foundation-p0a-synthetic.json").read_text())
replay = json.loads((root / "replay.json").read_text())
assert replay["replayed"] is True
state_path = root / "state.json"
before = state_path.read_bytes()
assert len(json.loads(before)["history"]) == 7
conflicted = deepcopy(original)
conflicted["seeds"][0]["request"]["replayTest"] = True
conflict_path = root / "conflict.json"
conflict_path.write_text(json.dumps(conflicted))
result = subprocess.run(
    [sys.executable, "-m", "english_action.content.spawn", "--input", str(conflict_path),
     "--contract", "schemas/spawn/foundation-p0a.schema.json", "--state", str(state_path)],
    capture_output=True, text=True, check=False,
)
assert result.returncode == 1
assert json.loads(result.stderr)["reason"] == "BATCH_REPLAY_CONFLICT"
assert state_path.read_bytes() == before
validator = load_contract("schemas/spawn/foundation-p0a.schema.json")
for seed in original["seeds"]:
    sample = deepcopy(seed)
    sample["fixture"].pop(next(iter(sample["fixture"])))
    result = asyncio.run(generate({"batchId": "negative", "seeds": [sample]},
                                  SyntheticWorker(), contract=validator))
    assert result[0]["status"] == "REJECTED", seed["recipe"]
    assert result[0]["generatedContentQuality"]["checks"]["producerContract"] == "FAIL"
valid_recipe_invalid_contract = deepcopy(original["seeds"][0])
valid_recipe_invalid_contract["request"] = {}
valid_recipe_invalid_contract["sourceRevision"] = 2
private_rule = type(validator)({"properties": {"sourceRevision": {"const": 1}}})
rejected = asyncio.run(generate(
    {"batchId": "private-rule", "seeds": [valid_recipe_invalid_contract]},
    SyntheticWorker(), contract=private_rule))[0]
assert rejected["issues"] == ["PRIVATE_CONTRACT_INVALID"]
assert rejected["generatedContentQuality"]["checks"]["recipeContent"] == "PASS"
bad = deepcopy(original["seeds"][3])
bad["fixture"]["audio"]["sha256"] = "f" * 64
bad_result = asyncio.run(generate({"batchId": "media-mismatch", "seeds": [bad]},
                                   SyntheticWorker(), contract=validator,
                                   media_root=str(Path(os.environ["RUNNER_TEMP"]) / "spawn-media")))
assert bad_result[0]["issues"] == ["MEDIA_HASH_MISMATCH"], bad_result
print("PRIVATE NEGATIVE PASS: 7 recipe failures, private rule, media mismatch, replay and conflict")
PY

