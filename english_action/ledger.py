"""Persistent candidate state transitions; pure functions, no external authority inferred."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import re
import secrets
import time
import unicodedata

from .contracts import (PREFIXES, MISSING, SchemaDirectory, digest, evaluate_publication,
                        validate_dataset, value_at)

_KEY = re.compile(r"[A-Za-z0-9._:-]{1,120}\Z")
_SOURCE_KEY = re.compile(r"[A-Za-z0-9._:/-]{1,240}\Z")
_FORBIDDEN = set("_id schemaVersion language lemma pos entryKind multiwordKind homographNumber entryId sourceId targetSenseId sourceSenseId lifecycleStatus retirementMode retiredByDecisionId supersededByIds editorialStatus createdAt evidenceIds revision".split())


def blank_state() -> dict:
    return {"formatVersion": 1, "batches": {}, "mappings": {}, "sourceMappings": {}, "records": [], "history": []}


def state_valid(state: dict) -> bool:
    return (isinstance(state, dict) and type(state.get("formatVersion")) is int and state["formatVersion"] == 1
            and isinstance(state.get("records"), list) and isinstance(state.get("history"), list)
            and all(isinstance(state.get(key), dict) for key in ("batches", "mappings", "sourceMappings"))
            and ("evidenceBindings" not in state or isinstance(state["evidenceBindings"], dict)))


def _result(state: dict, status: str, code: str | None = None, message: str = "", **extra) -> tuple[dict, dict]:
    details = extra.pop("details", [])
    return state, {"status": status, "issues": ([{"code": code, "message": message}] if code else []) + details, "publicationEligible": False, **extra}


def _prefix(item: dict) -> str | None:
    if item.get("type"):
        return item["type"]
    e = item.get("entity") or {}
    if all(k in e for k in ("language", "lemma", "pos")):
        return "lex"
    for keys, p in ((("entryId", "definition"), "sns"), (("sourceSenseId", "alignmentMode"), "eqv"),
                    (("senseId", "pattern"), "con"), (("anchorSenseId", "pattern"), "col"),
                    (("relationType", "sourceId"), "rel"), (("sourceId", "assertionIds"), "evd"),
                    (("title",), "src"), (("reviewerId", "subjectIds", "action"), "dec"),
                    (("reviewerId", "subjectIds"), "rev"), (("entryId", "form"), "frm")):
        if all(key in e for key in keys):
            return p
    return None


def _resolve(value, mappings: dict[str, str]):
    if isinstance(value, str) and value.startswith("candidate:"):
        if value[10:] not in mappings:
            raise ValueError("UNRESOLVED_CANDIDATE_REFERENCE: " + value)
        return mappings[value[10:]]
    if isinstance(value, list):
        return [_resolve(x, mappings) for x in value]
    if isinstance(value, dict):
        return {k: _resolve(v, mappings) for k, v in value.items()}
    return value


def uuidv7() -> str:
    """Compatible lowercase prefixed UUIDv7 layout (time + cryptographic entropy)."""
    t = f"{int(time.time() * 1000):012x}"[-12:]
    h = secrets.token_hex(10)
    variant = format((int(h[3], 16) & 3) | 8, "x")
    return f"{t[:8]}-{t[8:]}-7{h[:3]}-{variant}{h[4:7]}-{h[7:19]}"


def _preview(batch_digest: str, candidate_id: str) -> str:
    h = sha256((batch_digest + "#" + candidate_id).encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-7{h[12:15]}-8{h[15:18]}-{h[18:30]}"


def _projection(value):
    return {k: v for k, v in value.items() if k not in {"_id", "schemaVersion", "revision", "createdAt", "updatedAt"}}


def prepare_batch(batch: dict, state: dict, schemas: SchemaDirectory, *, dry_run: bool = False) -> tuple[dict, dict]:
    if not isinstance(batch, dict):
        return _result(state, "conflict", "BATCH_INVALID", "Invalid batch")
    batch = deepcopy(batch)
    for item in batch.get("records", ()):
        if isinstance(item, dict) and isinstance(item.get("entity"), dict):
            for k in ("lemma", "form"):
                if isinstance(item["entity"].get(k), str):
                    item["entity"][k] = unicodedata.normalize("NFC", item["entity"][k])
    try:
        hash_value = digest(batch)
    except ValueError:
        return _result(state, "conflict", "BATCH_INVALID", "Unrepresentable JSON value")
    records = batch.get("records")
    if not isinstance(batch.get("batchId"), str) or not _KEY.fullmatch(batch["batchId"]) or not isinstance(records, list) or not 1 <= len(records) <= 10000:
        return _result(state, "conflict", "BATCH_INVALID", "Invalid batch ID or record count (1..10000)")
    if "supersedesBatchId" in batch and (not isinstance(batch["supersedesBatchId"], str) or not _KEY.fullmatch(batch["supersedesBatchId"])):
        return _result(state, "conflict", "SUPERSEDES_BATCH_INVALID", "Invalid superseded batch ID")
    seen_ids, source_keys, types = set(), set(), {}
    for item in records:
        if not isinstance(item, dict) or not isinstance(item.get("candidateId"), str) or not _KEY.fullmatch(item["candidateId"]) or not isinstance(item.get("entity"), dict):
            return _result(state, "conflict", "CANDIDATE_INVALID", "Candidate ID or payload invalid")
        cid = item["candidateId"]
        if cid in seen_ids:
            return _result(state, "conflict", "DUPLICATE_CANDIDATE_ID", cid)
        seen_ids.add(cid)
        if "sourceItemKey" in item:
            key = item["sourceItemKey"]
            if not isinstance(key, str) or not _SOURCE_KEY.fullmatch(key):
                return _result(state, "conflict", "SOURCE_KEY_INVALID", cid)
            if key in source_keys:
                return _result(state, "conflict", "DUPLICATE_SOURCE_ITEM_KEY", key)
            source_keys.add(key)
        p = _prefix(item)
        if p not in PREFIXES:
            return _result(state, "conflict", "ENTITY_TYPE_UNKNOWN", cid)
        if "_id" in item["entity"] or "schemaVersion" in item["entity"]:
            return _result(state, "conflict", "CANONICAL_ID_IN_CANDIDATE", "Provide canonical IDs only via persistent mapping")
        if item["entity"].get("editorialStatus") == "verified":
            return _result(state, "conflict", "SELF_VERIFICATION_FORBIDDEN", cid)
        types[cid] = p
    if not state:
        state = {}
    if state and not state_valid(state):
        return _result(state, "conflict", "STATE_VERSION_INVALID", "Unrecognized persistent state format")
    prior = state.get("batches", {}).get(batch["batchId"])
    if prior:
        if prior.get("digest") == hash_value:
            return _result(state, "replayed", digest=hash_value, batchId=batch["batchId"], duplicates=[], missingEvidence=[], reviewQueue=[])
        return _result(state, "conflict", "CHANGED_BATCH_CONFLICT", "Batch ID already processed with another digest")
    next_state = deepcopy(blank_state() | state)
    by_id = {r["_id"]: r for r in next_state["records"]}
    candidates = {item["candidateId"]: item["entity"] for item in records}
    for item in records:
        if types[item["candidateId"]] != "evd" or not isinstance(item["entity"].get("claim"), dict):
            continue
        claim, ev = item["entity"]["claim"], item["entity"]
        ref = claim.get("subjectId")
        if ref not in ev.get("assertionIds", []):
            return _result(state, "conflict", "EVIDENCE_SUBJECT_MISMATCH", "Subject absent from assertionIds")
        subject = candidates.get(ref[10:]) if isinstance(ref, str) and ref.startswith("candidate:") else by_id.get(ref)
        if not subject:
            return _result(state, "conflict", "EVIDENCE_SUBJECT_NOT_FOUND", str(ref))
        value = value_at(subject, claim.get("fieldPath"))
        if value is MISSING:
            return _result(state, "conflict", "EVIDENCE_PATH_MISMATCH", str(claim.get("fieldPath")))
        if digest(value) != claim.get("valueDigest"):
            return _result(state, "conflict", "EVIDENCE_SOURCE_DIGEST_MISMATCH", item["candidateId"])
    mappings, reused, occupied = {}, set(), set(by_id)
    for item in records:
        cid = item["candidateId"]
        mapping_key = batch["batchId"] + "#" + cid
        if mapping_key in next_state["mappings"]:
            return _result(state, "conflict", "MAPPING_COLLISION", mapping_key)
        known = next_state["sourceMappings"].get(item.get("sourceItemKey"))
        previous = next_state["mappings"].get(batch.get("supersedesBatchId", "") + "#" + cid)
        if known:
            if previous and previous["canonicalId"] != known["canonicalId"]:
                return _result(state, "conflict", "IDENTITY_MAPPING_CONFLICT", cid)
            if known["contentDigest"] != digest(item["entity"]) or not known["canonicalId"].startswith(types[cid] + "_"):
                return _result(state, "conflict", "SOURCE_ITEM_CHANGED", cid)
            selected = known["canonicalId"]
        elif previous:
            selected = previous["canonicalId"]
            if not selected.startswith(types[cid] + "_"):
                return _result(state, "conflict", "SUPERSEDED_MAPPING_INVALID", cid)
        else:
            selected = types[cid] + "_" + (_preview(hash_value, cid) if dry_run else uuidv7())
            if selected in occupied:
                return _result(state, "conflict", "ID_COLLISION", selected)
        if selected in occupied and not (known or previous):
            return _result(state, "conflict", "ID_COLLISION", selected)
        if known or previous:
            if selected not in by_id:
                return _result(state, "conflict", "SOURCE_MAPPING_DANGLING", cid)
            reused.add(cid)
        occupied.add(selected)
        mappings[cid] = selected
    if len(set(mappings.values())) != len(mappings):
        return _result(state, "conflict", "CANONICAL_ID_REUSED_IN_BATCH", "Two candidates share an identity")
    try:
        materialized = []
        for item in records:
            entity = _resolve(item["entity"], mappings)
            if entity.get("relationType") in ("SYNONYM_OF", "NEAR_SYNONYM_OF", "ANTONYM_OF") and entity.get("sourceId", "") > entity.get("targetId", ""):
                entity["sourceId"], entity["targetId"] = entity["targetId"], entity["sourceId"]
            entity = {**entity, "_id": mappings[item["candidateId"]], "schemaVersion": "2.0.0"}
            materialized.append(entity)
    except (ValueError, TypeError) as exc:
        return _result(state, "conflict", "UNRESOLVED_REFERENCE", str(exc))
    for item, entity in zip(records, materialized):
        if item["candidateId"] in reused and digest(_projection(entity)) != digest(_projection(by_id[entity["_id"]])):
            return _result(state, "conflict", "BATCH_REVISION_REQUIRED", item["candidateId"])
    newly_created = [entity for item, entity in zip(records, materialized) if item["candidateId"] not in reused]
    full = next_state["records"] + newly_created
    validation = validate_dataset(full, schemas, publication_issue_limit=0)
    if not validation["valid"]:
        return _result(state, "conflict", "VALIDATION_FAILED", "Candidate graph/schema rejected", **{"details": validation["issues"]})
    missing_evidence = [x["_id"] for x in materialized if x["_id"].split("_", 1)[0] not in ("src", "evd", "rev", "dec") and not x.get("evidenceIds")]
    review_queue = sorted(x["_id"] for x in materialized if x.get("editorialStatus") != "rejected")
    if dry_run:
        return _result(state, "dry-run", digest=hash_value, batchId=batch["batchId"], duplicates=[], missingEvidence=["pending_identity"] * len(missing_evidence), reviewQueue=[x["candidateId"] for x in records])
    for item in records:
        cid = item["candidateId"]
        next_state["mappings"][batch["batchId"] + "#" + cid] = {"canonicalId": mappings[cid], "batchId": batch["batchId"], "contentDigest": digest(item["entity"])}
        if item.get("sourceItemKey") and item["sourceItemKey"] not in next_state["sourceMappings"]:
            next_state["sourceMappings"][item["sourceItemKey"]] = {"canonicalId": mappings[cid], "contentDigest": digest(item["entity"]), "firstBatchId": batch["batchId"]}
    next_state["batches"][batch["batchId"]] = {"digest": hash_value, "recordIds": [mappings[x["candidateId"]] for x in records], "contractVersion": "2.0.0"}
    if batch.get("supersedesBatchId"):
        next_state["batches"][batch["batchId"]]["supersedesBatchId"] = batch["supersedesBatchId"]
    next_state["records"].extend(newly_created)
    next_state["history"].append({"action": "batch_prepared", "batchId": batch["batchId"], "digest": hash_value, "recordIds": [mappings[x["candidateId"]] for x in records], "createdRecordIds": [x["_id"] for x in newly_created]})
    return _result(next_state, "prepared", digest=hash_value, batchId=batch["batchId"], duplicates=[], missingEvidence=sorted(missing_evidence), reviewQueue=review_queue, recordIds=[mappings[x["candidateId"]] for x in records], reusedCount=len(reused))


def revise_candidates(state: dict, request: dict, schemas: SchemaDirectory, *, dry_run: bool = False) -> tuple[dict, dict]:
    if not state_valid(state):
        return _result(state, "conflict", "STATE_INVALID", "Prepared ledger required")
    if not isinstance(request, dict) or not isinstance(request.get("revisionId"), str) or not _KEY.fullmatch(request["revisionId"]) or not isinstance(request.get("changes"), list) or not 1 <= len(request["changes"]) <= 1000:
        return _result(state, "conflict", "REVISION_INVALID", "Revision ID and bounded changes required")
    hash_value = digest(request)
    prior = state.get("revisions", {}).get(request["revisionId"])
    if prior:
        return _result(state, "replayed", revisionId=request["revisionId"]) if prior["digest"] == hash_value else _result(state, "conflict", "REVISION_ID_CONFLICT", "Revision ID reused")
    next_state = deepcopy(state)
    by_id = {r["_id"]: (i, r) for i, r in enumerate(state["records"])}
    before, after, seen = [], [], set()
    for change in request["changes"]:
        if not isinstance(change, dict) or not isinstance(change.get("canonicalId"), str) or type(change.get("expectedRevision")) is not int or change["expectedRevision"] < 1 or not isinstance(change.get("set"), dict):
            return _result(state, "conflict", "REVISION_CHANGE_INVALID", "Invalid revision change")
        identifier = change["canonicalId"]
        if identifier in seen:
            return _result(state, "conflict", "REVISION_DUPLICATE_TARGET", identifier)
        seen.add(identifier)
        if identifier not in by_id:
            return _result(state, "conflict", "REVISION_TARGET_MISSING", identifier)
        i, old = by_id[identifier]
        if old.get("lifecycleStatus") != "active" or old.get("editorialStatus") != "draft":
            return _result(state, "conflict", "REVISION_STATUS_BLOCKED", identifier)
        if old.get("revision", 1) != change["expectedRevision"]:
            return _result(state, "conflict", "REVISION_CAS_CONFLICT", identifier)
        patch = change["set"]
        if not patch or any(k in _FORBIDDEN or k.startswith("__") or k in ("constructor", "prototype") for k in patch):
            return _result(state, "conflict", "REVISION_FIELD_FORBIDDEN", identifier)
        updated = deepcopy(old) | deepcopy(patch) | {"revision": old.get("revision", 1) + 1}
        next_state["records"][i] = updated
        before.append(deepcopy(old)); after.append(deepcopy(updated))
    validated = validate_dataset(next_state["records"], schemas)
    if not validated["valid"]:
        return _result(state, "conflict", "REVISION_INVALID_DATASET", "Revision violates schema/graph")
    if dry_run:
        return _result(state, "dry-run", revisionId=request["revisionId"])
    next_state.setdefault("revisions", {})[request["revisionId"]] = {"digest": hash_value, "recordIds": [x["_id"] for x in after]}
    next_state["history"].append({"action": "draft_revision", "revisionId": request["revisionId"], "digest": hash_value, "before": before, "after": after})
    return _result(next_state, "revised", revisionId=request["revisionId"], recordIds=[x["_id"] for x in after])


def bind_evidence(state: dict, request: dict, schemas: SchemaDirectory, *, dry_run: bool = False) -> tuple[dict, dict]:
    if not state_valid(state):
        return _result(state, "conflict", "STATE_INVALID", "Prepared ledger required")
    if not isinstance(request, dict) or not isinstance(request.get("bindingId"), str) or not _KEY.fullmatch(request["bindingId"]) or not isinstance(request.get("subjectId"), str) or type(request.get("expectedRevision")) is not int or request["expectedRevision"] < 1 or not isinstance(request.get("evidenceIds"), list) or not 1 <= len(request["evidenceIds"]) <= 1000:
        return _result(state, "conflict", "EVIDENCE_BINDING_INVALID", "Invalid binding request")
    hash_value = digest(request)
    prior = state.get("evidenceBindings", {}).get(request["bindingId"])
    if prior:
        return _result(state, "replayed", bindingId=request["bindingId"]) if prior["digest"] == hash_value else _result(state, "conflict", "EVIDENCE_BINDING_ID_CONFLICT", "Binding ID reused")
    if len(set(request["evidenceIds"])) != len(request["evidenceIds"]):
        return _result(state, "conflict", "EVIDENCE_BINDING_DUPLICATE", "Duplicate evidence ID")
    by_id = {r["_id"]: r for r in state["records"]}
    subject = by_id.get(request["subjectId"])
    if not subject or subject["_id"].split("_", 1)[0] in ("src", "evd", "rev", "dec"):
        return _result(state, "conflict", "EVIDENCE_BINDING_SUBJECT_INVALID", "Invalid publishable subject")
    if subject.get("lifecycleStatus") != "active" or subject.get("editorialStatus") != "draft":
        return _result(state, "conflict", "EVIDENCE_BINDING_STATUS_BLOCKED", "Only active draft may bind")
    if subject.get("revision", 1) != request["expectedRevision"]:
        return _result(state, "conflict", "EVIDENCE_BINDING_CAS_CONFLICT", "Stale revision")
    evs = []
    for identifier in request["evidenceIds"]:
        ev = by_id.get(identifier)
        if not ev or not identifier.startswith("evd_"):
            return _result(state, "conflict", "EVIDENCE_BINDING_TARGET_INVALID", str(identifier))
        claim = ev.get("claim") or {}
        if ev.get("lifecycleStatus") != "active" or ev.get("editorialStatus") != "draft" or claim.get("subjectId") != subject["_id"] or subject["_id"] not in ev.get("assertionIds", ()):
            return _result(state, "conflict", "EVIDENCE_BINDING_SUBJECT_MISMATCH", identifier)
        value = value_at(subject, claim.get("fieldPath"))
        if value is MISSING or digest(value) != claim.get("valueDigest"):
            return _result(state, "conflict", "EVIDENCE_BINDING_CLAIM_MISMATCH", identifier)
        if identifier in subject.get("evidenceIds", ()):
            return _result(state, "conflict", "EVIDENCE_BINDING_ALREADY_PRESENT", identifier)
        evs.append(ev)
    next_state = deepcopy(state)
    updated = deepcopy(subject) | {"evidenceIds": [*subject.get("evidenceIds", []), *request["evidenceIds"]], "revision": subject.get("revision", 1) + 1}
    next_state["records"][next(i for i, x in enumerate(next_state["records"]) if x["_id"] == subject["_id"])] = updated
    if not validate_dataset(next_state["records"], schemas)["valid"]:
        return _result(state, "conflict", "EVIDENCE_BINDING_INVALID_DATASET", "Result violates schema/graph")
    if dry_run:
        return _result(state, "dry-run", bindingId=request["bindingId"])
    next_state.setdefault("evidenceBindings", {})[request["bindingId"]] = {"digest": hash_value, "subjectId": subject["_id"], "evidenceIds": request["evidenceIds"]}
    next_state["history"].append({"action": "evidence_binding", "bindingId": request["bindingId"], "digest": hash_value, "before": [deepcopy(subject)], "after": [deepcopy(updated)], "evidence": [{"evidenceId": e["_id"], "subjectId": e["claim"]["subjectId"], "fieldPath": e["claim"]["fieldPath"], "valueDigest": e["claim"]["valueDigest"]} for e in evs]})
    return _result(next_state, "bound", bindingId=request["bindingId"], subjectId=subject["_id"], evidenceIds=request["evidenceIds"])


def promote_candidates(state: dict, request: dict, schemas: SchemaDirectory, *, dry_run: bool = False, trust: dict | None = None) -> tuple[dict, dict]:
    if not state_valid(state):
        return _result(state, "conflict", "STATE_INVALID", "Prepared ledger required")
    if not isinstance(request, dict) or not isinstance(request.get("promotionId"), str) or not _KEY.fullmatch(request["promotionId"]) or not isinstance(request.get("subjects"), list) or not 1 <= len(request["subjects"]) <= 1000:
        return _result(state, "conflict", "PROMOTION_INVALID", "Invalid promotion request")
    hash_value = digest(request)
    prior = state.get("promotions", {}).get(request["promotionId"])
    if prior:
        return _result(state, "replayed", promotionId=request["promotionId"]) if prior["digest"] == hash_value else _result(state, "conflict", "PROMOTION_ID_CONFLICT", "Promotion ID reused")
    next_state = deepcopy(state)
    by_id = {r["_id"]: (i, r) for i, r in enumerate(state["records"])}
    seen, before, after = set(), [], []
    for selected in request["subjects"]:
        if not isinstance(selected, dict) or not isinstance(selected.get("canonicalId"), str) or selected["canonicalId"] in seen or type(selected.get("expectedRevision")) is not int or selected["expectedRevision"] < 1:
            return _result(state, "conflict", "PROMOTION_SUBJECT_INVALID", "Invalid/duplicate subject")
        identifier = selected["canonicalId"]
        seen.add(identifier)
        if identifier not in by_id or identifier.split("_", 1)[0] in ("src", "evd", "rev", "dec"):
            return _result(state, "conflict", "PROMOTION_TARGET_INVALID", identifier)
        i, old = by_id[identifier]
        if old.get("revision", 1) != selected["expectedRevision"]:
            return _result(state, "conflict", "PROMOTION_CAS_CONFLICT", identifier)
        if old.get("lifecycleStatus") != "active" or old.get("editorialStatus") != "draft":
            return _result(state, "blocked", "PROMOTION_STATUS_BLOCKED", identifier)
        new = deepcopy(old) | {"editorialStatus": "verified", "revision": old.get("revision", 1) + 1}
        next_state["records"][i] = new
        before.append(deepcopy(old)); after.append(deepcopy(new))
    validated = validate_dataset(next_state["records"], schemas)
    if not validated["valid"]:
        return _result(state, "blocked", "PROMOTION_INVALID_DATASET", "Candidate invalid")
    assessment = evaluate_publication(next_state["records"], (trust or {}) | {"subject_ids": list(seen)})
    if not assessment["eligible"]:
        return _result(state, "blocked", "PUBLICATION_GATE_FAILED", "Independent proof missing", details=assessment["issues"])
    if dry_run:
        return _result(state, "dry-run", publicationEligible=True, promotionId=request["promotionId"], subjectIds=list(seen))
    next_state.setdefault("promotions", {})[request["promotionId"]] = {"digest": hash_value, "subjectIds": list(seen)}
    next_state["history"].append({"action": "promotion", "promotionId": request["promotionId"], "digest": hash_value, "before": before, "after": after})
    return _result(next_state, "promoted", publicationEligible=True, promotionId=request["promotionId"], subjectIds=list(seen))
