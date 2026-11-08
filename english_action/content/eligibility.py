"""Bounded N2-06 authoring-time eligibility; not a publisher or learner grader.

The private caller supplies persisted Spawn state and independently authenticated
QA, source, rights, policy and dependency verifiers. JSON labels are not authority.
"""
from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable

from .spawn import _hash, prepare_text_review, validate_content

_FIELDS = ("context", "prompt", "key", "alternatives", "rationale")
_USE = "foundation_p0a_text"


def _fresh(record: dict, now: datetime) -> bool:
    try:
        if not isinstance(record, dict) or not record.get("verifiedAt"):
            return False
        observed = datetime.fromisoformat(record["verifiedAt"].replace("Z", "+00:00"))
        if observed.tzinfo is None or observed > now:
            return False
        if record.get("expiresAt") is not None:
            end = datetime.fromisoformat(record["expiresAt"].replace("Z", "+00:00"))
            if end.tzinfo is None or end <= now:
                return False
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _trusted(callback: Callable | None, **kwargs) -> bool:
    if not callable(callback):
        return False
    try:
        return callback(**kwargs) is True
    except Exception:
        return False


def assess_n2_cloze(state: dict, batch: dict, candidate_id: str, *,
                    qa_run: dict | None = None, claims: list[dict] | None = None,
                    rights: dict | None = None, policy: dict | None = None,
                    dependencies: list[dict] | None = None,
                    verify_qa: Callable | None = None,
                    verify_generation: Callable | None = None,
                    verify_closure: Callable | None = None,
                    verify_claim: Callable | None = None,
                    verify_rights: Callable | None = None,
                    verify_policy: Callable | None = None,
                    verify_dependency: Callable | None = None) -> dict:
    """Evaluate an exact persisted revision; missing external trust means HOLD.

    QA verifier authenticates a separate blind-before-keyed execution, not merely
    a JSON record claiming independence. ELIGIBLE is not publication permission.
    """
    reasons: list[str] = []
    binding = {"candidateId": candidate_id, "recipe": "N2-06", "useScope": _USE}
    try:
        handoff = prepare_text_review(state, batch, candidate_id)
        item = state["candidates"][candidate_id]
        packet = handoff["blindPacket"]
        if item["recipe"] != "N2-06" or packet["responseBranch"] != "type_in":
            raise ValueError("UNSUPPORTED_RECIPE_OR_BRANCH")
        binding.update(candidateRevision=item["revision"], contentHash=item["contentHash"],
                       sourceRef=item["sourceRef"], sourceRevision=item["sourceRevision"],
                       blindPacketDigest=_hash(packet))
    except (KeyError, TypeError, ValueError):
        return {"outcome": "HOLD", "reasons": ["SNAPSHOT_INVALID"], "binding": binding,
                "consumerSnapshot": None, "qaRunId": None, "evidenceIds": []}
    if validate_content("N2-06", item["content"]):
        return {"outcome": "REJECTED", "reasons": ["CONTENT_STRUCTURALLY_INVALID"],
                "binding": binding, "consumerSnapshot": None, "qaRunId": None, "evidenceIds": []}
    corrections = sum(1 for event in state.get("history", [])
                      if event.get("candidateId") == candidate_id and event.get("before") is not None
                      and event.get("after") != event.get("before"))
    if corrections > 2:
        reasons.append("CORRECTION_ROUND_LIMIT")
    if (item.get("provenance", {}).get("mode") != "real_ai"
            or not _trusted(verify_generation, candidate=item, binding=binding)):
        reasons.append("GENERATOR_PROVENANCE_UNVERIFIED")

    policy_version = policy.get("version") if isinstance(policy, dict) else None
    if (not isinstance(policy, dict) or not isinstance(policy_version, str)
            or not policy_version or policy.get("recipe") != "N2-06"
            or policy.get("useScope") != _USE or policy.get("objective") != packet["objective"]
            or policy.get("status") != "active"
            or not _trusted(verify_policy, policy=policy, binding=binding)):
        reasons.append("POLICY_UNVERIFIED")
    binding["policyVersion"] = policy_version

    qa_id = qa_run.get("runId") if isinstance(qa_run, dict) else None
    if not isinstance(qa_run, dict) or not all(isinstance(qa_run.get(k), str) and qa_run[k]
                                                  for k in ("runId", "actorId", "procedureVersion")):
        reasons.append("QA_RUN_MISSING")
    else:
        blind = qa_run.get("blind")
        comparison = qa_run.get("comparison")
        proposed = [item["content"]["key"], *item["content"]["alternatives"]]
        solutions = blind.get("answers") if isinstance(blind, dict) else None
        valid = (qa_run.get("candidateId") == candidate_id
                 and qa_run.get("candidateRevision") == item["revision"]
                 and qa_run.get("contentHash") == item["contentHash"]
                 and qa_run.get("blindPacketDigest") == _hash(packet)
                 and qa_run.get("sourceRevision") == item["sourceRevision"]
                 and qa_run.get("actorKind") == "ai" and qa_run.get("independence") == "separate_run"
                 and isinstance(solutions, list) and bool(solutions)
                 and all(isinstance(s, str) and s.strip() for s in solutions)
                 and isinstance(blind.get("reasoning"), str) and bool(blind["reasoning"].strip())
                 and blind.get("uncertain") is False
                 and isinstance(comparison, dict)
                 and comparison.get("status") == "supported"
                 and comparison.get("key") == item["content"]["key"]
                 and comparison.get("alternatives") == item["content"]["alternatives"]
                 and isinstance(comparison.get("reasoning"), str)
                 and bool(comparison["reasoning"].strip())
                 and {s.strip() for s in solutions} == {s.strip() for s in proposed}
                 and _trusted(verify_qa, qa_run=qa_run, blind_packet=packet,
                              keyed_content=item["content"]))
        if not valid:
            reasons.append("INDEPENDENT_QA_UNVERIFIED_OR_DISPUTED")
    binding["qaRunId"] = qa_id

    now = datetime.now(timezone.utc)
    expected = {"/content/" + field: _hash(item["content"][field]) for field in _FIELDS}
    expected.update({"/objective": _hash(packet["objective"]),
                     "/construct": _hash(packet["construct"]),
                     "/responseConditions": _hash([packet["responseBranch"], packet["responseConstraints"]])})
    covered = set()
    evidence_ids = []
    if isinstance(claims, list):
        for ev in claims:
            if not isinstance(ev, dict):
                continue
            path = ev.get("fieldPath")
            if (path not in expected or path in covered
                    or ev.get("valueDigest") != expected[path]
                    or ev.get("candidateRevision") != item["revision"]
                    or ev.get("contentHash") != item["contentHash"]
                    or ev.get("policyVersion") != policy_version
                    or not all(isinstance(ev.get(k), str) and ev[k] for k in
                               ("evidenceId", "sourceId", "sourceVersion", "locator", "claim", "limitations"))
                    or not _fresh(ev, now)
                    or not _trusted(verify_claim, evidence=ev, path=path,
                                    candidate=item, use_scope=_USE)):
                continue
            covered.add(path)
            evidence_ids.append(ev["evidenceId"])
    if covered != set(expected) or len(set(evidence_ids)) != len(evidence_ids):
        reasons.append("CLAIM_EVIDENCE_INCOMPLETE")

    if (not isinstance(rights, dict) or rights.get("status") != "redistributable"
            or rights.get("candidateId") != candidate_id
            or rights.get("candidateRevision") != item["revision"]
            or rights.get("contentHash") != item["contentHash"]
            or rights.get("sourceRevision") != item["sourceRevision"]
            or rights.get("policyVersion") != policy_version
            or rights.get("useScope") != _USE
            or not all(isinstance(rights.get(k), str) and rights[k] for k in
                       ("evidenceId", "origin", "rightsHolder", "rightsBasis", "territory"))
            or not _fresh(rights, now)
            or not _trusted(verify_rights, rights=rights, candidate=item, use_scope=_USE)):
        reasons.append("SOURCE_OR_OUTPUT_RIGHTS_UNVERIFIED")

    if not isinstance(dependencies, list):
        reasons.append("DEPENDENCIES_UNVERIFIED")
    elif (len({d.get("id") for d in dependencies if isinstance(d, dict)}) != len(dependencies)
          or any(not isinstance(d, dict) or not isinstance(d.get("id"), str)
                 or type(d.get("revision")) is not int or d["revision"] < 1
                 or not _trusted(verify_dependency, dependency=d, binding=binding)
                 for d in dependencies)):
        reasons.append("DEPENDENCIES_UNVERIFIED")
    if not _trusted(verify_closure, dependencies=dependencies, candidate=item, binding=binding):
        reasons.append("DEPENDENCY_CLOSURE_UNVERIFIED")
    # An empty dependency closure must still be externally authenticated.

    decision = "HOLD" if reasons else "ELIGIBLE"
    snapshot = None
    if decision == "ELIGIBLE":
        snapshot = {
            "contractVersion": "content-quality-02/n2-06/v1",
            "binding": deepcopy(binding), "qaRunId": qa_id,
            "evidenceIds": sorted(evidence_ids), "rightsEvidenceId": rights["evidenceId"],
            "dependencies": deepcopy(dependencies), "objective": packet["objective"],
            "construct": packet["construct"], "responseBranch": "type_in",
            "responseConstraints": deepcopy(packet["responseConstraints"]),
            "content": deepcopy(item["content"]),
        }
    return {"outcome": decision, "reasons": sorted(set(reasons)), "binding": binding,
            "qaRunId": qa_id, "evidenceIds": sorted(evidence_ids),
            "consumerSnapshot": snapshot}


