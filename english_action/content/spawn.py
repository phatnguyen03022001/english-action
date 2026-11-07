"""Bounded, draft-only Foundation P0-a AI producer. No editorial/publication authority."""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import math
import wave
import xml.etree.ElementTree as ET
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from jsonschema import Draft202012Validator
from PIL import Image, UnidentifiedImageError

from .cli import _atomic_save, _path_safe, _read_state, _state_lock
from .contracts import SchemaDirectory
from .ledger import blank_state, prepare_batch

RECIPES = ("N1-01", "N2-06", "N3-11", "K1-16", "K1-17", "K2-21", "N2-10")
_UUID7_SENSE = re.compile(r"sns_[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\Z")
_KEY = re.compile(r"[a-zA-Z0-9_.:-]{1,120}\Z")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
REQUIRED = {
    "N1-01": ("viCue", "enExpressions", "senseRefs"),
    "N2-06": ("context", "prompt", "key", "alternatives", "rationale"),
    "N3-11": ("phonemes", "labels", "assets", "key"),
    "K1-16": ("transcript", "acceptedForms", "audio"),
    "K1-17": ("passage", "question", "mainIdea", "alternatives", "answerKey", "rationale", "audio"),
    "K2-21": ("passage", "question", "mainIdea", "alternatives", "answerKey"),
    "N2-10": ("context", "tokens", "acceptedOrderings", "constraints", "explanation"),
}
ASSET_FIELDS = frozenset(("logicalId", "provider", "model", "mediaType", "storage",
                          "revision", "sha256", "rightsStatus", "accessibility", "locator"))
UNRESOLVED = ("answer_key_review", "independent_editorial_authority", "source_dependency_evidence", "usage_rights_clearance")
_MAX_MEDIA_BYTES = 32 * 1024 * 1024
_MAX_RASTER_SIDE = 10000
_MAX_RASTER_PIXELS = 25_000_000


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(x: Any) -> bool:
    return isinstance(x, str) and bool(x.strip()) and len(x) <= 10000


def _texts(xs: Any, *, minimum: int = 1) -> bool:
    return isinstance(xs, list) and len(xs) >= minimum and all(_text(x) for x in xs)


def _asset(asset: Any) -> bool:
    return (isinstance(asset, dict) and asset.keys() <= ASSET_FIELDS and all(_text(asset.get(k)) for k in ("logicalId", "provider", "model", "mediaType", "storage"))
            and asset["mediaType"] in ("audio", "image")
            and type(asset.get("revision")) is int and asset["revision"] > 0
            and isinstance(asset.get("sha256"), str) and bool(_HEX.fullmatch(asset["sha256"]))
            and asset.get("rightsStatus") in ("unknown", "reference_only", "permission_required", "redistributable")
            and _text(asset.get("accessibility")))


def validate_content(recipe: str, payload: Any) -> list[str]:
    """Transferable structural checks; semantic/editorial validation remains private."""
    if recipe not in REQUIRED:
        return ["RECIPE_NOT_APPROVED"]
    if not isinstance(payload, dict):
        return ["CONTENT_NOT_OBJECT"]
    if any(field not in payload for field in REQUIRED[recipe]):
        return ["CONTENT_REQUIRED_FIELD_MISSING"]
    # K2-21 v1 drafts may omit rationale; reviewed feedback must gate it.
    allowed = set(REQUIRED[recipe]) | ({"rationale"} if recipe == "K2-21" else set())
    if payload.keys() - allowed:
        return ["CONTENT_FIELD_NOT_ALLOWED"]
    errors = []
    if recipe == "N1-01":
        if not _text(payload["viCue"]) or not _texts(payload["enExpressions"]) or not _texts(payload["senseRefs"]):
            errors.append("FLASHCARD_CONTENT_INVALID")
        elif (len(set(payload["enExpressions"])) != len(payload["enExpressions"]) or
              not all(_UUID7_SENSE.fullmatch(s) for s in payload["senseRefs"]) or
              len(set(payload["senseRefs"])) != len(payload["senseRefs"])):
            errors.append("FLASHCARD_SENSE_OR_DUPLICATE_INVALID")
    elif recipe == "N2-06":
        if (not all(_text(payload[k]) for k in ("context", "prompt", "key", "rationale"))
                or not isinstance(payload["alternatives"], list)
                or not all(_text(v) for v in payload["alternatives"])
                or payload["key"] in payload["alternatives"]
                or len(set(payload["alternatives"])) != len(payload["alternatives"])):
            errors.append("CLOZE_KEY_INVALID")
    elif recipe == "N3-11":
        if (not _texts(payload["phonemes"], minimum=2) or len(payload["phonemes"]) != 2
                or len(set(payload["phonemes"])) != 2 or not _texts(payload["labels"], minimum=2)
                or len(payload["labels"]) != 2 or len(set(payload["labels"])) != 2
                or payload["key"] not in payload["labels"]
                or not isinstance(payload["assets"], list) or len(payload["assets"]) != 2
                or not all(_asset(a) and a["mediaType"] == "audio" for a in payload["assets"])
                or len({a["logicalId"] for a in payload["assets"] if isinstance(a, dict) and "logicalId" in a}) != 2):
            errors.append("MINIMAL_PAIR_INVALID")
    elif recipe == "K1-16":
        if (not _text(payload["transcript"]) or not _texts(payload["acceptedForms"])
                or payload["transcript"] not in payload["acceptedForms"]
                or len(set(payload["acceptedForms"])) != len(payload["acceptedForms"])
                or not _asset(payload["audio"]) or payload["audio"]["mediaType"] != "audio"):
            errors.append("DICTATION_ALIGNMENT_NOT_VERIFIED")
    elif recipe == "K1-17":
        if (not all(_text(payload[k]) for k in ("passage", "question", "mainIdea", "answerKey", "rationale"))
                or not _texts(payload["alternatives"], minimum=2)
                or len(set(payload["alternatives"])) != len(payload["alternatives"])
                or payload["answerKey"] not in payload["alternatives"]
                or not _asset(payload["audio"]) or payload["audio"]["mediaType"] != "audio"):
            errors.append("GIST_CONTENT_INVALID")
    elif recipe == "K2-21":
        if (not all(_text(payload[k]) for k in ("passage", "question", "mainIdea", "answerKey"))
                or not _texts(payload["alternatives"], minimum=2)
                or len(set(payload["alternatives"])) != len(payload["alternatives"])
                or payload["answerKey"] not in payload["alternatives"]):
            errors.append("READING_KEY_INVALID")
        if "rationale" in payload and not _text(payload["rationale"]):
            errors.append("READING_RATIONALE_INVALID")
    elif recipe == "N2-10":
        tokens, orders = payload["tokens"], payload["acceptedOrderings"]
        if (not _text(payload["context"]) or not _text(payload["explanation"])
                or not _texts(tokens, minimum=2) or not isinstance(orders, list) or not orders
                or not all(isinstance(o, list) and _texts(o) and sorted(o) == sorted(tokens) for o in orders)
                or len({_canonical(o) for o in orders}) != len(orders)
                or not isinstance(payload["constraints"], list)
                or not all(_text(c) for c in payload["constraints"])):
            errors.append("SENTENCE_ORDER_INVALID")
    return errors


