"""Offline, opt-in validation for consumer-owned DATA-03/04 content contracts.

This is a contract/consistency checker, not an author, scorer, or publisher.
Caller-supplied canonical records and authority proofs are explicit inputs.
"""
from __future__ import annotations

from collections import defaultdict, deque
import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

SCHEMAS = {
    "core": ("core", "core-v1.schema.json", "urn:english-content:core:v1"),
    "assessment": ("assessment", "assessment-v1.schema.json", "urn:english-content:assessment:v1"),
    "exam": ("exam", "exam-v1.schema.json", "urn:english-content:exam:v1"),
    "media": ("media", "media-v1.schema.json", "urn:english-content:media:v1"),
}
KINDS = {
    "stimulus": "core", "foundation_item": "core", "exam_item": "core",
    "answer_spec": "assessment", "assessment_metadata": "assessment",
    "exam_version": "exam", "task_profile": "exam", "blueprint": "exam",
    "exam_form": "exam", "audio_source": "media", "image_source": "media",
}
_LEXICAL_PREFIXES = {"lex", "sns", "frm", "fam", "mrf", "ety", "top",
                     "mwe", "con", "col", "eqv", "rel", "evd", "src", "rev", "dec"}
_MAX_RECORDS = 10000
_MAX_EDGES = 100000


def _resolve_pointer(schema: dict, pointer: str) -> None:
    """Reject dangling local JSON pointers before validation (never retrieve remote)."""
    if not pointer:
        return
    if not pointer.startswith("/"):
        raise ValueError("Only JSON-pointer schema fragments are supported")
    node: Any = schema
    for component in pointer[1:].split("/"):
        token = component.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and token in node:
            node = node[token]
        elif isinstance(node, list) and token.isdigit() and int(token) < len(node):
            node = node[int(token)]
        else:
            raise ValueError("Unresolved local schema reference")


class WholeAppSchemas:
    """Immutable registry of four approved local contracts, with no retrieval callback."""

    def __init__(self, root: str | Path) -> None:
        root_path = Path(root).resolve(strict=True)
        definitions: dict[str, dict] = {}
        for area, (folder, filename, identifier) in SCHEMAS.items():
            path = root_path / folder / filename
            if path.is_symlink() or not path.resolve(strict=True).is_relative_to(root_path):
                raise ValueError("Schema path escapes supplied directory")
            schema = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(schema, dict) or schema.get("$id") != identifier:
                raise ValueError("Missing, conflicting or unsupported schema identity")
            Draft202012Validator.check_schema(schema)
            definitions[area] = schema

        ids = {schema["$id"]: schema for schema in definitions.values()}

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    if key in ("$ref", "$dynamicRef"):
                        if not isinstance(value, str) or "#" not in value:
                            raise ValueError("Unsupported offline schema reference")
                        name, fragment = value.split("#", 1)
                        target = ids.get(name) if name else current
                        if target is None:
                            raise ValueError("Unknown or remote schema reference")
                        _resolve_pointer(target, fragment)
                    else:
                        walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        for current in definitions.values():
            walk(current)
        registry = Registry().with_resources(
            [(s["$id"], Resource.from_contents(s)) for s in definitions.values()])
        self.validators = {
            area: Draft202012Validator(schema, registry=registry)
            for area, schema in definitions.items()
        }

    def errors(self, record: dict) -> list:
        validator = self.validators[KINDS[record["kind"]]]
        return sorted(validator.iter_errors(record),
                      key=lambda e: ("/".join(map(str, e.absolute_path)), e.validator or ""))


def _key(record: dict) -> tuple | None:
    kind = record.get("kind")
    if kind in ("stimulus", "foundation_item", "exam_item", "answer_spec"):
        return (kind, record.get("id"), record.get("revision"))
    if kind == "blueprint":
        return (kind, record.get("compositionKey"), record.get("revision"))
    if kind == "exam_form":
        return (kind, record.get("formKey"), record.get("revision"))
    if kind == "exam_version":
        return (kind, *_version(record))
    if kind == "task_profile":
        return (kind, *_version(record.get("examVersion", {})),
                _product_key(record.get("profileRef", {})))
    identifier = record.get("_id")
    if isinstance(identifier, str) and identifier.partition("_")[0] in _LEXICAL_PREFIXES:
        return (identifier.partition("_")[0], identifier, record.get("revision"))
    return None


def _response_matches(actual: dict, declared: dict) -> bool:
    return all(actual.get(key) == value for key, value in declared.items())