def record_n2_decision(state: dict, decision_id: str, assessment: dict) -> tuple[dict, dict]:
    """Append-only, conflict-aware QA decision history; no promotion/publication."""
    if not isinstance(decision_id, str) or not decision_id or len(decision_id) > 128:
        raise ValueError("INVALID_DECISION_ID")
    if assessment.get("outcome") not in ("ELIGIBLE", "HOLD", "REJECTED"):
        raise ValueError("INVALID_DECISION")
    binding = assessment.get("binding")
    candidate = state.get("candidates", {}).get(binding.get("candidateId")) if isinstance(binding, dict) else None
    if (not isinstance(candidate, dict)
            or candidate.get("revision") != binding.get("candidateRevision")
            or candidate.get("contentHash") != binding.get("contentHash")
            or candidate.get("sourceRevision") != binding.get("sourceRevision")
            or candidate.get("status") != "HOLD"):
        return state, {"status": "conflict", "code": "DECISION_REVISION_STALE"}
    consumer = assessment.get("consumerSnapshot")
    if ((assessment["outcome"] == "ELIGIBLE"
         and (not isinstance(consumer, dict) or consumer.get("binding") != binding))
        or (assessment["outcome"] != "ELIGIBLE" and consumer is not None)):
        return state, {"status": "conflict", "code": "DECISION_CONSUMER_INVALID"}
    immutable = deepcopy(assessment)
    proof = _hash(immutable)
    previous = state.get("qualityDecisions", {}).get(decision_id)
    if previous:
        if previous["digest"] != proof:
            return state, {"status": "conflict", "code": "DECISION_ID_CONFLICT"}
        return state, {"status": "replayed", "decisionId": decision_id, "digest": proof}
    output = deepcopy(state)
    output.setdefault("qualityDecisions", {})[decision_id] = {"digest": proof, "assessment": immutable}
    output.setdefault("qualityDecisionHistory", []).append({"action": "content_quality_decision",
                                               "decisionId": decision_id, "digest": proof,
                                               "binding": deepcopy(assessment.get("binding"))})
    return output, {"status": "recorded", "decisionId": decision_id, "digest": proof}
