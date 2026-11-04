"""Schema-first validation and conservative lexical/publication quality gates.

Schemas, private evidence, and editorial authorizations are supplied by the private
consumer; no source of authority is bundled with this public Python library.
"""
from __future__ import annotations

from collections import defaultdict
from heapq import heappush, heapreplace
from datetime import datetime
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

PREFIXES = {
    "lex": "lexical-entry", "sns": "lexical-sense", "frm": "lexical-form",
    "fam": "word-family", "mrf": "morpheme", "ety": "etymology",
    "top": "topic", "con": "construction", "col": "collocation",
    "eqv": "translation-equivalence", "rel": "lexical-relation",
    "evd": "evidence", "src": "source", "rev": "editorial-review",
    "dec": "editorial-decision",
}
# Numeric object keys use JS Object.keys ordering. Other JSON keys preserve input order.
_INDEX_KEY = re.compile(r"(?:0|[1-9][0-9]*)\Z")


def _js_order(obj: dict) -> list[str]:
    if not all(isinstance(k, str) for k in obj):
        raise ValueError("JSON object keys must be strings")
    indexed = [k for k in obj if _INDEX_KEY.fullmatch(k) and int(k) < 4294967295]
    return [*sorted(indexed, key=int), *(k for k in obj if k not in indexed)]


def js_json(value: Any) -> str:
    """JSON.stringify-compatible serialization for JSON values, including JS key order."""
    from decimal import Decimal
    def emit(v):
        if v is None:
            return "null"
        if v is True:
            return "true"
        if v is False:
            return "false"
        if isinstance(v, str):
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, dict):
            return "{" + ",".join(emit(k) + ":" + emit(v[k]) for k in _js_order(v)) + "}"
        if isinstance(v, list):
            return "[" + ",".join(emit(item) for item in v) + "]"
        if isinstance(v, int):
            # JSON.parse stores all numbers as IEEE-754 double; integers outside
            # the safe range are rounded before JSON.stringify emits them.
            if abs(v) > 2**53 - 1:
                try:
                    return emit(float(v))
                except OverflowError as exc:
                    raise ValueError("JSON number exceeds finite double range") from exc
            return str(v)
        if isinstance(v, float):
            if not math.isfinite(v):
                raise ValueError("Non-finite JSON number")
            if v == 0:
                return "0"
            if v.is_integer() and abs(v) < 1e21:
                return str(int(v))
            if 1e-6 <= abs(v) < 1e21:
                fixed = format(Decimal(repr(v)), "f")
                return fixed.rstrip("0").rstrip(".") if "." in fixed else fixed
            # JS prints exponent without a leading zero (1e-7, not 1e-07).
            mantissa, exponent = repr(v).lower().split("e")
            if mantissa.endswith(".0"):
                mantissa = mantissa[:-2]
            n = int(exponent)
            return mantissa + "e" + ("+" if n >= 0 else "-") + str(abs(n))
        raise ValueError("Value is not a JSON primitive")
    return emit(value)


def digest(value: Any) -> str:
    return sha256(js_json(value).encode("utf-8")).hexdigest()


def issue(code: str, entity_id: str = "", path: str = "", message: str = "") -> dict:
    return {"code": code, "entityId": entity_id, "path": path, "message": message}


def sort_issues(issues: list[dict]) -> list[dict]:
    return sorted(issues, key=lambda i: (i.get("entityId", ""), i.get("path", ""), i["code"], i.get("message", "")))


class _WorstIssueFirst:
    """Heap entry comparing in reverse, so the largest retained issue is first."""
    __slots__ = ("key", "record")

    def __init__(self, record: dict):
        self.record = record
        self.key = (record.get("entityId", ""), record.get("path", ""),
                    record["code"], record.get("message", ""))

    def __lt__(self, other: "_WorstIssueFirst") -> bool:
        return self.key > other.key


class _BoundedPublicationIssues:
    """Exact diagnostic count with a bounded, deterministically ordered API result."""
    __slots__ = ("limit", "count", "_retained")

    def __init__(self, limit: int | None):
        self.limit = limit
        self.count = 0
        self._retained: list = []

    def append(self, record: dict) -> None:
        self.count += 1
        if self.limit is None:
            self._retained.append(record)
        elif self.limit:
            item = _WorstIssueFirst(record)
            if len(self._retained) < self.limit:
                heappush(self._retained, item)
            elif item.key < self._retained[0].key:
                heapreplace(self._retained, item)

    def result(self) -> list[dict]:
        if self.limit is None:
            return sort_issues(self._retained)
        return sort_issues([item.record for item in self._retained])