def _assets(recipe: str, payload: dict) -> list[dict]:
    if recipe == "N3-11":
        return payload["assets"]
    if recipe in ("K1-16", "K1-17"):
        return [payload["audio"]]
    return []


def _isolate_rights(recipe: str, payload: dict) -> tuple[dict, dict]:
    content = deepcopy(payload)
    claims = []
    for asset in _assets(recipe, content):
        claims.append({"logicalId": asset["logicalId"], "revision": asset["revision"],
                       "claimedStatus": asset["rightsStatus"]})
        asset["rightsStatus"] = "unknown"
    return content, {"generatorClaims": claims, "authenticatedEvidence": [],
                     "clearance": "UNVERIFIED"}


def _media_issues(assets: list[dict], root: Path) -> list[str]:
    errors = []
    for asset in assets:
        locator = asset.get("locator")
        if not isinstance(locator, str) or not locator or Path(locator).is_absolute():
            errors.append("MEDIA_LOCATOR_INVALID")
            continue
        path = root / locator
        if any(p == ".." for p in Path(locator).parts) or path.is_symlink():
            errors.append("MEDIA_PATH_UNSAFE")
            continue
        try:
            resolved = path.resolve(strict=True)
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise ValueError("unsafe")
            if resolved.suffix.lower() != ".wav" and resolved.stat().st_size > _MAX_MEDIA_BYTES:
                errors.append("MEDIA_RESOURCE_LIMIT")
                continue
            data = resolved.read_bytes()
            if sha256(data).hexdigest() != asset["sha256"]:
                errors.append("MEDIA_HASH_MISMATCH")
            else:
                errors.extend(_media_bytes_issues(data, resolved.suffix.lower(), asset["mediaType"]))
        except (OSError, ValueError):
            errors.append("MEDIA_NOT_AVAILABLE")
    return errors


