"""Bounded, draft-only Foundation P0-a AI producer. No editorial/publication authority."""
from __future__ import annotations

import argparse
import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from jsonschema import Draft202012Validator

from .cli import _atomic_save, _path_safe, _read_state, _state_lock
from .contracts import SchemaDirectory, digest
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
    "K1-17": ("passage", "question", "mainIdea", "answerKey", "audio"),
    "K2-21": ("passage", "question", "mainIdea", "alternatives", "answerKey"),
    "N2-10": ("context", "tokens", "acceptedOrderings", "constraints", "explanation"),
}
UNRESOLVED = ("answer_key_review", "independent_editorial_authority", "source_dependency_evidence", "usage_rights_clearance")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _text(x: Any) -> bool:
    return isinstance(x, str) and bool(x.strip()) and len(x) <= 10000


def _texts(xs: Any, *, minimum: int = 1) -> bool:
    return isinstance(xs, list) and len(xs) >= minimum and all(_text(x) for x in xs)


def _asset(asset: Any) -> bool:
    return (isinstance(asset, dict) and all(_text(asset.get(k)) for k in ("logicalId", "provider", "model", "mediaType", "storage"))
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
    errors = []
    if recipe == "N1-01":
        if not _text(payload["viCue"]) or not _texts(payload["enExpressions"]) or not _texts(payload["senseRefs"]):
            errors.append("FLASHCARD_CONTENT_INVALID")
        elif (len(set(payload["enExpressions"])) != len(payload["enExpressions"]) or
              not all(_UUID7_SENSE.fullmatch(s) for s in payload["senseRefs"])):
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
        if (not all(_text(payload[k]) for k in ("passage", "question", "mainIdea", "answerKey"))
                or not _asset(payload["audio"]) or payload["audio"]["mediaType"] != "audio"):
            errors.append("GIST_CONTENT_INVALID")
    elif recipe == "K2-21":
        if (not all(_text(payload[k]) for k in ("passage", "question", "mainIdea", "answerKey"))
                or not _texts(payload["alternatives"], minimum=2)
                or len(set(payload["alternatives"])) != len(payload["alternatives"])
                or payload["answerKey"] not in payload["alternatives"]):
            errors.append("READING_KEY_INVALID")
    elif recipe == "N2-10":
        tokens, orders = payload["tokens"], payload["acceptedOrderings"]
        if (not _text(payload["context"]) or not _text(payload["explanation"])
                or not _texts(tokens, minimum=2) or not isinstance(orders, list) or not orders
                or not all(isinstance(o, list) and sorted(o) == sorted(tokens) for o in orders)
                or len({_canonical(o) for o in orders}) != len(orders)
                or not isinstance(payload["constraints"], list)
                or not all(_text(c) for c in payload["constraints"])):
            errors.append("SENTENCE_ORDER_INVALID")
    return errors


def _media_issues(payload: dict, media_root: str | None) -> list[str]:
    if media_root is None:
        return []
    root = Path(media_root).resolve(strict=True)
    assets = payload.get("assets", [])
    if "audio" in payload:
        assets = [payload["audio"], *assets]
    errors = []
    for asset in assets:
        if not isinstance(asset, dict) or asset.get("storage") != "local-fixture":
            # No claim about remote Google Drive availability or rights.
            continue
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
            if sha256(resolved.read_bytes()).hexdigest() != asset["sha256"]:
                errors.append("MEDIA_HASH_MISMATCH")
        except (OSError, ValueError):
            errors.append("MEDIA_NOT_AVAILABLE")
    return errors


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
    source = json.loads(Path(quality_input).read_text(encoding="utf-8"))
    result = prepare_batch(source, blank_state(), SchemaDirectory(schemas), dry_run=True)[1]
    ok = result.get("status") == "dry-run" and not result.get("issues")
    return {"status": "PASS" if ok else "HOLD", "issues": [] if ok else ["PRIVATE_LEXICAL_QA_FAILED"],
            "publicationEligible": False}


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
        request = {"model": self.model, "temperature": 0,
                   "response_format": {"type": "json_object"},
                   "messages": [
                       {"role": "system", "content": "Generate exactly one unreviewed English learning content item as a JSON object. Return only the recipe-specific content object, never assert evidence, rights or editorial approval."},
                       {"role": "user", "content": _canonical({"recipe": seed["recipe"], "request": seed["request"], "requiredFields": REQUIRED[seed["recipe"]]})},
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
                    if not isinstance(output, dict):
                        return {"candidateId": "spn_" + key, "seedId": seed_id,
                                "status": "REJECTED", "issues": ["WORKER_OUTPUT_NOT_OBJECT"]}
                    item = {"candidateId": "spn_" + key, "batchId": batch["batchId"], "seedId": seed_id,
                            "recipe": seed["recipe"], "sourceRef": seed["sourceRef"],
                            "sourceRevision": seed["sourceRevision"], "variantKey": seed.get("variantKey", "default"),
                            "content": output, "provenance": {"provider": worker.provider, "model": worker.model,
                            "workerId": seed_id, "mode": "synthetic" if worker.provider == "synthetic" else "real_ai"},
                            "revision": 1, "status": "HOLD", "unresolvedEvidence": list(UNRESOLVED)}
                    errors = validate_content(seed["recipe"], output)
                    if not errors:
                        errors.extend(_media_issues(output, media_root))
                    if contract is not None:
                        errors.extend("PRIVATE_CONTRACT_INVALID" for _ in list(contract.iter_errors(item))[:1])
                    if errors:
                        return {"candidateId": item["candidateId"], "seedId": seed_id,
                                "status": "REJECTED", "issues": sorted(set(errors))}
                    item["contentHash"] = _hash(output)
                    item["expectedRevision"] = seed.get("expectedRevision", 0)
                    return item
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if attempt == retries:
                        message = (str(exc) if isinstance(exc, (ValueError, RuntimeError)) else "WORKER_FAILURE")
                        return {"candidateId": "spn_" + key, "seedId": seed_id,
                                "status": "REJECTED", "issues": [message[:80]]}
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
    for item in result:
        if item["status"] == "REJECTED":
            continue
        scope = (item["recipe"], item["sourceRef"], item["variantKey"])
        identical = (item["recipe"], item["contentHash"])
        if scope in seen_scope:
            item["status"], item["issues"] = "REJECTED", ["DUPLICATE_OR_CONFLICTING_SOURCE_VARIANT"]
        elif identical in seen_hash:
            item["status"], item["issues"] = "REJECTED", ["DUPLICATE_CONTENT"]
        else:
            seen_scope[scope], seen_hash[identical] = item, item
    return result


def persist(batch: dict, generated: list[dict], path: str, quality: dict) -> dict:
    """Single writer atomic CAS; append-only before/after history; replay immutable."""
    _check_batch(batch)
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
            return {**old["report"], "replayed": True}
        statuses = []
        for item in generated:
            if item["status"] == "REJECTED":
                statuses.append({"candidateId": item["candidateId"], "status": "REJECTED",
                                 "issues": item["issues"]})
                continue
            identifier = item["candidateId"]
            before = state["candidates"].get(identifier)
            expected = item.pop("expectedRevision", 0)
            if (before is None and expected != 0) or (before is not None and expected != before["revision"]):
                statuses.append({"candidateId": identifier, "status": "REJECTED", "issues": ["CAS_CONFLICT"]})
                continue
            if before and (before["contentHash"] == item["contentHash"] and
                           before["sourceRevision"] == item["sourceRevision"]):
                statuses.append({"candidateId": identifier, "status": "HOLD", "issues": ["UNCHANGED_REVISION"]})
                continue
            next_item = {**item, "revision": 1 if before is None else before["revision"] + 1}
            state["candidates"][identifier] = next_item
            state["history"].append({"candidateId": identifier, "batchId": batch["batchId"],
                                     "before": before, "after": next_item})
            statuses.append({"candidateId": identifier, "status": "HOLD",
                             "issues": list(UNRESOLVED)})
        report = {"batchId": batch["batchId"], "status": "DRAFT_HOLD",
                  "generationMode": generated[0].get("provenance", {}).get("mode", "synthetic") if generated else "unknown",
                  "generated": len(generated), "drafts": sum(s["status"] == "HOLD" for s in statuses),
                  "rejected": sum(s["status"] == "REJECTED" for s in statuses),
                  "quality": quality, "candidates": statuses, "publicationEligible": False}
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
            print(_canonical({**old["report"], "replayed": True}))
            return 0
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