def value_at(record: Any, path: str) -> Any:
    """JSON pointer; missing is None-sentinel (None remains an actual valid JSON value)."""
    if not isinstance(path, str) or not path.startswith("/") or path == "/":
        return MISSING
    value = record
    for part in path[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdecimal() and (key == "0" or not key.startswith("0")) and int(key) < len(value):
            value = value[int(key)]
        else:
            return MISSING
    return value


MISSING = object()


class SchemaDirectory:
    """Load an exact version of consumer-owned local JSON Schema; deny network refs."""

    def __init__(self, root: str | Path, version: str = "v2") -> None:
        if version not in ("v1", "v2"):
            raise ValueError("Unsupported schema version")
        root_path = Path(root).resolve(strict=True)
        folder = root_path / version
        if not folder.is_dir():
            raise ValueError(f"Schema directory not found: {version}")
        self.v1_vocabulary = json.loads((root_path / "v1" / "vocabularies.json").read_text(encoding="utf-8"))
        self.version = version
        self.vocabularies = self.v1_vocabulary if version == "v1" else {}
        self.schemas: dict[str, dict] = {}
        resources = []
        for path in sorted(folder.glob("*.schema.json")):
            if path.is_symlink() or not path.resolve().is_relative_to(folder):
                raise ValueError("Schema path escapes supplied directory")
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("$id"), str):
                raise ValueError("Schema must declare a stable $id")
            Draft202012Validator.check_schema(data)
            resources.append((data["$id"], Resource.from_contents(data)))
            self.schemas[path.stem.removesuffix(".schema")] = data
        missing = sorted(set(PREFIXES.values()) - set(self.schemas))
        if missing:
            raise ValueError("Missing required schemas: " + ", ".join(missing))
        registry = Registry().with_resources(resources)
        self.validators = {
            prefix: Draft202012Validator(self.schemas[name], registry=registry)
            for prefix, name in PREFIXES.items()
        }

    def validate(self, records: list) -> list[dict]:
        failures = []
        for i, record in enumerate(records):
            identifier = record.get("_id", "") if isinstance(record, dict) else ""
            identifier = identifier if isinstance(identifier, str) else ""
            validator = self.validators.get(identifier.split("_", 1)[0])
            if validator is None:
                prefix = identifier.split("_", 1)[0]
                message = ("No active V1 entity schema for prefix " + prefix
                           if self.version == "v1" else "Unknown entity type")
                failures.append(issue("SCHEMA_INVALID", identifier, f"records/{i}/_id", message))
                continue
            try:
                errors = list(validator.iter_errors(record))
            except Exception as exc:
                # An unresolvable $ref or bad external schema must never become PASS.
                failures.append(issue("SCHEMA_INVALID", identifier, f"records/{i}", "Schema resolution failed: " + type(exc).__name__))
                continue
            def append_error(error):
                # Ajv allErrors expands failed oneOf/anyOf branches; jsonschema
                # nests their errors in context. Retain the same diagnostic fan-out.
                if error.validator in ("oneOf", "anyOf"):
                    for child in error.context:
                        append_error(child)
                pointer = "/".join(str(part) for part in error.absolute_path)
                suffix = "/" + pointer if pointer else "/"
                if error.validator == "required":
                    m = re.search(r"'([^']+)' is a required property", error.message)
                    if m:
                        suffix += m.group(1)
                messages = {
                    "oneOf": "must match exactly one schema in oneOf",
                    "anyOf": "must match a schema in anyOf",
                    "not": "must NOT be valid",
                    "const": "must be equal to constant",
                    "enum": "must be equal to one of the allowed values",
                    "additionalProperties": "must NOT have additional properties",
                }
                if error.validator == "pattern":
                    detail = f'must match pattern "{error.validator_value}"'
                elif error.validator == "required":
                    detail = "must have required property " + repr(m.group(1)) if m else error.message
                else:
                    detail = messages.get(error.validator, error.message)
                failures.append(issue("SCHEMA_INVALID", identifier,
                                      f"records/{i}{suffix}", f"{error.validator}: {detail}"))
            for e in errors:
                append_error(e)
        return sort_issues(failures)


def _refs(record: dict) -> list[tuple[str, str, set[str]]]:
    prefix = record["_id"].split("_", 1)[0]
    config: dict[str, tuple[str, ...]] = {
        "sns": ("entryId", "lex"), "frm": ("entryId", "lex"),
        "ety": ("entryId", "lex"), "con": ("senseId", "sns"),
        "col": ("anchorSenseId", "sns"), "eqv": ("sourceSenseId", "sns"),
    }
    out: list[tuple[str, str, set[str]]] = []
    if prefix in config:
        field, expected = config[prefix]
        if field in record and isinstance(record[field], str):
            out.append((field, record[field], {expected}))
    for field, expected in {
        "targetSenseId": "sns", "parentTopicId": "top", "sourceId": "src",
    }.items():
        if isinstance(record.get(field), str) and (prefix, field) in {("eqv", "targetSenseId"), ("top", "parentTopicId"), ("evd", "sourceId")}:
            out.append((field, record[field], {expected}))
    for field, expected in {
        "familyIds": "fam", "topicIds": "top", "constructionIds": "con", "evidenceIds": "evd",
        "pronunciationEvidenceIds": "evd", "assertionIds": "*", "subjectIds": "*",
        "previousSenseIds": "sns", "proposedSenseIds": "sns", "supersededByIds": "sns",
    }.items():
        if field not in record:
            continue
        if field == "subjectIds" and prefix not in ("rev", "dec"):
            continue
        if field == "assertionIds" and prefix != "evd":
            continue
        for index, target in enumerate(record[field] if isinstance(record[field], list) else []):
            if isinstance(target, str):
                out.append((f"{field}/{index}", target, {expected}))
    if prefix == "rel":
        for field in ("sourceId", "targetId"):
            if isinstance(record.get(field), str):
                out.append((field, record[field], {"lex", "sns", "frm", "con", "col", "mrf", "fam", "top"}))
    if prefix == "dec" and isinstance(record.get("entryId"), str):
        out.append(("entryId", record["entryId"], {"lex"}))
    return out