def _media_bytes_issues(data: bytes, suffix: str, media_type: str) -> list[str]:
    """Validate supported local media bytes, not meaning, rights or publication."""
    formats = {".wav": ("WAVE", "audio"), ".svg": ("SVG", "image"),
               ".png": ("PNG", "image"), ".webp": ("WEBP", "image")}
    detected = (("PNG" if data.startswith(b"\x89PNG\r\n\x1a\n") else None)
                or ("WAVE" if data[:4] == b"RIFF" and data[8:12] == b"WAVE" else None)
                or ("WEBP" if data[:4] == b"RIFF" and data[8:12] == b"WEBP" else None)
                or ("SVG" if data.lstrip().startswith(b"<svg") else None))
    expected = formats.get(suffix)
    if expected is None:
        return ["MEDIA_TYPE_MISMATCH" if detected else "MEDIA_ENCODING_UNSUPPORTED"]
    encoding, declared_type = expected
    if media_type != declared_type or (detected and detected != encoding):
        return ["MEDIA_TYPE_MISMATCH"]
    if suffix == ".wav":
        try:
            if (len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE"
                    or int.from_bytes(data[4:8], "little") + 8 != len(data)):
                return ["AUDIO_BYTES_INVALID"]
            with wave.open(BytesIO(data), "rb") as audio:
                frames = audio.readframes(audio.getnframes())
                expected = audio.getnframes() * audio.getnchannels() * audio.getsampwidth()
                if not frames or len(frames) != expected:
                    return ["AUDIO_BYTES_INVALID"]
                silence = 128 if audio.getsampwidth() == 1 else 0
                if all(value == silence for value in frames):
                    return ["AUDIO_SILENT"]
        except (wave.Error, EOFError, ValueError):
            return ["AUDIO_BYTES_INVALID"]
    elif suffix in (".png", ".webp"):
        # WebP and WAV both use RIFF: check form type and container length.
        if suffix == ".webp" and (len(data) < 12 or data[:4] != b"RIFF"
                                  or data[8:12] != b"WEBP"
                                  or int.from_bytes(data[4:8], "little") + 8 != len(data)):
            return ["IMAGE_BYTES_INVALID"]
        try:
            with Image.open(BytesIO(data)) as image:
                if image.format != encoding:
                    return ["MEDIA_TYPE_MISMATCH"]
                width, height = image.size
                if (not 0 < width <= _MAX_RASTER_SIDE
                        or not 0 < height <= _MAX_RASTER_SIDE
                        or width * height > _MAX_RASTER_PIXELS):
                    return ["IMAGE_DIMENSIONS_INVALID"]
                if getattr(image, "n_frames", 1) != 1:
                    return ["IMAGE_ANIMATED_UNSUPPORTED"]
                image.verify()  # Container/chunks; does not decode pixels.
            with Image.open(BytesIO(data)) as image:
                image.load()    # Full pixel decode; rejects truncated/corrupt payloads.
        except (UnidentifiedImageError, OSError, ValueError, SyntaxError, EOFError,
                Image.DecompressionBombError):
            return ["IMAGE_BYTES_INVALID"]
    elif suffix == ".svg":
        # Narrow static graphics profile. No browser, network or code.
        tags = {"svg", "g", "title", "desc", "rect", "line", "text",
                "path", "polyline", "polygon", "circle", "ellipse"}
        attributes = {"id", "role", "aria-labelledby", "viewBox", "width", "height",
                      "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry",
                      "d", "points", "fill", "stroke", "stroke-width", "font-size",
                      "text-anchor"}
        try:
            if b"<!" in data or b"<?" in data:
                return ["SVG_UNSAFE"]
            tree = ET.fromstring(data)
            if tree.tag != "{http://www.w3.org/2000/svg}svg":
                return ["IMAGE_BYTES_INVALID"]
            for node in tree.iter():
                if (not node.tag.startswith("{http://www.w3.org/2000/svg}")
                        or node.tag.split("}", 1)[-1] not in tags
                        or set(node.attrib) - attributes
                        or any("url" in value.lower() or "://" in value
                               for value in node.attrib.values())):
                    return ["SVG_UNSAFE"]
            width, height = float(tree.attrib["width"]), float(tree.attrib["height"])
            if not all(math.isfinite(v) and 0 < v <= 10000 for v in (width, height)):
                return ["IMAGE_DIMENSIONS_INVALID"]
        except (ET.ParseError, KeyError, ValueError, UnicodeError):
            return ["IMAGE_BYTES_INVALID"]
    return []


def media_binding(assets: list[dict], source: dict, configuration: dict) -> str:
    """Fingerprint exact source/key/order/construct and generation dependencies.

    source is an evidence projection, not a second candidate or asset registry.
    Pass exact content minus embedded assets; retain immutable source/content
    revisions, objective/construct and resolved dependency references.
    Locator/storage/rights labels do not change media bytes. Accessibility does
    require revalidation. Configuration records actual voice/language/render
    settings, with null for facts that are not available.
    """
    if (not isinstance(assets, list) or not assets or not all(_asset(a) for a in assets)
            or not isinstance(source, dict) or not isinstance(configuration, dict)
            or not all(k in source for k in ("sourceRef", "sourceRevision", "contentRevision",
                                            "objective", "construct", "content", "dependencies"))
            or not _text(source["sourceRef"])
            or any(type(source[k]) is not int or source[k] < 1
                   for k in ("sourceRevision", "contentRevision"))
            or not _text(source["objective"]) or not _text(source["construct"])
            or not isinstance(source["content"], dict) or not source["content"]
            or not _texts(source["dependencies"], minimum=0)):
        raise ValueError("MEDIA_BINDING_INPUT_INVALID")
    identities = [{k: v for k, v in asset.items()
                   if k not in {"locator", "storage", "rightsStatus"}} for asset in assets]
    return _hash({"source": source, "assets": identities, "configuration": configuration})


