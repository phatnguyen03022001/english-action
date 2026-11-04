"""Pure version migration, golden QA and explicit authenticated review gates.

This module contains public-safe deterministic logic. It never authenticates itself,
publishes content or accesses private production material.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Callable


QUALITY_DIMENSIONS = (
    "sense_boundary_accuracy", "translation_fidelity", "grammar_accuracy",
    "idiomaticity", "collocation_validity", "learner_usefulness",
    "evidence_sufficiency", "license_compliance",
)


def migrate_v1_records(input_value: dict | list[dict]) -> dict:
    records = input_value if isinstance(input_value, list) else (
        input_value.get("records") if isinstance(input_value, dict) else None)
    if not isinstance(records, list):
        raise TypeError("V1 migration requires records")
    seen = set()
    for record in records:
        if not isinstance(record, dict) or record.get("schemaVersion") != "1.0.0" or not isinstance(record.get("_id"), str):
            raise ValueError("MIGRATION_INVALID_V1_RECORD")
        if record["_id"] in seen:
            raise ValueError("MIGRATION_DUPLICATE_ID: " + record["_id"])
        seen.add(record["_id"])
    return {"records": [{**deepcopy(record), "schemaVersion": "2.0.0"} for record in records],
            "migration": {"from": "1.0.0", "to": "2.0.0",
                          "preservedIdentities": True, "reviewStatus": "not_assessed",
                          "rightsStatus": "not_assessed"}}


def assess_golden_qa(benchmark: dict, submissions: list[dict] | None = None, *,
                     verify_independent_review: Callable | None = None,
                     verify_rights: Callable | None = None) -> dict:
    """Compare eight independent QA dimensions; no self-certified approval.

    Synthetic adapters may use callbacks for tests. A missing, failing or throwing
    external verifier never produces accepted QA or publication eligibility.
    """
    submissions = [] if submissions is None else submissions
    if not isinstance(benchmark, dict) or not isinstance(benchmark.get("families"), list) or not isinstance(submissions, list):
        return {"qualityAcceptedCount": 0, "pendingCount": 0,
                "qualityRejectedCount": 0, "publicationApprovedCount": 0,
                "results": [], "issues": [{"code": "GOLDEN_QA_INPUT_INVALID", "caseId": ""}]}
    issues = []
    results = []
    by_case = {}
    for submission in submissions:
        if not isinstance(submission, dict) or not isinstance(submission.get("caseId"), str):
            continue
        case_id = submission["caseId"]
        if case_id in by_case:
            issues.append({"code": "GOLDEN_QA_DUPLICATE_SUBMISSION", "caseId": case_id})
        else:
            by_case[case_id] = submission
    expected = {family["pilotFile"].removesuffix(".json") for family in benchmark["families"]}
    for key in by_case:
        if key not in expected:
            issues.append({"code": "GOLDEN_QA_UNKNOWN_FAMILY", "caseId": key})
    for family in benchmark["families"]:
        case_id = family["pilotFile"].removesuffix(".json")
        submission = by_case.get(case_id)
        status = "pending"
        if submission:
            outcomes = [submission.get("dimensions", {}).get(dimension) for dimension in QUALITY_DIMENSIONS]
            if any(value in ("fail", "disputed", "rejected") for value in outcomes):
                issues.append({"code": "GOLDEN_QA_REJECTED", "caseId": case_id})
                status = "rejected"
            elif (any(value != "pass" for value in outcomes)
                  or not isinstance(submission.get("reviewerId"), str)
                  or not submission.get("authorityReference")
                  or not isinstance(submission.get("evidenceReferences"), list)
                  or not submission["evidenceReferences"]):
                issues.append({"code": "GOLDEN_QA_INCOMPLETE", "caseId": case_id})
            else:
                trusted = callable(verify_independent_review) and callable(verify_rights)
                if trusted:
                    for dimension in QUALITY_DIMENSIONS:
                        try:
                            if verify_independent_review(family=family, submission=submission, dimension=dimension) is not True:
                                trusted = False
                                break
                        except Exception:
                            trusted = False
                            break
                    if trusted:
                        try:
                            trusted = verify_rights(family=family, submission=submission) is True
                        except Exception:
                            trusted = False
                if trusted:
                    status = "independently_reviewed"
                else:
                    issues.append({"code": "GOLDEN_QA_AUTHORITY_UNVERIFIED", "caseId": case_id})
        results.append({"caseId": case_id, "status": status, "publicationEligible": False})
    issues.sort(key=lambda problem: (problem["caseId"], problem["code"]))
    return {"qualityAcceptedCount": sum(item["status"] == "independently_reviewed" for item in results),
            "pendingCount": sum(item["status"] == "pending" for item in results),
            "qualityRejectedCount": sum(item["status"] == "rejected" for item in results),
            "publicationApprovedCount": 0, "results": results, "issues": issues}