def validate_v1_graph(records: list[dict], vocabulary: dict, *, publication: bool = False) -> dict:
    """V1 semantic and publication diagnostics; vocabulary is caller-supplied.

    V1 review metadata cannot establish an independently authenticated
    owner-appointed editorial authority, even when all schema checks pass.
    """
    import unicodedata

    graph: list[dict] = []
    publication_errors: list[dict] = []
    by_id: dict[str, dict] = {}
    active = set(vocabulary["identifiers"]["activeV1Prefixes"])
    name_to_prefix = {name: p for p, name in {
        "lex": "LexicalEntry", "sns": "LexicalSense", "frm": "LexicalForm",
        "fam": "WordFamily", "mrf": "Morpheme", "ety": "Etymology",
        "top": "Topic", "con": "Construction", "col": "Collocation",
        "eqv": "TranslationEquivalence", "rel": "LexicalRelation",
        "evd": "Evidence", "src": "Source", "rev": "EditorialReview",
        "dec": "EditorialDecision",
    }.items()}
    rules = {r["type"]: r for r in vocabulary["relationTypes"]}
    adjacency: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    seen_links: dict[tuple, str] = {}

    def emit(code: str, ident: str, path: str, message: str, *, publishing: bool = False) -> None:
        (publication_errors if publishing else graph).append(issue(code, ident, path, message))

    for record in sorted(records, key=lambda r: r.get("_id", "") if isinstance(r, dict) else ""):
        if not isinstance(record, dict):
            emit("RECORD_INVALID", "", "records", "Record must be an object")
            continue
        ident = record.get("_id")
        if not isinstance(ident, str):
            emit("IDENTITY_INVALID", "", "_id", "Missing canonical identifier")
            continue
        prefix = ident.split("_", 1)[0]
        if prefix not in active:
            emit("INACTIVE_OR_UNKNOWN_ENTITY", ident, "_id", "Prefix is not active in V1")
        elif ident in by_id:
            emit("DUPLICATE_ID", ident, "_id", "Canonical ID occurs more than once")
        else:
            by_id[ident] = record

    def reference(owner: dict, path: str, ident: Any, expected: tuple[str, ...],
                  historical: bool = False) -> dict | None:
        if not isinstance(ident, str):
            return None
        target = by_id.get(ident)
        if target is None:
            emit("REFERENCE_NOT_FOUND", owner["_id"], path, "Referenced entity does not exist: " + ident)
            return None
        prefix = ident.split("_", 1)[0]
        if expected and prefix not in expected:
            emit("REFERENCE_TYPE_MISMATCH", owner["_id"], path,
                 "Expected " + "|".join(expected) + ", found " + prefix)
            return None
        if not historical and owner.get("lifecycleStatus") == "active" and target.get("lifecycleStatus") == "retired":
            emit("RETIRED_LIVE_REFERENCE", owner["_id"], path, "Active record references retired entity: " + ident)
        return target

    def each(owner: dict, field: str, types: tuple[str, ...], historical: bool = False) -> None:
        value = owner.get(field)
        if isinstance(value, list):
            for idx, ident in enumerate(value):
                reference(owner, f"{field}/{idx}", ident, types, historical)
        elif isinstance(value, str):
            reference(owner, field, value, types, historical)

    for record in sorted(records, key=lambda r: r.get("_id", "") if isinstance(r, dict) else ""):
        if not isinstance(record, dict) or not isinstance(record.get("_id"), str):
            continue
        ident = record["_id"]
        if by_id.get(ident) is not record:
            continue
        prefix = ident.split("_", 1)[0]
        if prefix not in active:
            continue
        each(record, "evidenceIds", ("evd",), True)
        if record.get("lifecycleStatus") == "retired":
            each(record, "retiredByDecisionId", ("dec",), True)
            each(record, "supersededByIds", ("sns",) if prefix == "sns" else tuple(sorted(active)), True)
            if not record.get("retiredByDecisionId"):
                emit("RETIREMENT_INVALID", ident, "retiredByDecisionId", "Retirement requires a decision")
        elif any(record.get(k) for k in ("retirementMode", "retiredByDecisionId", "supersededByIds")):
            emit("RETIREMENT_INVALID", ident, "lifecycleStatus", "Active record cannot carry retirement metadata")

        if prefix == "lex":
            each(record, "familyIds", ("fam",))
        elif prefix == "sns":
            each(record, "entryId", ("lex",))
            each(record, "topicIds", ("top",))
        elif prefix in ("frm", "ety"):
            each(record, "entryId", ("lex",))
        elif prefix == "con":
            each(record, "senseId", ("sns",))
        elif prefix == "col":
            each(record, "anchorSenseId", ("sns",))
        elif prefix == "top":
            parent = record.get("parentTopicId")
            if parent is not None:
                each(record, "parentTopicId", ("top",))
                if parent in by_id:
                    adjacency["TOPIC_PARENT"][ident].add(parent)
        elif prefix == "eqv":
            source = reference(record, "sourceSenseId", record.get("sourceSenseId"), ("sns",))
            target = (reference(record, "targetSenseId", record.get("targetSenseId"), ("sns",))
                      if record.get("alignmentMode") == "sense_aligned" else None)
            src_entry = by_id.get(source.get("entryId")) if source else None
            tgt_entry = by_id.get(target.get("entryId")) if target else None
            if src_entry and src_entry.get("language") != "en":
                emit("BILINGUAL_LANGUAGE_MISMATCH", ident, "sourceSenseId",
                     "Source sense must belong to an English entry")
            if tgt_entry and tgt_entry.get("language") != "vi":
                emit("BILINGUAL_LANGUAGE_MISMATCH", ident, "targetSenseId",
                     "Target sense must belong to a Vietnamese entry")
            if record.get("alignmentMode") == "gloss_only" and "targetSenseId" in record:
                emit("ALIGNMENT_MODE_INVALID", ident, "targetSenseId",
                     "Gloss-only may not contain a target sense")
            if record.get("alignmentMode") == "sense_aligned" and not record.get("targetSenseId"):
                emit("ALIGNMENT_MODE_INVALID", ident, "targetSenseId",
                     "Aligned mode requires a real target sense")
        elif prefix == "rel":
            rule = rules.get(record.get("relationType"))
            if rule is None:
                emit("RELATION_TYPE_UNKNOWN", ident, "relationType", "Unregistered relation type")
                continue
            source_type, target_type = name_to_prefix[rule["sourceType"]], name_to_prefix[rule["targetType"]]
            source = reference(record, "sourceId", record.get("sourceId"), (source_type,))
            target = reference(record, "targetId", record.get("targetId"), (target_type,))
            sid, tid = record.get("sourceId"), record.get("targetId")
            if sid == tid:
                emit("RELATION_SELF_LOOP", ident, "targetId", "Self-loop prohibited")
            if rule.get("symmetric") and isinstance(sid, str) and isinstance(tid, str) and sid > tid:
                emit("RELATION_NOT_CANONICAL", ident, "sourceId",
                     "Symmetric storage order must be ascending ASCII")
            if source is not None and target is not None:
                if (rule["sourceType"] == "LexicalSense" and rule["targetType"] == "LexicalSense"
                        and source.get("lifecycleStatus") == target.get("lifecycleStatus") == "active"):
                    se, te = by_id.get(source.get("entryId")), by_id.get(target.get("entryId"))
                    if (se and te and se.get("lifecycleStatus") == te.get("lifecycleStatus") == "active"
                            and se["language"].split("-")[0] != te["language"].split("-")[0]):
                        emit("RELATION_LANGUAGE_MISMATCH", ident, "targetId",
                             "Sense-to-sense lexical relation requires compatible source and target languages")
                first, second = (tid, sid) if rule.get("symmetric") and sid > tid else (sid, tid)
                qualifiers = record.get("qualifiers") or {}
                key_fields = vocabulary["relationModel"]["qualifierFieldsInOrder"]
                qualifier_key = js_json([[k, unicodedata.normalize("NFC", qualifiers[k])]
                                         for k in key_fields if k in qualifiers])
                link_key = (record["relationType"], first, second, qualifier_key)
                previous = seen_links.get(link_key)
                if previous:
                    emit("DUPLICATE_SEMANTIC_EDGE", ident, "relationType",
                         "Same typed endpoints and semantic qualifiers as " + previous)
                else:
                    seen_links[link_key] = ident
                if (rule.get("cyclePolicy") == "reject" and
                        record.get("lifecycleStatus") == source.get("lifecycleStatus") == target.get("lifecycleStatus") == "active"):
                    adjacency[record["relationType"]][sid].add(tid)
        elif prefix == "evd":
            each(record, "sourceId", ("src",), True)
            each(record, "assertionIds", tuple(sorted(active)), True)
        elif prefix == "rev":
            each(record, "subjectIds", tuple(sorted(active)), True)
        elif prefix == "dec":
            for field, expected in {
                "subjectIds": tuple(sorted(active)), "entryId": ("lex",),
                "previousSenseIds": ("sns",), "proposedSenseIds": ("sns",),
                "supersedesDecisionId": ("dec",), "affectedEdges": ("rel",),
            }.items():
                each(record, field, expected, True)

    for edge_type, mapping in sorted(adjacency.items()):
        visited: set[str] = set()
        active_stack: dict[str, int] = {}
        for start in sorted(mapping):
            if start in visited:
                continue
            stack: list[tuple[str, list[str], int]] = [(start, sorted(mapping[start]), 0)]
            active_stack[start] = 0
            visited.add(start)
            while stack:
                ident, neighbors, next_index = stack[-1]
                if next_index == len(neighbors):
                    active_stack.pop(ident, None)
                    stack.pop()
                    continue
                target = neighbors[next_index]
                stack[-1] = (ident, neighbors, next_index + 1)
                if target not in visited:
                    visited.add(target)
                    active_stack[target] = len(stack)
                    stack.append((target, sorted(mapping.get(target, ())), 0))
                elif target in active_stack:
                    cycle = [item[0] for item in stack[active_stack[target]:]] + [target]
                    emit("CYCLE_DETECTED", ident, edge_type, " -> ".join(cycle))

    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for sense in (r for r in by_id.values() if r["_id"].startswith("sns_") and r.get("lifecycleStatus") == "retired"):
        decision = by_id.get(sense.get("retiredByDecisionId"))
        if not decision or not decision["_id"].startswith("dec_"):
            continue
        mode = sense.get("retirementMode")
        if mode in ("semantic_split", "semantic_merge", "duplicate_reconciliation"):
            groups[(decision["_id"], mode)].append(sense)
        action = {"semantic_split": "split", "semantic_merge": "merge",
                  "duplicate_reconciliation": "duplicate_reconciliation", "invalidated": "invalidate"}.get(mode)
        if (decision.get("action") != action or sense["_id"] not in decision.get("subjectIds", []) or
                sense["_id"] not in decision.get("previousSenseIds", []) or
                sorted(decision.get("proposedSenseIds") or []) != sorted(sense.get("supersededByIds") or [])):
            emit("DECISION_TRANSITION_MISMATCH", sense["_id"], "retiredByDecisionId",
                 "Decision action, source IDs, or proposed successors do not match retirement")
        if decision.get("outcome") != "accepted":
            emit("DECISION_NOT_ACCEPTED", sense["_id"], "retiredByDecisionId",
                 "Transition decision is not accepted")
        for successor_id in sense.get("supersededByIds", ()):
            successor = by_id.get(successor_id)
            if successor and successor.get("lifecycleStatus") != "active":
                emit("SUCCESSOR_RETIRED", sense["_id"], "supersededByIds", "Successor must be active")
            if successor and successor.get("entryId") != sense.get("entryId"):
                emit("SUCCESSOR_OWNERSHIP_MISMATCH", sense["_id"], "supersededByIds",
                     "Successor belongs to a different entry")
        if mode in ("semantic_split", "semantic_merge", "duplicate_reconciliation"):
            emit("INSUFFICIENT_EVIDENCE", sense["_id"], "retiredByDecisionId",
                 "Cannot prove new/existing successor identity or approved incoming-edge resolution",
                 publishing=True)
        if mode == "duplicate_reconciliation" and not (decision.get("evidenceIds") and sense.get("evidenceIds")):
            emit("INSUFFICIENT_EVIDENCE", sense["_id"], "evidenceIds",
                 "Duplicate identity has no independently reviewable evidence", publishing=True)
    for (decision_id, mode), group in groups.items():
        decision = by_id[decision_id]
        if mode == "semantic_merge":
            if (len(group) < vocabulary["retirementRequirements"]["semantic_merge"]["retiredSourceMin"] or
                    len(decision.get("previousSenseIds", ())) != len(group) or
                    len({tuple(s.get("supersededByIds") or [None])[0] for s in group}) != 1):
                emit("MERGE_GROUP_INVALID", decision_id, "previousSenseIds",
                     "Merge requires two or more retired sources and exactly one common successor")
        elif len(group) != 1 or len(decision.get("previousSenseIds", ())) != 1:
            emit("RETIREMENT_GROUP_INVALID", decision_id, "previousSenseIds",
                 "Split/reconciliation requires exactly one retired source")

    accepted_reviews: set[str] = set()
    for review in by_id.values():
        if (review["_id"].startswith("rev_") and review.get("lifecycleStatus") == "active"
                and review.get("outcome") == "accepted"):
            accepted_reviews.update(review.get("subjectIds") or [])
    for record in by_id.values():
        if record.get("lifecycleStatus") != "active":
            continue
        ident = record["_id"]
        status = record.get("editorialStatus")
        if status in ("disputed", "rejected"):
            emit("PUBLICATION_STATUS_BLOCKED", ident, "editorialStatus",
                 "Disputed/rejected claim may not be published", publishing=True)
        elif status != "verified":
            emit("PUBLICATION_NOT_VERIFIED", ident, "editorialStatus",
                 "Only verified records may be published", publishing=True)
        else:
            if not record.get("evidenceIds"):
                emit("PUBLICATION_EVIDENCE_MISSING", ident, "evidenceIds",
                     "Verified claim requires evidence", publishing=True)
            else:
                for ev_id in record["evidenceIds"]:
                    ev = by_id.get(ev_id)
                    source = by_id.get(ev.get("sourceId")) if ev else None
                    if not ev or ident not in ev.get("assertionIds", ()):
                        emit("PUBLICATION_EVIDENCE_MISSING", ident, "evidenceIds",
                             "Evidence must attest the specific claim", publishing=True)
                    if not source or not source.get("license") or not source.get("permittedUse"):
                        emit("INSUFFICIENT_EVIDENCE", ident, "evidenceIds",
                             "Source permission/licensing not verifiable", publishing=True)
            if ident not in accepted_reviews:
                emit("PUBLICATION_REVIEW_MISSING", ident, "editorialStatus",
                     "No accepted editorial review addresses this claim", publishing=True)
            emit("INSUFFICIENT_EVIDENCE", ident, "editorialStatus",
                 "V1 cannot establish owner-appointed LER authority, final approval and permitted reuse",
                 publishing=True)
    graph = sort_issues(graph)
    publication_errors = sort_issues(publication_errors)
    selected = sort_issues(graph + publication_errors) if publication else graph
    return {
        "valid": not graph and (not publication or not publication_errors),
        "stage": "graph",
        "issues": selected,
        "publication": {"eligible": not graph and not publication_errors,
                        "issues": publication_errors},
    }