def verify_media(assets: list[dict], source: dict, configuration: dict,
                 media_root: str, *, reviewed_binding: str | None = None,
                 findings: tuple[str, ...] = (), rights_status: str = "unknown",
                 withdrawn: tuple[str, ...] = ()) -> dict:
    """Read-only denial/revalidation aid; never authenticates reviews or grants use.

    Findings are reported observations, not independently authenticated verdicts.
    A matching binding means only the dependency snapshot matches.
    Existing external rights/editorial/publication gates remain mandatory.
    withdrawn contains resolved source/asset dependency IDs, including transitive
    IDs in source.dependencies; this function does not discover remote references.
    """
    checks = {"integrity": "NOT_VERIFIED", "dependencyBinding": "NOT_VERIFIED",
              "actualContent": "NOT_VERIFIED", "rightsClearance": "NOT_VERIFIED",
              "editorialAuthorization": "NOT_VERIFIED"}
    issues = []
    current = None
    try:
        current = media_binding(assets, source, configuration)
    except (ValueError, TypeError):
        issues.append("MEDIA_BINDING_INPUT_INVALID")
    if current:
        if reviewed_binding is not None:
            checks["dependencyBinding"] = "PASS" if current == reviewed_binding else "FAIL"
            if current != reviewed_binding:
                issues.append("MEDIA_BINDING_STALE")
        try:
            root = Path(media_root).resolve(strict=True)
            local = [a for a in assets if a["storage"] == "local-fixture"]
            errors = _media_issues(local, root)
            issues.extend(errors)
            checks["integrity"] = ("FAIL" if errors else
                                   "PASS" if len(local) == len(assets) else "NOT_VERIFIED")
        except (OSError, ValueError, TypeError):
            issues.append("MEDIA_NOT_AVAILABLE")
            checks["integrity"] = "FAIL"
        dependencies = {source["sourceRef"], *source["dependencies"],
                        *(a["logicalId"] for a in assets)}
        if dependencies.intersection(withdrawn):
            issues.append("MEDIA_DEPENDENCY_WITHDRAWN")
    if findings:
        checks["actualContent"] = "FAIL"
        issues.append("MEDIA_REPORTED_CONTENT_DEFECT")
    if rights_status in {"revoked", "expired", "insufficient", "reference_only",
                         "permission_required"}:
        checks["rightsClearance"] = "FAIL"
        issues.append("MEDIA_RIGHTS_DENIED")
    return {"status": "HOLD", "publicationEligible": False, "newUseAllowed": False,
            "binding": current, "checks": checks, "issues": sorted(set(issues)),
            "reportedFindings": list(findings),
            "assetAction": ("REUSE_BYTES_PENDING_GATES"
                            if checks["dependencyBinding"] == "PASS"
                            and checks["integrity"] == "PASS" and not issues
                            else "REVALIDATE_AFFECTED_DEPENDENCIES")}


def load_contract(path: str) -> Draft202012Validator:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("$ref", "$dynamicRef") and (not isinstance(value, str) or not value.startswith("#/")):
                    raise ValueError("External JSON Schema references are disabled")
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(data)
    Draft202012Validator.check_schema(data)
    return Draft202012Validator(data)


def check_quality(schemas: str | None, quality_input: str | None) -> dict:
    if not schemas or not quality_input:
        return {"status": "NOT_VERIFIED", "issues": ["PRIVATE_LEXICAL_QA_NOT_RUN"]}
    try:
        source = json.loads(Path(quality_input).read_text(encoding="utf-8"))
        result = prepare_batch(source, blank_state(), SchemaDirectory(schemas), dry_run=True)[1]
    except (OSError, ValueError, UnicodeError):
        return {"status": "HOLD", "issues": ["PRIVATE_LEXICAL_QA_INPUT_INVALID"],
                "publicationEligible": False}
    ok = result.get("status") == "dry-run" and not result.get("issues")
    return {"status": "PASS" if ok else "HOLD", "issues": [] if ok else ["PRIVATE_LEXICAL_QA_FAILED"],
            "publicationEligible": False}


def _candidate_quality(item: dict, checks: dict, issues: list[str]) -> dict:
    statuses = checks.values()
    status = "FAIL" if "FAIL" in statuses else (
        "NOT_VERIFIED" if "NOT_VERIFIED" in statuses else "PASS")
    return {"candidateId": item["candidateId"], "revision": item["revision"],
            "contentHash": item.get("contentHash"), "status": status,
            "checks": checks, "issues": sorted(set(issues))}