def _product_key(ref: dict) -> tuple:
    return tuple(ref.get(k) for k in ("owner", "sourcePath", "sourceCommit", "locator"))


def _version(record: dict) -> tuple:
    return (record.get("family"), record.get("formatVariant"), record.get("versionTag"))


def _label(record: dict) -> str:
    return str(record.get("id") or record.get("compositionKey") or
               record.get("formKey") or record.get("_id") or record.get("kind", ""))


def _iter_product_refs(node: Any):
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            if current.get("owner") == "english" and all(
                    k in current for k in ("sourcePath", "sourceCommit", "locator")):
                yield _product_key(current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def _diagnostic(status: str, code: str, entity: str, path: str) -> dict:
    prefix = code.split("_", 1)[0]
    stages = {"SCHEMA": "structural", "REFERENCE": "referential",
              "CANDIDATE": "referential", "IDENTITY": "identity",
              "DEPENDENCY": "dependency", "SUPERSESSION": "lifecycle",
              "ANSWER": "assessment", "ASSESSMENT": "assessment",
              "ACOUSTIC": "assessment", "SPEECH": "assessment",
              "EXAM": "exam", "BLUEPRINT": "exam", "FORM": "exam",
              "MEDIA": "media", "EVIDENCE": "eligibility",
              "PRODUCT": "authority", "POLICY": "authority"}
    required = {
        "REFERENCE_MISSING": "existing canonical target at exact revision",
        "REFERENCE_REVISION": "exact canonical revision",
        "EVIDENCE_UNVERIFIED": "independently verified content and rights evidence",
        "PRODUCT_AUTHORITY_UNVERIFIED": "verified product source commit and locator",
        "EXAM_VERSION_UNRESOLVED": "authoritative ExamVersion",
        "MEDIA_NOT_GENERATED": "authorized generated asset and output QA",
        "POLICY_UNVERIFIED": "authoritative applicable policy",
    }.get(code, "")
    return {"status": status, "stage": stages.get(prefix, "semantic"),
            "code": code, "entity": entity, "path": path, "required": required,
            "message": code.replace("_", " ").lower()[:160]}


class _Validation:
    def __init__(self, records: list[dict], external: list[dict], *,
                 candidate_records: tuple[dict, ...], batch_id: str | None,
                 trusted_product_refs: set[tuple], trusted_evidence: set[str],
                 limit: int, require_eligibility: bool) -> None:
        self.records = records
        self.external = external
        self.index: dict[tuple, dict] = {}
        self.by_id: dict[str, set[tuple]] = defaultdict(set)
        self.parents: dict[tuple, set[tuple]] = defaultdict(set)
        self.cycle_edges: dict[tuple, set[tuple]] = defaultdict(set)
        self.issues: list[dict] = []
        self.dropped = 0
        self.affected: set[str] = set()
        self.affected_keys: set[tuple] = set()
        self.limit = limit
        self.edges = 0
        self.candidates = {c.get("candidateId"): c for c in candidate_records
                           if isinstance(c, dict) and isinstance(c.get("candidateId"), str)}
        self.batch_id = batch_id
        self.product_refs = trusted_product_refs
        self.evidence = trusted_evidence
        self.eligibility = require_eligibility

    def add(self, code: str, record: dict, path: str, *, blocked: bool = False) -> None:
        if len(self.issues) >= self.limit:
            self.dropped += 1
            return
        self.issues.append(_diagnostic("BLOCKED" if blocked else "FAIL",
                                        code, _label(record), path))
        if not blocked:
            self.affected.add(_label(record))
            key = _key(record)
            if key is not None:
                self.affected_keys.add(key)

    def make_index(self) -> None:
        for i, record in enumerate([*self.external, *self.records]):
            key = _key(record)
            if key is None:
                continue
            if key in self.index:
                self.add("IDENTITY_CONFLICT", record, f"records/{i}")
            else:
                self.index[key] = record
                if len(key) > 1 and isinstance(key[1], str):
                    self.by_id[key[1]].add(key)

    def link(self, owner: dict, reference: dict, path: str, *, record_edge: bool = True,
             cycle_edge: bool = False) -> dict | None:
        if reference.get("scope") == "candidate":
            candidate = self.candidates.get(reference.get("id"))
            if self.batch_id is None or candidate is None or candidate.get("batchId") != self.batch_id:
                self.add("CANDIDATE_SCOPE_INVALID", owner, path)
                return None
            if candidate.get("targetKind") != reference.get("targetKind"):
                self.add("REFERENCE_TYPE", owner, path)
            return candidate
        target_kind = reference.get("targetKind")
        key = (target_kind, reference.get("id"), reference.get("revision"))
        target = self.index.get(key)
        if target is None:
            possible = self.by_id.get(reference.get("id"), set())
            if any(k[0] == target_kind for k in possible):
                self.add("REFERENCE_REVISION", owner, path)
            elif possible:
                self.add("REFERENCE_TYPE", owner, path)
            else:
                self.add("REFERENCE_MISSING", owner, path)
            return None
        if target.get("lifecycle", {}).get("state") in ("withdrawn", "superseded") or (
                target.get("lifecycleStatus") in ("retired", "withdrawn")):
            self.add("DEPENDENCY_WITHDRAWN", owner, path)
        if record_edge:
            parent = _key(owner)
            if parent is not None:
                self.parents[key].add(parent)
                if cycle_edge:
                    self.cycle_edges[parent].add(key)
                self.edges += 1
        return target

    def visit_record(self, record: dict) -> None:
        kind = record["kind"]
        for n, reference in enumerate(record.get("dependencies", ())):
            if "scope" in reference:
                self.link(record, reference, f"dependencies/{n}", cycle_edge=True)
        if kind in ("foundation_item", "exam_item"):
            self.link(record, {"scope": "canonical", "targetKind": "answer_spec",
                               **record["answerSpecRef"]}, "answerSpecRef")
            for n, source in enumerate(record.get("sources", ())):
                if source["binding"] == "shared":
                    self.link(record, source["ref"], f"sources/{n}/ref", cycle_edge=True)
            for n, objective in enumerate(record.get("objectiveRefs", ())):
                if objective["competency"] != record.get("primaryCompetency", objective["competency"]):
                    # Objectives may legitimately map several competencies; no invented equality restriction.
                    pass
        elif kind == "answer_spec":
            item = self.link(record, record["itemRef"], "itemRef", record_edge=False)
            if item is not None:
                if item.get("answerSpecRef") != {"id": record["id"], "revision": record["revision"]}:
                    self.add("ANSWER_ITEM_BINDING", record, "itemRef")
                if not _response_matches(item.get("response", {}), record.get("response", {})):
                    self.add("ANSWER_RESPONSE_MISMATCH", record, "response")
                if item.get("sources") != record.get("sourceBindings"):
                    self.add("ANSWER_SOURCE_MISMATCH", record, "sourceBindings")
            for n, source in enumerate(record.get("sourceBindings", ())):
                if source["binding"] == "shared":
                    self.link(record, source["ref"], f"sourceBindings/{n}")
        elif kind == "assessment_metadata":
            self.link(record, record["itemRef"], "itemRef")
            answer = self.link(record, {"scope": "canonical", "targetKind": "answer_spec",
                                        **record["answerSpecRef"]}, "answerSpecRef")
            if answer is not None:
                if answer.get("itemRef") != record["itemRef"]:
                    self.add("ASSESSMENT_ITEM_BINDING", record, "itemRef")
                if not _response_matches(answer.get("response", {}), record.get("response", {})):
                    self.add("ASSESSMENT_RESPONSE_MISMATCH", record, "response")
        elif kind in ("audio_source", "image_source"):
            binding = record["sourceBinding"]
            source = (self.link(record, binding["ref"], "sourceBinding/ref", record_edge=False)
                      if binding["binding"] == "shared" else binding)
            content = (source.get("content") if source else None)
            if content and kind == "audio_source" and content.get("kind") == "transcript":
                expected = [(t["speakerKey"], t["text"]) for t in content["turns"]]
                actual = [(t["speakerKey"], t["text"]) for t in record["turns"]]
                if expected != actual:
                    self.add("MEDIA_SOURCE_MISMATCH", record, "sourceBinding")
            if content and kind == "image_source" and content.get("kind") in ("chart_facts", "table", "scene"):
                if record.get("sourceFacts") != content:
                    self.add("MEDIA_SOURCE_MISMATCH", record, "sourceBinding")
        elif kind == "exam_form":
            self.check_form(record)

    def check_form(self, record: dict) -> None:
        ref = record["blueprintRef"]
        blueprint = self.index.get(("blueprint", ref["compositionKey"], ref["revision"]))
        if blueprint is None:
            if any(k[0] == "blueprint" and k[1] == ref["compositionKey"] for k in self.index):
                self.add("BLUEPRINT_REVISION", record, "blueprintRef")
            else:
                self.add("BLUEPRINT_MISSING", record, "blueprintRef")
            return
        if _version(blueprint.get("examVersion", {})) != _version(record["examVersion"]):
            self.add("EXAM_VERSION_MISMATCH", record, "examVersion")
        for index, section in enumerate(record.get("sections", ())):
            for group in section.get("groups", ()):
                for placement in group.get("items", ()):
                    item = self.link(record, placement["itemRef"],
                                     f"sections/{index}/groups/items/itemRef")
                    answer = self.link(record, {"scope": "canonical", "targetKind": "answer_spec",
                                                **placement["answerSpecRef"]},
                                       f"sections/{index}/groups/items/answerSpecRef")
                    if item and item.get("answerSpecRef") != placement["answerSpecRef"]:
                        self.add("FORM_ANSWER_BINDING", record, f"sections/{index}")
                    if item and item.get("examFamily") != record["examVersion"]["family"]:
                        self.add("FORM_ITEM_FAMILY", record, f"sections/{index}")
                    if item and answer and answer.get("itemRef") != placement["itemRef"]:
                        self.add("FORM_ANSWER_BINDING", record, f"sections/{index}")
                    for ref in placement.get("sharedStimulusRefs", ()):
                        self.link(record, ref, f"sections/{index}/sharedStimulusRefs")
                for ref in group.get("sharedStimulusRefs", ()):
                    self.link(record, ref, f"sections/{index}/groups/sharedStimulusRefs")
        for n, dependency in enumerate(record.get("mediaDependencies", ())):
            self.link(record, dependency["sourceRef"], f"mediaDependencies/{n}/sourceRef")
        if blueprint["completeness"] == "evidence_backed":
            b_sections = {s["section"]: s for s in blueprint.get("sections", ())}
            section_names = {s["section"] for s in record.get("sections", ())}
            if section_names != set(b_sections):
                self.add("FORM_SECTION_MISMATCH", record, "sections")
            for section in record.get("sections", ()):
                count = sum(len(g["items"]) for g in section["groups"])
                requirement = b_sections.get(section["section"], {}).get("count")
                if requirement and not requirement["minimum"] <= count <= requirement["maximum"]:
                    self.add("FORM_COUNT_MISMATCH", record, "sections")

    def check_versions(self) -> None:
        versions = {_version(r): r for r in self.index.values() if r.get("kind") == "exam_version"}
        for record in self.records:
            if record["kind"] not in ("task_profile", "blueprint", "exam_form"):
                continue
            version = _version(record["examVersion"])
            if version not in versions:
                self.add("EXAM_VERSION_UNRESOLVED", record, "examVersion", blocked=True)
            elif _product_key(versions[version]["formatRef"]) != _product_key(record["examVersion"]["formatRef"]):
                # Format refs from a profile must be independently authorized even on same version.
                self.add("EXAM_FORMAT_BINDING", record, "examVersion/formatRef")

    def check_cycles(self) -> None:
        # Only explicit core dependencies/sources; answerSpec↔item bindings are not cycles.
        visited: set[tuple] = set()
        active: set[tuple] = set()
        adj = self.cycle_edges
        for root in list(adj):
            if root in visited:
                continue
            stack = [(root, iter(sorted(adj[root], key=str)))]
            active.add(root)
            while stack:
                node, iterator = stack[-1]
                nxt = next(iterator, None)
                if nxt is None:
                    visited.add(node)
                    active.discard(node)
                    stack.pop()
                elif nxt in active:
                    self.add("DEPENDENCY_CYCLE", self.index[node], "dependencies")
                elif nxt not in visited:
                    active.add(nxt)
                    stack.append((nxt, iter(sorted(adj.get(nxt, ()), key=str))))

    def propagate(self) -> None:
        # Restrict invalidation to dependents of invalid graph nodes (reverse edges).
        queue = deque(self.affected_keys)
        seen = set(self.affected_keys)
        while queue:
            key = queue.popleft()
            for dependent in self.parents.get(key, ()):
                if dependent not in seen:
                    seen.add(dependent)
                    queue.append(dependent)
                    self.affected.add(_label(self.index[dependent]))
                    self.affected_keys.add(dependent)

    def check_authority(self) -> None:
        if not self.eligibility:
            return
        for record in self.records:
            if record["kind"] in ("audio_source", "image_source"):
                self.add("MEDIA_NOT_GENERATED", record, "assetState", blocked=True)
            if record["kind"] in ("task_profile",) and record.get("policyStatus") != "referenced":
                self.add("POLICY_UNVERIFIED", record, "policyStatus", blocked=True)
            for ref in set(_iter_product_refs(record)):
                if ref not in self.product_refs:
                    self.add("PRODUCT_AUTHORITY_UNVERIFIED", record, "productRef", blocked=True)
            provenance = record.get("provenance", {})
            if not provenance or not provenance.get("evidenceRefs") or not provenance.get("rightsEvidenceRefs"):
                self.add("EVIDENCE_UNVERIFIED", record, "provenance", blocked=True)
            elif not set(provenance["evidenceRefs"] + provenance["rightsEvidenceRefs"]).issubset(self.evidence):
                self.add("EVIDENCE_UNVERIFIED", record, "provenance", blocked=True)


def validate_whole_app(records: list[dict], *, schemas: WholeAppSchemas,
                       canonical_records: tuple[dict, ...] = (),
                       candidate_records: tuple[dict, ...] = (),
                       batch_id: str | None = None,
                       trusted_product_refs: frozenset[tuple] = frozenset(),
                       trusted_evidence: frozenset[str] = frozenset(),
                       require_eligibility: bool = False, limit: int = 256) -> dict:
    """Validate a bounded batch; PASS means deterministic checks only, never publication.

    canonical_records must be caller-verified exact revisions. Trusted evidence
    inputs must originate from an independent verifier; declarations are not proof.
    """
    if type(limit) is not int or limit < 1 or limit > 4096:
        return {"status": "FAIL", "diagnostics": [
            _diagnostic("FAIL", "INPUT_LIMIT", "", "limit")],
            "affected": [], "publicationEligible": False, "truncated": False}
    if (not isinstance(records, list) or len(records) > _MAX_RECORDS or
            not isinstance(canonical_records, (tuple, list)) or
            len(records) + len(canonical_records) > _MAX_RECORDS):
        return {"status": "FAIL", "diagnostics": [
            _diagnostic("FAIL", "INPUT_LIMIT", "", "records")],
            "affected": [], "publicationEligible": False, "truncated": False}
    if any(not isinstance(r, dict) or _key(r) is None or
           r.get("revision") is None and r.get("kind") not in ("exam_version", "task_profile")
           for r in canonical_records):
        return {"status": "FAIL", "diagnostics": [
            _diagnostic("FAIL", "CANONICAL_INVALID", "", "canonical_records")],
            "affected": [], "publicationEligible": False, "truncated": False}
    diagnostics = []
    for index, record in enumerate(records):
        if not isinstance(record, dict) or record.get("kind") not in KINDS:
            diagnostics.append(_diagnostic("FAIL", "SCHEMA_INVALID", "",
                                           f"records/{index}/kind"))
            continue
        try:
            errors = schemas.errors(record)
        except Exception:
            errors = [None]
        for error in errors[:4]:
            path = "/".join(map(str, error.absolute_path)) if error is not None else ""
            diagnostics.append(_diagnostic("FAIL", "SCHEMA_INVALID",
                                           _label(record), f"records/{index}/{path}"))
        if len(diagnostics) >= limit:
            break
    if diagnostics:
        return {"status": "FAIL", "diagnostics": diagnostics[:limit],
                "affected": sorted({d["entity"] for d in diagnostics if d["entity"]}),
                "publicationEligible": False, "truncated": len(diagnostics) > limit}
    state = _Validation(records, list(canonical_records),
                        candidate_records=tuple(candidate_records), batch_id=batch_id,
                        trusted_product_refs=set(trusted_product_refs),
                        trusted_evidence=set(trusted_evidence),
                        limit=limit, require_eligibility=require_eligibility)
    state.make_index()
    for record in records:
        state.visit_record(record)
        from .whole_app_semantics import validate_semantics
        validate_semantics(record, state)
    if state.edges > _MAX_EDGES:
        state.add("INPUT_LIMIT", {}, "dependencies")
    else:
        state.check_versions()
        state.check_cycles()
    state.propagate()
    state.check_authority()
    diagnostics = sorted(state.issues, key=lambda d: (d["status"] != "FAIL",
                                 d["code"], d["entity"], d["path"]))[:limit]
    status = "FAIL" if any(d["status"] == "FAIL" for d in diagnostics) else (
        "BLOCKED" if diagnostics else "PASS")
    return {"status": status, "diagnostics": diagnostics,
            "affected": sorted(state.affected), "publicationEligible": False,
            "truncated": state.dropped > 0}