def graph_issues(records: list[dict], version: str = "v2") -> list[dict]:
    by_id: dict[str, dict] = {}
    errors: list[dict] = []
    for record in sorted(records, key=lambda r: r.get("_id", "")):
        identifier = record["_id"]
        if identifier in by_id:
            errors.append(issue("DUPLICATE_ID", identifier, "_id", "Canonical ID occurs more than once"))
        by_id[identifier] = record
    topics = {}
    edges: dict[str, set[str]] = defaultdict(set)
    seen_edges: set[tuple] = set()
    for r in sorted(records, key=lambda x: x["_id"]):
        identifier = r["_id"]
        prefix = identifier.split("_", 1)[0]
        for field, target_id, expected in _refs(r):
            target = by_id.get(target_id)
            if target is None:
                errors.append(issue("REFERENCE_NOT_FOUND", identifier, field, "Referenced entity does not exist: " + target_id))
            elif "*" not in expected and target_id.split("_", 1)[0] not in expected:
                errors.append(issue("REFERENCE_TYPE_MISMATCH", identifier, field, "Unexpected reference type: " + target_id))
            elif r.get("lifecycleStatus") == "active" and target.get("lifecycleStatus") == "retired":
                errors.append(issue("RETIRED_LIVE_REFERENCE", identifier, field, "Active record references retired entity: " + target_id))
        if prefix == "top" and isinstance(r.get("parentTopicId"), str):
            topics[identifier] = r["parentTopicId"]
        if prefix == "rel":
            source, target = r.get("sourceId"), r.get("targetId")
            if source == target:
                errors.append(issue("RELATION_SELF_LOOP", identifier, "targetId", "Self-loop prohibited"))
            symmetric = r.get("relationType") in {"SYNONYM_OF", "NEAR_SYNONYM_OF", "ANTONYM_OF"}
            if symmetric and isinstance(source, str) and isinstance(target, str) and source > target:
                errors.append(issue("RELATION_NOT_CANONICAL", identifier, "sourceId", "Symmetric storage order must be ascending ASCII"))
            if isinstance(source, str) and isinstance(target, str):
                key = (r.get("relationType"), source, target, js_json(r.get("qualifiers", {})))
                if key in seen_edges:
                    errors.append(issue("DUPLICATE_SEMANTIC_EDGE", identifier, "relationType", "Duplicate typed semantic edge"))
                seen_edges.add(key)
        if prefix == "sns":
            for n, cid in enumerate(r.get("constructionIds", [])):
                other = by_id.get(cid)
                if other and other.get("senseId") != identifier:
                    errors.append(issue("CONSTRUCTION_SENSE_MISMATCH", identifier, f"constructionIds/{n}", "Construction belongs to a different sense"))
        if prefix == "evd" and isinstance(r.get("claim"), dict):
            claim = r["claim"]
            subject = claim.get("subjectId")
            target = by_id.get(subject)
            path = claim.get("fieldPath")
            if not target or subject not in r.get("assertionIds", []) or value_at(target, path) is MISSING or digest(value_at(target, path)) != claim.get("valueDigest"):
                errors.append(issue("EVIDENCE_CLAIM_MISMATCH", identifier, "claim", "Claim digest, path or assertion ID does not match"))
        if prefix == "rev":
            for n, claim in enumerate(r.get("claims", [])):
                if not isinstance(claim, dict) or claim.get("subjectId") not in r.get("subjectIds", []) or value_at(by_id.get(claim.get("subjectId")), claim.get("fieldPath")) is MISSING:
                    errors.append(issue("EDITORIAL_CLAIM_MISMATCH", identifier, f"claims/{n}", "Review is not scoped to an existing exact claim"))
        if prefix == "dec" and "decisionEffect" in r:
            transition = r.get("action") in {"split", "merge", "duplicate_reconciliation", "invalidate"}
            if (transition and r["decisionEffect"] != "lifecycle_transition") or (r.get("action") == "retain" and r["decisionEffect"] != "non_transition"):
                errors.append(issue("DECISION_EFFECT_MISMATCH", identifier, "decisionEffect", "Decision effect contradicts the stated action"))
            if r["decisionEffect"] == "lifecycle_transition" and not r.get("transitionAudit"):
                errors.append(issue("LIFECYCLE_AUDIT_INCOMPLETE", identifier, "transitionAudit", "Transition requires incoming-edge adjudication"))
    # An actual topic-parent cycle is a semantic error. Iterative DFS handles deep graphs.
    for start in sorted(topics):
        seen: set[str] = set()
        current = start
        while current in topics:
            if current in seen:
                errors.append(issue("CYCLE_DETECTED", start, "parentTopicId", "Topic parent cycle"))
                break
            seen.add(current)
            current = topics[current]
    return sort_issues(errors)