def prepare_text_review(state: dict, batch: dict, candidate_id: str) -> dict:
    """Project the existing text pilot from a persisted snapshot, never approve it.

    Give only blindPacket to the independent solver. mechanicalEvidence is for
    the coordinator: unchanged field digests do not transfer semantic review,
    rights, appointment or publication authority. Use the batch that created
    the current snapshot, including after an unchanged CAS submission.
    """
    _check_batch(batch)
    item = state.get("candidates", {}).get(candidate_id)
    recorded = state.get("runs", {}).get(batch["batchId"], {})
    seed = next((s for s in batch["seeds"]
                 if item and s["seedId"] == item.get("seedId")), None)
    history = [h for h in state.get("history", [])
               if h.get("candidateId") == candidate_id]
    fields = {"N2-06": ("context", "prompt"),
              "K2-21": ("passage", "question", "alternatives"),
              "N2-10": ("context", "tokens", "constraints")}
    if (not item or not seed or recorded.get("inputHash") != _hash(batch)
            or item.get("batchId") != batch["batchId"] or item.get("status") != "HOLD"
            or item.get("recipe") not in fields
            or item.get("recipe") != seed["recipe"]
            or item.get("sourceRef") != seed["sourceRef"]
            or item.get("sourceRevision") != seed["sourceRevision"]
            or item.get("variantKey") != seed.get("variantKey", "default")
            or candidate_id != "spn_" + _hash([seed["recipe"], seed["sourceRef"],
                                              seed.get("variantKey", "default"), seed["seedId"]])[:24]
            or type(item.get("revision")) is not int or item["revision"] < 1
            or item.get("contentHash") != _hash(item.get("content"))
            or validate_content(item["recipe"], item.get("content"))
            or not history or history[-1].get("after") != item):
        raise ValueError("TEXT_REVIEW_SNAPSHOT_MISMATCH")
    request = seed["request"]
    if (not all(_text(request.get(k)) for k in ("objective", "construct", "responseBranch"))
            or not _texts(request.get("constraints"))):
        raise ValueError("TEXT_REVIEW_BRIEF_INCOMPLETE")
    before = history[-1].get("before")
    claims = []
    old = before["content"] if before else {}
    for field in sorted(item["content"].keys() | old.keys()):
        present = field in item["content"]
        value = item["content"].get(field)
        claims.append({"fieldPath": "/content/" + field, "present": present,
                       "valueDigest": _hash(value) if present else None,
                       "previousValueDigest": _hash(old[field]) if field in old else None,
                       "changed": (not present or field not in old or old[field] != value)
                                  if before else None})
    quality = item.get("generatedContentQuality") or {}
    bound = all(quality.get(k) == item[k] for k in ("candidateId", "revision", "contentHash"))
    if not bound:
        quality = _candidate_quality(item, {"generation": "NOT_VERIFIED"},
                                     ["GENERATED_QUALITY_BINDING_STALE"])
    packet = {
        "candidateId": candidate_id, "seedId": item["seedId"], "recipe": item["recipe"],
        "sourceRef": item["sourceRef"], "sourceRevision": item["sourceRevision"],
        "candidateRevision": item["revision"], "contentHash": item["contentHash"],
        "objective": request["objective"], "construct": request["construct"],
        "responseBranch": request["responseBranch"],
        "responseConstraints": request["constraints"],
        "stimulus": {key: item["content"][key] for key in fields[item["recipe"]]},
    }
    evidence = {
        "candidateId": candidate_id, "revision": item["revision"], "contentHash": item["contentHash"],
        "claims": claims,
        "changedFields": [c["fieldPath"] for c in claims if c["changed"] is True],
        "unchangedFields": [c["fieldPath"] for c in claims if c["changed"] is False],
        "generatedContentQuality": quality,
        "qualityBinding": "PASS" if bound else "NOT_VERIFIED",
        "unresolvedEvidence": list(UNRESOLVED),
        "issues": sorted(set(quality.get("issues", []))),
        "status": "HOLD", "publicationEligible": False,
    }
    return deepcopy({"blindPacket": packet, "mechanicalEvidence": evidence})


def _check_generated(item: dict, contract: Draft202012Validator | None,
                     media_root: str | None) -> tuple[dict[str, str], list[str]]:
    recipe, content = item["recipe"], item["content"]
    issues = validate_content(recipe, content)
    checks = {"recipeContent": "FAIL" if issues else "PASS",
              "producerContract": "NOT_VERIFIED", "mediaMetadata": "NOT_APPLICABLE",
              "localMediaIntegrity": "NOT_APPLICABLE", "sourceReferenceResolution": "NOT_VERIFIED",
              "editorialCorrectness": "NOT_VERIFIED"}
    if contract is not None:
        valid = contract.is_valid(item)
        checks["producerContract"] = "PASS" if valid else "FAIL"
        if not valid:
            issues.append("PRIVATE_CONTRACT_INVALID")
    if recipe in ("N3-11", "K1-16", "K1-17"):
        if checks["recipeContent"] == "FAIL":
            checks.update(mediaMetadata="NOT_VERIFIED", localMediaIntegrity="NOT_VERIFIED")
        else:
            assets = _assets(recipe, content)
            checks["mediaMetadata"] = "PASS"
            local = [asset for asset in assets if asset["storage"] == "local-fixture"]
            if local and media_root is not None:
                try:
                    root = Path(media_root).resolve(strict=True)
                    media_issues = _media_issues(local, root)
                except (OSError, ValueError):
                    media_issues = ["MEDIA_NOT_AVAILABLE"]
                issues.extend(media_issues)
                checks["localMediaIntegrity"] = "FAIL" if media_issues else "PASS"
            else:
                checks["localMediaIntegrity"] = "NOT_VERIFIED"
            if len(local) != len(assets):
                checks["remoteMediaIntegrity"] = "NOT_VERIFIED"
    return checks, issues


def _reject(item: dict, issues: list[str], checks: dict | None = None) -> dict:
    return {"candidateId": item["candidateId"], "seedId": item["seedId"],
            "status": "REJECTED", "issues": sorted(set(issues)),
            "generatedContentQuality": _candidate_quality(item, checks or {"generation": "FAIL"}, issues)}


class Worker(Protocol):
    provider: str
    model: str
    async def generate(self, seed: dict) -> dict: ...


