"""V2 semantic integrity: reuse the V1 registry graph and add V2-specific checks.

The private caller supplies the V1 registry and V2 schemas. No private source,
data, reviewer material or credentials are bundled with this implementation.
"""
from __future__ import annotations

from .contracts import digest, issue, lifecycle_v2_issues, sort_issues, validate_v1_graph, value_at, MISSING


def validate_v2_graph(records: list[dict], v1_vocabulary: dict) -> list[dict]:
    """Return full, deterministic V2 storage diagnostics as the Node source contract."""
    # Node V2 invokes its public V1 semantic validator over the same V2 records,
    # then adds the V2-only references, exact claim binding and lifecycle audit.
    base = validate_v1_graph(records, v1_vocabulary)
    errors = list(base["issues"])
    by_id = {record["_id"]: record for record in records}

    def reference(record: dict, path: str, identifier: str, prefix: str) -> None:
        target = by_id.get(identifier)
        if target is None:
            errors.append(issue("REFERENCE_NOT_FOUND", record["_id"], path, identifier))
        elif not identifier.startswith(prefix + "_"):
            errors.append(issue("REFERENCE_TYPE_MISMATCH", record["_id"], path, identifier))
        elif record.get("lifecycleStatus") == "active" and target.get("lifecycleStatus") == "retired":
            errors.append(issue("RETIRED_LIVE_REFERENCE", record["_id"], path, identifier))

    for record in records:
        ident = record["_id"]
        for index, construction_id in enumerate(record.get("constructionIds", ())):
            reference(record, f"constructionIds/{index}", construction_id, "con")
            target = by_id.get(construction_id)
            if target and target.get("senseId") != ident:
                errors.append(issue("CONSTRUCTION_SENSE_MISMATCH", ident,
                                    f"constructionIds/{index}", "Construction belongs to a different sense"))
        claim = record.get("claim")
        if isinstance(claim, dict):
            subject_id = claim.get("subjectId")
            if isinstance(subject_id, str):
                reference(record, "claim/subjectId", subject_id, subject_id.split("_", 1)[0])
            target = by_id.get(subject_id)
            if (target is not None and
                    (subject_id not in record.get("assertionIds", ()) or
                     value_at(target, claim.get("fieldPath")) is MISSING or
                     digest(value_at(target, claim.get("fieldPath"))) != claim.get("valueDigest"))):
                errors.append(issue("EVIDENCE_CLAIM_MISMATCH", ident, "claim",
                                    "Claim digest, path or assertion ID does not match"))
        for index, reviewed in enumerate(record.get("claims", ())):
            subject_id = reviewed.get("subjectId")
            if isinstance(subject_id, str):
                reference(record, f"claims/{index}/subjectId",
                          subject_id, subject_id.split("_", 1)[0])
            target = by_id.get(subject_id)
            if (subject_id not in record.get("subjectIds", ()) or target is None
                    or value_at(target, reviewed.get("fieldPath")) is MISSING):
                errors.append(issue("EDITORIAL_CLAIM_MISMATCH", ident, f"claims/{index}",
                                    "Review does not apply to exact subject/path"))
        for index, evidence_id in enumerate(record.get("pronunciationEvidenceIds", ())):
            reference(record, f"pronunciationEvidenceIds/{index}", evidence_id, "evd")
        for i, example in enumerate(record.get("examples", ())):
            for j, evidence_id in enumerate(example.get("evidenceIds", ())):
                reference(record, f"examples/{i}/evidenceIds/{j}", evidence_id, "evd")

    for record in records:
        ident = record["_id"]
        if not ident.startswith("dec_") or "decisionEffect" not in record:
            continue
        is_transition = record.get("action") in ("split", "merge", "duplicate_reconciliation", "invalidate")
        if ((is_transition and record["decisionEffect"] != "lifecycle_transition")
                or (record.get("action") == "retain" and record["decisionEffect"] != "non_transition")):
            errors.append(issue("DECISION_EFFECT_MISMATCH", ident, "decisionEffect",
                                "Decision effect contradicts the stated action"))
        if record["decisionEffect"] == "lifecycle_transition" and not record.get("transitionAudit"):
            errors.append(issue("LIFECYCLE_AUDIT_INCOMPLETE", ident, "transitionAudit",
                                "Transition requires evidence and incoming-edge inventory"))

    errors.extend(lifecycle_v2_issues(records))
    return sort_issues(errors)