def lifecycle_v2_issues(records: list[dict]) -> list[dict]:
    """Check V2 retirement and supersession integrity without trusting audit assertions."""
    by_id = {record["_id"]: record for record in records}
    incoming: dict[str, set[str]] = defaultdict(set)
    fields = {
        "rel": ("sourceId", "targetId"), "eqv": ("sourceSenseId", "targetSenseId"),
        "con": ("senseId",), "col": ("anchorSenseId",),
    }
    for record in records:
        kind = record["_id"].split("_", 1)[0]
        for field in fields.get(kind, ()):
            target = record.get(field)
            if isinstance(target, str) and target.startswith("sns_"):
                incoming[target].add(record["_id"])

    issues = []
    for decision in records:
        identifier = decision["_id"]
        if not identifier.startswith("dec_") or decision.get("decisionEffect") != "lifecycle_transition":
            continue
        audit = decision.get("transitionAudit") or {}
        edges = audit.get("incomingEdges") or []
        affected = {edge.get("edgeId") for edge in edges}
        for predecessor in decision.get("previousSenseIds") or []:
            old = by_id.get(predecessor)
            if not old or old.get("lifecycleStatus") != "retired" or old.get("retiredByDecisionId") != identifier:
                issues.append(issue("LIFECYCLE_SOURCE_NOT_RETIRED", identifier, "previousSenseIds",
                                    "Consumed transition requires a retired predecessor tied to this decision"))
            for edge_id in sorted(incoming.get(predecessor, ())):
                if edge_id not in affected:
                    issues.append(issue("LIFECYCLE_INCOMING_EDGE_UNRESOLVED", identifier,
                                        "transitionAudit/incomingEdges",
                                        "Incoming edge " + edge_id + " has no explicit adjudication"))
        seen_edges = set()
        for edge in edges:
            edge_id = edge.get("edgeId")
            if edge_id in seen_edges:
                issues.append(issue("LIFECYCLE_EDGE_DUPLICATE", identifier,
                                    "transitionAudit/incomingEdges", "Same edge has multiple resolutions"))
            seen_edges.add(edge_id)
            if edge.get("decisionId") != identifier:
                issues.append(issue("LIFECYCLE_EDGE_DECISION_MISMATCH", identifier,
                                    "transitionAudit/incomingEdges", "Incoming edge references another decision"))
            if edge.get("resolution") == "reattached" and not edge.get("targetId"):
                issues.append(issue("LIFECYCLE_EDGE_TARGET_MISSING", identifier,
                                    "transitionAudit/incomingEdges", "Reattached edge requires explicit target"))
            if edge_id not in by_id:
                issues.append(issue("LIFECYCLE_EDGE_HISTORY_MISSING", identifier,
                                    "transitionAudit/incomingEdges",
                                    "Recorded edge is absent from current snapshot; historical proof is required"))

    # Iterative functional-graph traversal: O(records + supersession edges), no recursion.
    decisions = [r for r in records if r["_id"].startswith("dec_") and r.get("supersedesDecisionId")]
    visit_state: dict[str, int] = {}
    reaches_cycle: dict[str, bool] = {}
    for start in decisions:
        if visit_state.get(start["_id"]) == 2:
            continue
        path = []
        current = start
        cycle = False
        while current and current.get("supersedesDecisionId"):
            key = current["_id"]
            status = visit_state.get(key)
            if status == 2:
                cycle = reaches_cycle.get(key) is True
                break
            if status == 1:
                cycle = True
                break
            visit_state[key] = 1
            path.append(key)
            current = by_id.get(current["supersedesDecisionId"])
        for key in path:
            visit_state[key] = 2
            reaches_cycle[key] = cycle
    for start in decisions:
        if reaches_cycle.get(start["_id"]):
            issues.append(issue("DECISION_SUPERSESSION_CYCLE", start["_id"],
                                "supersedesDecisionId", "Decision supersession cycle"))
    return sort_issues(issues)