class SyntheticWorker:
    """Explicit fixture replay. Never represents real AI generation."""
    provider = "synthetic"
    model = "fixture-v1"

    async def generate(self, seed: dict) -> dict:
        if "fixture" not in seed:
            raise ValueError("SYNTHETIC_FIXTURE_MISSING")
        return seed["fixture"]


class OpenAIWorker:
    """Opt-in real provider adapter. Requires explicit authorization and secret."""
    provider = "openai"

    def __init__(self, model: str, api_key: str):
        if not model or not api_key:
            raise ValueError("Model and OPENAI_API_KEY required")
        self.model, self._key = model, api_key

    async def generate(self, seed: dict) -> dict:
        request = {"model": self.model, "max_completion_tokens": 800,
                   "response_format": {"type": "json_object"},
                   "messages": [
                       {"role": "system", "content": "Generate exactly one unreviewed English learning content item as a JSON object. Return only the recipe-specific content object, never assert evidence, rights or editorial approval."},
                       {"role": "user", "content": _canonical({"recipe": seed["recipe"], "request": seed["request"], "requiredFields": (*REQUIRED[seed["recipe"]], "rationale") if seed["recipe"] == "K2-21" else REQUIRED[seed["recipe"]]})},
                   ]}
        def call() -> dict:
            body = _canonical(request).encode()
            http = Request("https://api.openai.com/v1/chat/completions", data=body, method="POST",
                           headers={"Authorization": "Bearer " + self._key, "Content-Type": "application/json"})
            try:
                with urlopen(http, timeout=30) as res:
                    raw = res.read(262145)
                    if len(raw) > 262144:
                        raise ValueError("PROVIDER_RESPONSE_TOO_LARGE")
            except HTTPError as exc:
                raise RuntimeError("PROVIDER_HTTP_" + str(exc.code)) from None
            except URLError:
                raise RuntimeError("PROVIDER_NETWORK_ERROR") from None
            obj = json.loads(raw)
            return json.loads(obj["choices"][0]["message"]["content"])
        return await asyncio.to_thread(call)


def _seed_ok(seed: Any) -> bool:
    return (isinstance(seed, dict) and all(_text(seed.get(k)) for k in ("seedId", "sourceRef", "recipe"))
            and _KEY.fullmatch(seed["seedId"]) is not None and seed["recipe"] in RECIPES
            and type(seed.get("sourceRevision")) is int and seed["sourceRevision"] > 0
            and isinstance(seed.get("request"), dict)
            and _text(seed.get("variantKey", "default"))
            and type(seed.get("expectedRevision", 0)) is int and seed.get("expectedRevision", 0) >= 0)


def _check_batch(batch: Any) -> None:
    if (not isinstance(batch, dict) or not isinstance(batch.get("batchId"), str)
            or not _KEY.fullmatch(batch["batchId"])
            or not isinstance(batch.get("seeds"), list)
            or not 1 <= len(batch["seeds"]) <= 100):
        raise ValueError("Invalid batch identifier or number of seeds (1..100)")
    if not all(_seed_ok(s) for s in batch["seeds"]):
        raise ValueError("Invalid or unapproved recipe seed")
    if len({s["seedId"] for s in batch["seeds"]}) != len(batch["seeds"]):
        raise ValueError("Duplicate seed identifiers")