def _parse_time(s: str | None) -> datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def reviewer_appointed(options: Mapping[str, Any], reviewer_id: str, role: str, reference: str, subject_id: str, field_path: str | None = None) -> bool:
    callback = options.get("verify_owner_attestation")
    owner = options.get("owner_id")
    now = _parse_time(options.get("effective_at"))
    if not callable(callback) or not owner or not now or not reference:
        return False
    appointments = options.get("appointments")
    if not isinstance(appointments, list):
        return False
    for appointment in appointments:
        if not isinstance(appointment, dict) or any((appointment.get("appointmentKey") != reference, appointment.get("reviewerId") != reviewer_id, appointment.get("role") != role, appointment.get("appointedByOwnerId") != owner, owner == reviewer_id, not appointment.get("ownerAttestationRef"))):
            continue
        scope = appointment.get("scope") or {}
        if subject_id not in scope.get("subjectIds", ()):
            continue
        if "fieldPaths" in scope and field_path not in scope["fieldPaths"]:
            continue
        since = _parse_time(appointment.get("effectiveFrom"))
        until = _parse_time(appointment.get("effectiveUntil")) if "effectiveUntil" in appointment else None
        revoked = _parse_time(appointment.get("revokedAt")) if appointment.get("revokedAt") else None
        try:
            inactive = not since or now < since or ("effectiveUntil" in appointment and (not until or now > until)) or (revoked and now >= revoked)
        except TypeError:
            inactive = True
        if inactive:
            continue
        try:
            if callback(appointment=appointment, owner_id=owner, subject_id=subject_id, field_path=field_path, role=role) is True:
                return True
        except Exception:
            pass
    return False


def publication_claim_paths(record: dict) -> list[str]:
    # Preserve claim-level authorization, including nested examples and answer-relevant fields.
    prefix = record["_id"].split("_", 1)[0]
    fields = {
        "lex": ("language", "lemma", "pos", "entryKind", "multiwordKind", "familyIds", "homographNumber"),
        "sns": ("entryId", "definition", "topicIds", "register", "usageNote", "domain", "constructionIds"),
        "eqv": ("sourceSenseId", "targetSenseId", "glossVi", "direction", "alignmentMode", "equivalenceType", "context", "register"),
        "con": ("senseId", "constructionType", "pattern", "prepositions", "complements", "separable"),
        "rel": ("relationType", "sourceId", "targetId"),
        "col": ("anchorSenseId", "pattern", "components", "context"),
        "frm": ("entryId", "form", "locale", "ipa", "stress", "pronunciationVariety"),
        "fam": ("familyBasis", "label"), "top": ("label", "parentTopicId"),
        "mrf": ("form", "morphologicalRole", "language"),
        "ety": ("entryId", "historicalForm", "sourceLanguage", "notes"),
    }.get(prefix, ())
    paths = ["/" + f for f in fields if f in record]
    for nested in ("grammar", "qualifiers"):
        if nested in record and isinstance(record[nested], dict):
            paths.extend(f"/{nested}/{k}" for k in sorted(record[nested]))
    for field, keys in {"usageRestrictions": ("kind", "description"), "contrastiveNotes": ("language", "text"), "examples": ("english", "exampleType", "vi", "register", "variety")}.items():
        if field in record and isinstance(record[field], list):
            for n, item in enumerate(record[field]):
                paths.extend(f"/{field}/{n}/{key}" for key in keys if isinstance(item, dict) and key in item)
    if prefix == "eqv":
        paths.extend(f"/restrictions/{n}" for n, _ in enumerate(record.get("restrictions", [])))
    if prefix == "con":
        paths.extend(f"/constraints/{n}" for n, _ in enumerate(record.get("constraints", [])))
    return sorted(set(paths))