async def generate(batch: dict, worker: Worker, *, concurrency: int = 3,
                   timeout: float = 30, retries: int = 1,
                   contract: Draft202012Validator | None = None,
                   media_root: str | None = None) -> list[dict]:
    _check_batch(batch)
    if not 1 <= concurrency <= 16 or not 0 < timeout <= 120 or not 0 <= retries <= 3:
        raise ValueError("Unbounded concurrency, timeout or retry configuration")
    semaphore = asyncio.Semaphore(concurrency)

    async def one(seed: dict) -> dict:
        seed_id = seed["seedId"]
        key = _hash([seed["recipe"], seed["sourceRef"], seed.get("variantKey", "default"), seed_id])[:24]
        async with semaphore:
            for attempt in range(retries + 1):
                try:
                    output = await asyncio.wait_for(worker.generate(seed), timeout)
                    revision = seed.get("expectedRevision", 0) + 1
                    identity = {"candidateId": "spn_" + key, "seedId": seed_id, "revision": revision}
                    if not isinstance(output, dict):
                        return _reject(identity, ["WORKER_OUTPUT_NOT_OBJECT"])
                    item = {**identity, "batchId": batch["batchId"],
                            "recipe": seed["recipe"], "sourceRef": seed["sourceRef"],
                            "sourceRevision": seed["sourceRevision"], "variantKey": seed.get("variantKey", "default"),
                            "content": output, "provenance": {"provider": worker.provider, "model": worker.model,
                            "workerId": seed_id, "mode": "synthetic" if worker.provider == "synthetic" else "real_ai"},
                            "status": "HOLD", "unresolvedEvidence": list(UNRESOLVED)}
                    # The v1 producer envelope describes the initial draft. The CAS
                    # revision belongs to the ledger and its quality evidence.
                    envelope = {**item, "revision": 1}
                    checks, errors = _check_generated(envelope, contract, media_root)
                    if errors:
                        return _reject({**identity, "contentHash": _hash(output)}, errors, checks)
                    content, rights = _isolate_rights(seed["recipe"], output)
                    if content != output and contract is not None and not contract.is_valid({**envelope, "content": content}):
                        checks["producerContract"] = "FAIL"
                        return _reject({**identity, "contentHash": _hash(output)}, ["PRIVATE_CONTRACT_INVALID"], checks)
                    item.update(content=content, contentHash=_hash(content), rights=rights,
                                expectedRevision=revision - 1)
                    item["generatedContentQuality"] = _candidate_quality(item, checks, errors)
                    return item
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if attempt == retries:
                        message = (str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "WORKER_FAILURE")
                        return _reject({"candidateId": "spn_" + key, "seedId": seed_id,
                                        "revision": seed.get("expectedRevision", 0) + 1}, [message[:80]])
                    await asyncio.sleep(0.01 * (attempt + 1))
        raise AssertionError("Unreachable")

    tasks = [asyncio.create_task(one(s)) for s in batch["seeds"]]
    try:
        result = await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    seen_scope: dict[tuple, dict] = {}
    seen_hash: dict[tuple, dict] = {}
    for index, item in enumerate(result):
        if item["status"] == "REJECTED":
            continue
        scope = (item["recipe"], item["sourceRef"], item["variantKey"])
        identical = (item["recipe"], item["contentHash"])
        if scope in seen_scope:
            checks = {**item["generatedContentQuality"]["checks"], "batchUniqueness": "FAIL"}
            result[index] = _reject(item, ["DUPLICATE_OR_CONFLICTING_SOURCE_VARIANT"], checks)
        elif identical in seen_hash:
            checks = {**item["generatedContentQuality"]["checks"], "batchUniqueness": "FAIL"}
            result[index] = _reject(item, ["DUPLICATE_CONTENT"], checks)
        else:
            seen_scope[scope], seen_hash[identical] = item, item
            item["generatedContentQuality"]["checks"]["batchUniqueness"] = "PASS"
    return result


def _batch_quality(candidates: list[dict]) -> dict:
    results = [item["generatedContentQuality"] for item in candidates]
    statuses = {result["status"] for result in results}
    status = "FAIL" if "FAIL" in statuses else "NOT_VERIFIED" if "NOT_VERIFIED" in statuses else "PASS"
    return {"status": status, "candidates": results}


def _replay_report(report: dict) -> dict:
    return {**report, "replayed": True,
            "lexicalSourceQuality": report.get("lexicalSourceQuality", report["quality"]),
            "generatedContentQuality": report.get("generatedContentQuality", {
                "status": "NOT_VERIFIED", "issues": ["LEGACY_REPORT_WITHOUT_GENERATED_CHECKS"],
                "candidates": []})}


def persist(batch: dict, generated: list[dict], path: str, quality: dict) -> dict:
    """Single writer atomic CAS; append-only before/after history; replay immutable."""
    _check_batch(batch)
    if len(generated) != len(batch["seeds"]):
        raise ValueError("Incomplete generated batch")
    for item, seed in zip(generated, batch["seeds"]):
        expected_id = "spn_" + _hash([seed["recipe"], seed["sourceRef"],
                                       seed.get("variantKey", "default"), seed["seedId"]])[:24]
        if (not isinstance(item, dict) or item.get("seedId") != seed["seedId"]
                or item.get("candidateId") != expected_id
                or item.get("status") not in ("REJECTED", "HOLD")):
            raise ValueError("Unsafe generated candidate")
        if item["status"] == "HOLD" and (
                item.get("recipe") != seed["recipe"] or item.get("sourceRef") != seed["sourceRef"]
                or item.get("sourceRevision") != seed["sourceRevision"]
                or item.get("contentHash") != _hash(item.get("content"))
                or validate_content(seed["recipe"], item.get("content"))
                or item.get("unresolvedEvidence") != list(UNRESOLVED)):
            raise ValueError("Generated draft integrity invalid")
    state_path = _path_safe(path)
    input_hash = _hash(batch)
    with _state_lock(state_path):
        state = _read_state(state_path) or {"formatVersion": 1, "runs": {}, "candidates": {}, "history": []}
        if (not isinstance(state, dict) or state.get("formatVersion") != 1
                or not all(isinstance(state.get(k), dict) for k in ("runs", "candidates"))
                or not isinstance(state.get("history"), list)):
            raise ValueError("Invalid existing draft state")
        old = state["runs"].get(batch["batchId"])
        if old:
            if old["inputHash"] != input_hash:
                raise ValueError("BATCH_REPLAY_CONFLICT")
            return _replay_report(old["report"])
        statuses = []
        for item in generated:
            identifier = item["candidateId"]
            evidence = item.get("generatedContentQuality") or _candidate_quality(
                {**item, "revision": item.get("revision", 1)}, {"generation": "NOT_VERIFIED"}, [])
            entry = {"candidateId": identifier, "generatedContentQuality": evidence}
            if item["status"] == "REJECTED":
                statuses.append({**entry, "status": "REJECTED", "issues": item["issues"]})
                continue
            before = state["candidates"].get(identifier)
            expected = item.get("expectedRevision", 0)
            if (before is None and expected != 0) or (before is not None and expected != before["revision"]):
                statuses.append({**entry, "status": "REJECTED", "issues": ["CAS_CONFLICT"]})
                continue
            content, rights = _isolate_rights(item["recipe"], item["content"])
            if content == item["content"] and "rights" in item:
                rights["generatorClaims"] = item["rights"].get("generatorClaims", [])
            if before and (before["content"] == content and before.get("rights") == rights and
                           before["sourceRevision"] == item["sourceRevision"]):
                evidence = {**evidence, "revision": before["revision"]}
                statuses.append({**entry, "generatedContentQuality": evidence, "status": "HOLD",
                                 "revision": before["revision"], "issues": ["UNCHANGED_REVISION"]})
                continue
            revision = expected + 1
            next_item = {**item, "revision": revision, "content": content, "contentHash": _hash(content),
                         "rights": rights}
            if (evidence["candidateId"] != identifier or evidence["revision"] != revision
                    or evidence["contentHash"] != next_item["contentHash"]):
                evidence = _candidate_quality(next_item, {"generation": "NOT_VERIFIED"}, [])
            # Exact repeats and reused sources can understate effective bank
            # diversity. Report candidates for review, never infer semantic
            # equivalence, learner exposure or publication from these signals.
            possible_reuse = {
                "exactContentCandidateIds": sorted(
                    cid for cid, previous in state["candidates"].items()
                    if cid != identifier and previous.get("recipe") == next_item["recipe"]
                    and previous.get("contentHash") == next_item["contentHash"]),
                "sharedSourceCandidateIds": sorted(
                    cid for cid, previous in state["candidates"].items()
                    if cid != identifier and previous.get("recipe") == next_item["recipe"]
                    and previous.get("sourceRef") == next_item["sourceRef"]),
            }
            next_item["generatedContentQuality"] = evidence
            next_item.pop("expectedRevision", None)
            state["candidates"][identifier] = next_item
            state["history"].append({"candidateId": identifier, "batchId": batch["batchId"],
                                     "before": before, "after": next_item})
            statuses.append({**entry, "generatedContentQuality": evidence, "status": "HOLD",
                             "revision": revision, "issues": list(UNRESOLVED),
                             "possibleReuse": possible_reuse})
        report = {"batchId": batch["batchId"], "status": "DRAFT_HOLD",
                  "generationMode": generated[0].get("provenance", {}).get("mode", "synthetic") if generated else "unknown",
                  "generated": len(generated), "drafts": sum(s["status"] == "HOLD" for s in statuses),
                  "rejected": sum(s["status"] == "REJECTED" for s in statuses),
                  "quality": quality, "lexicalSourceQuality": quality,
                  "generatedContentQuality": _batch_quality(statuses),
                  "candidates": statuses, "publicationEligible": False}
        state["runs"][batch["batchId"]] = {"inputHash": input_hash, "report": report}
        _atomic_save(state_path, state)
        return report


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="english-spawn")
    parser.add_argument("--input", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--contract", required=True, help="Private producer JSON Schema")
    parser.add_argument("--schemas", help="Private lexical schema root")
    parser.add_argument("--quality-input", help="Private lexical batch for Python Quality Engine validation")
    parser.add_argument("--media-root", help="Private local synthetic media fixture root")
    parser.add_argument("--provider", choices=("synthetic", "openai"), default="synthetic")
    parser.add_argument("--model")
    parser.add_argument("--allow-real-ai", action="store_true")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--retries", type=int, default=1)
    opts = parser.parse_args(argv)
    try:
        if bool(opts.schemas) != bool(opts.quality_input):
            raise ValueError("Both --schemas and --quality-input are required for lexical validation")
        if opts.provider == "openai":
            if not opts.allow_real_ai:
                raise ValueError("Real AI generation requires --allow-real-ai")
            worker: Worker = OpenAIWorker(opts.model or "", os.environ.get("OPENAI_API_KEY", ""))
        else:
            worker = SyntheticWorker()
        batch = json.loads(Path(opts.input).read_text(encoding="utf-8"))
        _check_batch(batch)
        state_path = _path_safe(opts.state)
        existing = _read_state(state_path)
        if existing and batch["batchId"] in existing.get("runs", {}):
            old = existing["runs"][batch["batchId"]]
            if old["inputHash"] != _hash(batch):
                raise ValueError("BATCH_REPLAY_CONFLICT")
            report = _replay_report(old["report"])
            print(_canonical(report))
            return 0 if report["rejected"] == 0 else 1
        validator = load_contract(opts.contract)
        quality = check_quality(opts.schemas, opts.quality_input)
        result = asyncio.run(generate(batch, worker, concurrency=opts.concurrency,
                                      timeout=opts.timeout, retries=opts.retries, contract=validator,
                                      media_root=opts.media_root))
        report = persist(batch, result, opts.state, quality)
        print(_canonical(report))
        return 0 if report["rejected"] == 0 else 1
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(_canonical({"status": "ERROR", "reason": str(exc)[:160]}), file=sys.stderr)
        return 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