def evaluate_publication(records: list[dict], options: Mapping[str, Any] | None = None, limit: int | None = 1000) -> dict:
    opts = options or {}
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("publicationIssueLimit must be non-negative integer or null")
    by_id = {r["_id"]: r for r in records}
    # Node publication counts the V1 registry graph, not the V2-only storage extras.
    # External authority remains independently fail-closed.
    vocabulary = opts.get("v1_vocabulary")
    graph_problems = (validate_v1_graph(records, vocabulary)["issues"]
                      if isinstance(vocabulary, dict) else graph_issues(records))
    problems = _BoundedPublicationIssues(limit)
    for graph_problem in graph_problems:
        problems.append(graph_problem)
    del graph_problems

    def add(code, identifier="", path="", msg=""):
        problems.append(issue(code, identifier, path, msg))
    publishable = lambda r: r["_id"].split("_", 1)[0] not in {"src", "evd", "rev", "dec"}
    if "subject_ids" in opts:
        scope = opts["subject_ids"]
        if not isinstance(scope, list):
            add("PUBLICATION_SCOPE_INVALID", "", "subjectIds", "Subject scope must be an array")
            scope = []
        wanted: set[str] = set()
        constructions_by_sense: dict[str, list[str]] = defaultdict(list)
        for item in records:
            if item["_id"].startswith("con_") and item.get("senseId"):
                constructions_by_sense[item["senseId"]].append(item["_id"])
        stack = list(scope)
        while stack:
            identifier = stack.pop()
            r = by_id.get(identifier)
            if r is None:
                add("PUBLICATION_SUBJECT_NOT_FOUND", str(identifier), "subjectIds", "Unknown publication subject")
                continue
            if identifier in wanted:
                continue
            wanted.add(identifier)
            kind = r["_id"].split("_", 1)[0]
            references = []
            if kind in ("sns", "frm", "ety"):
                references.append(r.get("entryId"))
            if kind == "con":
                references.append(r.get("senseId"))
            if kind == "col":
                references.append(r.get("anchorSenseId"))
            if kind == "eqv":
                references.extend((r.get("sourceSenseId"), r.get("targetSenseId")))
            if kind == "rel":
                references.extend((r.get("sourceId"), r.get("targetId")))
            if kind == "lex":
                references.extend(r.get("familyIds", ()))
            if kind == "sns":
                references.extend(r.get("topicIds", ()))
                references.extend(r.get("constructionIds", ()))
                references.extend(constructions_by_sense.get(identifier, ()))
            stack.extend(ref for ref in references if ref)
    else:
        wanted = {r["_id"] for r in records if publishable(r)}
    published = [r for r in records if publishable(r) and r["_id"] in wanted]
    if not published:
        add("NO_PUBLISHABLE_CLAIMS", "", "records", "Nothing is eligible for publication")
    evidence = defaultdict(list)
    reviews = defaultdict(list)
    for r in records:
        if r["_id"].startswith("evd_") and isinstance(r.get("claim"), dict):
            evidence[r["claim"].get("subjectId")].append(r)
        if r["_id"].startswith("rev_") and r.get("lifecycleStatus") == "active" and r.get("outcome") == "accepted":
            for c in r.get("claims", []):
                reviews[c.get("subjectId")].append((r, c))
    def rights(ev):
        source = by_id.get(ev.get("sourceId")) or {}
        r = source.get("rights") or {}
        if r.get("status") != "redistributable" or opts.get("usage_scope", "dictionary_publication") not in r.get("scopes", ()) or not r.get("reviewerId") or r.get("revokedAt"):
            return False
        expiry = _parse_time(r.get("expiresAt")) if r.get("expiresAt") else None
        now = _parse_time(opts.get("effective_at"))
        try:
            if r.get("expiresAt") and (not expiry or not now or expiry <= now):
                return False
        except TypeError:
            return False
        if not reviewer_appointed(opts, r["reviewerId"], "rights", r.get("permissionReference"), source["_id"]):
            return False
        try:
            return opts.get("verify_rights")(source=source, scope=opts.get("usage_scope", "dictionary_publication")) is True
        except Exception:
            return False
    for r in published:
        identifier = r["_id"]
        if r.get("lifecycleStatus") != "active":
            add("RETIRED_PUBLICATION_BLOCKED", identifier, "lifecycleStatus", "Inactive claim cannot be published")
        if r.get("editorialStatus") != "verified":
            add("DISPUTED_PUBLICATION_BLOCKED" if r.get("editorialStatus") == "disputed" else "PUBLICATION_NOT_VERIFIED", identifier, "editorialStatus", "Editorial status is not verified")
        eligible = []
        for ev in evidence.get(identifier, ()):
            claim = ev["claim"]
            value = value_at(r, claim.get("fieldPath"))
            if ev["_id"] not in r.get("evidenceIds", ()) or identifier not in ev.get("assertionIds", ()) or value is MISSING or digest(value) != claim.get("valueDigest") or claim.get("specificity") != "exact_value" or ev.get("reviewStatus") != "accepted" or ev.get("independence") != "independent_reviewed" or ev.get("lifecycleStatus") != "active":
                continue
            try:
                verified = opts.get("verify_evidence")(evidence=ev, subject=r, source=by_id.get(ev.get("sourceId"))) is True
            except Exception:
                verified = False
            if verified:
                eligible.append(ev)
        if not eligible:
            add("CLAIM_EVIDENCE_INSUFFICIENT", identifier, "evidenceIds", "Independent exact evidence not authenticated")
        if not any(rights(ev) for ev in eligible):
            add("SOURCE_RIGHTS_UNVERIFIED", identifier, "evidenceIds", "Authenticated intended-use rights absent")
        accepted = []
        for rev, claim in reviews.get(identifier, ()):
            val = value_at(r, claim.get("fieldPath"))
            if val is MISSING or claim.get("valueDigest") != digest(val) or not any(ev["claim"].get("fieldPath") == claim.get("fieldPath") and ev["claim"].get("valueDigest") == claim.get("valueDigest") for ev in eligible):
                continue
            if identifier in rev.get("subjectIds", ()) and reviewer_appointed(opts, rev.get("reviewerId"), rev.get("role"), rev.get("appointmentReference"), identifier, claim.get("fieldPath")):
                accepted.append((rev, claim))
        if not accepted:
            add("REVIEW_SCOPE_MISSING", identifier, "editorialStatus", "No authorized accepted exact review")
        if not any(rev.get("role") == "lead_editorial" for rev, _ in accepted):
            add("LER_AUTHORITY_UNVERIFIED", identifier, "editorialStatus", "Lead editorial appointment unverified")
        specialist = "bilingual" if identifier.startswith("eqv_") else "grammar" if identifier.startswith("con_") or any(str(c.get("fieldPath", "")).startswith("/grammar") for _, c in accepted) else "linguistic"
        if not any(rev.get("role") in (specialist, "lead_editorial") for rev, _ in accepted):
            add("SPECIALIST_REVIEW_MISSING", identifier, "editorialStatus", "Scoped specialist review missing")
        for path in publication_claim_paths(r):
            if not any(ev["claim"].get("fieldPath") == path and rights(ev) for ev in eligible) or not any(c.get("fieldPath") == path and rev.get("role") == "lead_editorial" for rev, c in accepted):
                add("CLAIM_PATH_UNVERIFIED", identifier, path, "Claim-specific approval absent")
        if identifier.startswith("eqv_"):
            for field in ("sourceSenseId", "targetSenseId"):
                if field == "targetSenseId" and r.get("alignmentMode") != "sense_aligned":
                    continue
                t = by_id.get(r.get(field))
                if not t or not t["_id"].startswith("sns_") or t.get("editorialStatus") != "verified":
                    add("BILINGUAL_ENDPOINT_UNVERIFIED", identifier, field, "Endpoint not verified")
            if r.get("translationEvidenceStatus") != "independently_reviewed":
                add("TRANSLATION_UNREVIEWED", identifier, "translationEvidenceStatus", "Translation not reviewed")
        if identifier.startswith("sns_"):
            for n, ex in enumerate(r.get("examples", [])):
                if ex.get("editorialStatus") == "verified" and (not ex.get("evidenceIds") or ex.get("exampleType") in ("synthetic_test", "unverified_candidate")):
                    add("EXAMPLE_EVIDENCE_MISSING", identifier, f"examples/{n}", "Example evidence missing")
                if ex.get("vi") and ex.get("translationEvidenceStatus") != "independently_reviewed":
                    add("EXAMPLE_TRANSLATION_UNREVIEWED", identifier, f"examples/{n}/vi", "Example translation not approved")
    # Lifecycle decisions affecting selected claims require actual owner-appointed
    # LER authority even after structural audits succeed.
    for decision in records:
        if not decision["_id"].startswith("dec_") or decision.get("decisionEffect") != "lifecycle_transition":
            continue
        subjects = decision.get("subjectIds", ())
        if not any(subject in wanted for subject in subjects):
            continue
        audit = decision.get("transitionAudit") or {}
        if "incomingEdges" not in audit:
            add("LIFECYCLE_AUDIT_INCOMPLETE", decision["_id"], "transitionAudit",
                "Historical edge adjudication required")
        if not reviewer_appointed(opts, decision.get("reviewerId"), "lead_editorial",
                                  decision.get("appointmentReference"),
                                  subjects[0] if subjects else None):
            add("LIFECYCLE_AUTHORITY_UNVERIFIED", decision["_id"], "reviewerId",
                "Decision appointment not authenticated")
    # TASK 06 is not private-consumer parity certified. No public Engine
    # caller may turn local quality checks into permission to publish.
    add("QUALITY_PARITY_NOT_VERIFIED", "", "publication", "Private consumer parity and external authority boundary not yet certified")
    count = problems.count
    return {"eligible": count == 0, "issues": problems.result(),
            "issueCount": count, "omittedIssueCount": max(0, count - (count if limit is None else min(limit, count)))}


def validate_dataset(records_or_dataset: Any, schemas: SchemaDirectory, *, version: str = "v2", publication: bool = False, publication_issue_limit: int | None = 1000) -> dict:
    records = records_or_dataset if isinstance(records_or_dataset, list) else records_or_dataset.get("records") if isinstance(records_or_dataset, dict) else None
    if not isinstance(records, list):
        return {"valid": False, "stage": "schema", "issues": [issue("SCHEMA_INVALID", "", "records", "Missing records array")], "publication": {"eligible": False, "issues": []}}
    if publication_issue_limit is not None and (type(publication_issue_limit) is not int or publication_issue_limit < 0):
        return {"valid": False, "stage": "options", "issues": [issue("OPTION_INVALID", "", "publicationIssueLimit", "Must be a non-negative integer or null")], "publication": {"eligible": False, "issues": []}}
    errors = schemas.validate(records)
    if errors:
        return {"valid": False, "stage": "schema", "issues": errors, "publication": {"eligible": False, "issues": []}}
    if version == "v1":
        return validate_v1_graph(records, schemas.vocabularies, publication=publication)
    from .v2_validation import validate_v2_graph
    graph = validate_v2_graph(records, schemas.v1_vocabulary)
    publication_result = evaluate_publication(records, {"v1_vocabulary": schemas.v1_vocabulary}, limit=publication_issue_limit)
    issues = sort_issues(graph + (publication_result["issues"] if publication else []))
    return {"valid": not issues and (not publication or publication_result["eligible"]), "stage": "graph", "issues": issues, "publication": publication_result}
