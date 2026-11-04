"""Deterministic V1/V2 schema generation for caller-owned registry and schema files.

No repository-specific data is embedded. Generation is pure; consumers own paths and
authorization for writing schemas. The CI verifier never writes.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path


V1_ROOT = "https://raw.githubusercontent.com/phatnguyen03022001/ielts-tradition/main/schemas/v1/"
V2_ROOT = V1_ROOT.replace("/v1/", "/v2/")


def _ref(root: str, name: str) -> dict:
    return {"$ref": root + "common.schema.json#/$defs/" + name}


def _s(minimum: int = 1) -> dict:
    return {"type": "string", "minLength": minimum}


def _array(items: dict, minimum: int | None = None) -> dict:
    value = {"type": "array", "items": items, "uniqueItems": True}
    if minimum is not None:
        value["minItems"] = minimum
    return value


def generate_v1(reg: dict) -> dict[str, dict]:
    """Generate the registered V1 schemas in Node-compatible key order."""
    d = lambda name: _ref(V1_ROOT, name)
    defs = {}
    for prefix in reg["identifiers"]["allRegisteredPrefixes"]:
        defs["id_" + prefix] = {"type": "string", "pattern": "^" + prefix + "_" + reg["identifiers"]["uuidV7Pattern"] + "$"}
    for name, values in reg["enums"].items():
        defs[name] = {"type": "string", "enum": values}
    active = reg["identifiers"]["activeV1Prefixes"]
    reserved = reg["identifiers"]["reservedInactiveV1Prefixes"]
    defs["registeredId"] = {"type": "string", "pattern": reg["identifiers"]["globalPattern"]}
    defs["activeV1Id"] = {"type": "string", "pattern": "^(" + "|".join(active) + ")_" + reg["identifiers"]["uuidV7Pattern"] + "$"}
    defs["reservedInactiveV1Id"] = {"type": "string", "pattern": "^(" + "|".join(reserved) + ")_" + reg["identifiers"]["uuidV7Pattern"] + "$"}
    defs["languageTag"] = {"type": "string", "pattern": "^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$"}

    def meta(filename: str, title: str) -> dict:
        return {"$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": V1_ROOT + filename, "title": title, "version": reg["registryVersion"]}

    files = {"common.schema.json": {**meta("common.schema.json", "Shared V1 vocabulary and identifier definitions"), "$defs": defs}}
    common = {
        "schemaVersion": {"const": reg["registryVersion"]},
        "lifecycleStatus": d("lifecycleStatus"), "editorialStatus": d("editorialStatus"),
        "evidenceIds": _array(d("id_evd")), "revision": {"type": "integer", "minimum": 1},
        "createdAt": _s(), "updatedAt": _s(), "retirementMode": d("retirementMode"),
        "retiredByDecisionId": d("id_dec"), "supersededByIds": _array(d("activeV1Id")),
    }
    retirement = [{"if": {"properties": {"lifecycleStatus": {"const": "active"}}, "required": ["lifecycleStatus"]},
                   "then": {"not": {"anyOf": [{"required": [key]} for key in ("retirementMode", "retiredByDecisionId", "supersededByIds")]}},
                   "else": {"required": ["retirementMode", "retiredByDecisionId", "supersededByIds"]}}]
    sense_retirement = []
    for mode in reg["enums"]["retirementMode"]:
        spec = reg["retirementRequirements"][mode]
        shape = {"items": d("id_sns")}
        if "successorMin" in spec:
            shape["minItems"] = spec["successorMin"]
        if "successorMax" in spec:
            shape["maxItems"] = spec["successorMax"]
        sense_retirement.append({
            "if": {"properties": {"lifecycleStatus": {"const": "retired"}, "retirementMode": {"const": mode}},
                   "required": ["lifecycleStatus", "retirementMode"]},
            "then": {"properties": {"supersededByIds": shape}},
        })

    def entity(filename: str, title: str, prefix: str, required: list[str],
               props: dict, all_of: list[dict] | None = None) -> None:
        files[filename] = {**meta(filename, title), "type": "object",
                           "properties": {"_id": d("id_" + prefix), **common, **props},
                           "required": ["_id", "schemaVersion", "lifecycleStatus", "editorialStatus", *required],
                           "additionalProperties": False, "allOf": [*retirement, *(all_of or [])]}

    entity("lexical-entry.schema.json", "LexicalEntry", "lex", ["language", "lemma", "pos", "entryKind"], {
        "language": d("languageTag"), "lemma": _s(), "pos": d("lexicalEntryPos"),
        "entryKind": d("entryKind"), "multiwordKind": d("multiwordKind"),
        "familyIds": _array(d("id_fam")), "homographNumber": {"type": "integer", "minimum": 1},
    }, [{"if": {"properties": {"entryKind": {"const": "word"}}, "required": ["entryKind"]},
         "then": {"not": {"required": ["multiwordKind"]}}}])
    entity("lexical-sense.schema.json", "LexicalSense", "sns", ["entryId", "definition"], {
        "entryId": d("id_lex"), "definition": _s(), "topicIds": _array(d("id_top")),
        "register": d("register"), "usageNote": _s(),
    }, sense_retirement)
    entity("translation-equivalence.schema.json", "TranslationEquivalence", "eqv",
           ["sourceSenseId", "direction", "alignmentMode", "equivalenceType", "evidenceIds"], {
        "sourceSenseId": d("id_sns"), "targetSenseId": d("id_sns"), "glossVi": _s(),
        "direction": d("translationDirection"), "alignmentMode": d("alignmentMode"),
        "equivalenceType": d("equivalenceType"), "context": _s(),
        "register": d("register"), "restrictions": _array(_s()),
    }, [{"if": {"properties": {"alignmentMode": {"const": "gloss_only"}}, "required": ["alignmentMode"]},
         "then": {"required": ["glossVi"], "not": {"required": ["targetSenseId"]}},
         "else": {"required": ["targetSenseId"]}}])
    type_prefix = {"LexicalEntry": "lex", "LexicalSense": "sns"}
    branches = [{"properties": {
        "relationType": {"const": rule["type"]},
        "sourceId": d("id_" + type_prefix[rule["sourceType"]]),
        "targetId": d("id_" + type_prefix[rule["targetType"]])},
        "required": ["relationType", "sourceId", "targetId"]} for rule in reg["relationTypes"]]
    entity("lexical-relation.schema.json", "LexicalRelation", "rel",
           ["relationType", "sourceId", "targetId"], {
        "relationType": {"type": "string"}, "sourceId": _s(), "targetId": _s(),
        "qualifiers": {"type": "object", "properties": {
            "context": _s(), "variety": _s(), "domain": _s(),
            "register": d("register"), "period": _s()}, "additionalProperties": False},
    }, [{"oneOf": branches}])
    entity("word-family.schema.json", "WordFamily", "fam", ["familyBasis"], {
        "familyBasis": d("familyBasis"), "label": _s()})
    entity("topic.schema.json", "Topic", "top", ["label", "parentTopicId"], {
        "label": _s(), "parentTopicId": {"anyOf": [d("id_top"), {"type": "null"}]}})
    entity("construction.schema.json", "Construction", "con", ["senseId", "constructionType", "pattern"], {
        "senseId": d("id_sns"), "constructionType": d("constructionType"), "pattern": _s(),
        "prepositions": _array(_s()), "complements": _array(_s()),
        "separable": {"type": "boolean"}, "constraints": _array(_s())})
    entity("collocation.schema.json", "Collocation", "col", ["anchorSenseId", "pattern"], {
        "anchorSenseId": d("id_sns"), "pattern": _s(),
        "components": _array(_s()), "context": _s()})
    entity("lexical-form.schema.json", "LexicalForm", "frm", ["entryId", "form"], {
        "entryId": d("id_lex"), "form": _s(), "locale": d("languageTag"),
        "ipa": _s(), "stress": _s()})
    entity("morpheme.schema.json", "Morpheme", "mrf", ["form", "morphologicalRole"], {
        "form": _s(), "morphologicalRole": d("morphologicalRole"), "language": d("languageTag")})
    entity("etymology.schema.json", "Etymology", "ety", ["entryId"], {
        "entryId": d("id_lex"), "historicalForm": _s(), "sourceLanguage": d("languageTag"), "notes": _s()})
    entity("evidence.schema.json", "Evidence", "evd", ["sourceId", "assertionIds"], {
        "sourceId": d("id_src"), "assertionIds": _array(d("activeV1Id"), 1),
        "attestation": _s(), "rightsNote": _s()})
    entity("source.schema.json", "Source", "src", ["title"], {
        "title": _s(), "url": {"type": "string", "pattern": "^https?://"},
        "edition": _s(), "license": _s(), "permittedUse": _s()})
    entity("editorial-review.schema.json", "EditorialReview", "rev", ["reviewerId", "subjectIds"], {
        "reviewerId": _s(), "subjectIds": _array(d("activeV1Id"), 1),
        "outcome": d("decisionOutcome"), "issueType": d("issueType"), "rationale": _s()})
    entity("editorial-decision.schema.json", "EditorialDecision", "dec",
           ["subjectIds", "issueType", "outcome", "reviewerId", "rationale"], {
        "subjectIds": _array(d("activeV1Id"), 1), "issueType": d("issueType"),
        "outcome": d("decisionOutcome"), "reviewerId": _s(), "rationale": _s(),
        "reviewedAt": _s(), "action": d("senseDecisionAction"),
        "entryId": d("id_lex"), "previousSenseIds": _array(d("id_sns")),
        "proposedSenseIds": _array(d("id_sns")),
        "supersedesDecisionId": d("id_dec"), "affectedEdges": _array(d("id_rel"))})
    return files


def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties,
            "required": required or [], "additionalProperties": False}


def generate_v2(v1: dict, reg: dict) -> dict:
    """Migrate a generated V1 schema to the exact V2 registered representation."""
    result = deepcopy(v1)

    def change_refs(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, str) and "/schemas/v1/" in child:
                    value[key] = child.replace("/schemas/v1/", "/schemas/v2/")
                elif isinstance(child, (dict, list)):
                    change_refs(child)
        elif isinstance(value, list):
            for child in value:
                change_refs(child)

    change_refs(result)
    result["version"] = reg["registryVersion"]
    if "$defs" in result:
        for name, values in reg["enums"].items():
            result["$defs"][name] = {"type": "string", "enum": values}
        return result
    result["properties"]["schemaVersion"] = {"const": reg["registryVersion"]}
    d = lambda name: _ref(V2_ROOT, name)
    s = _s()
    a = lambda items: _array(items)
    claim = _obj({"subjectId": d("activeV1Id"), "fieldPath": {"type": "string", "pattern": "^/"},
                  "valueDigest": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                  "specificity": d("claimSpecificity"), "description": s},
                 ["subjectId", "fieldPath", "valueDigest", "specificity"])
    reference = _obj({"subjectId": d("activeV1Id"), "fieldPath": s}, ["subjectId", "fieldPath"])
    reviewed_claim = _obj({"subjectId": d("activeV1Id"), "fieldPath": s,
                           "valueDigest": {"type": "string", "pattern": "^[a-f0-9]{64}$"}},
                          ["subjectId", "fieldPath"])
    extra = {
        "lexical-sense": {
            "domain": s, "usageRestrictions": a(_obj({
                "kind": s, "description": s, "evidenceIds": a(d("id_evd"))}, ["kind", "description"])),
            "contrastiveNotes": a(_obj({
                "language": d("languageTag"), "text": s, "evidenceIds": a(d("id_evd"))}, ["language", "text"])),
            "grammar": _obj({"countability": d("countability"), "transitivity": d("transitivity")}),
            "constructionIds": a(d("id_con")),
            "examples": {"type": "array", "items": _obj({
                "english": s, "exampleType": d("exampleType"),
                "editorialStatus": d("editorialStatus"),
                "translationEvidenceStatus": d("translationEvidenceStatus"),
                "vi": s, "evidenceIds": a(d("id_evd")), "register": d("register"),
                "variety": d("languageTag")},
                ["english", "exampleType", "editorialStatus", "translationEvidenceStatus"])}},
        "construction": {"grammar": _obj({
            "countability": d("countability"), "transitivity": d("transitivity"),
            "complementTypes": a(d("complementType")), "requiredPrepositions": a(s),
            "pronounPlacement": d("pronounPlacement"),
            "separability": d("separability"), "register": d("register"),
            "variety": d("languageTag")})},
        "lexical-form": {"pronunciationVariety": d("languageTag"),
                         "pronunciationEvidenceIds": a(d("id_evd"))},
        "translation-equivalence": {"translationEvidenceStatus": d("translationEvidenceStatus")},
        "evidence": {"claim": claim, "location": s, "attestationType": s,
                     "collectionMethod": s, "reviewStatus": d("evidenceReviewStatus"),
                     "independence": d("sourceIndependence"), "limitations": a(s)},
        "source": {"provider": s, "sourceType": d("sourceType"),
                   "sourceIdentifier": s, "materialVersion": s, "retrievedAt": s,
                   "attribution": s, "rights": _obj({
                       "status": d("sourceRightsStatus"), "scopes": a(s),
                       "licenseIdentifier": s, "permissionReference": s,
                       "reviewerId": s, "decisionId": d("id_dec"),
                       "reviewedAt": s, "expiresAt": s, "revokedAt": s}, ["status"])},
        "editorial-review": {"role": d("editorialRole"), "appointmentReference": s,
                             "claims": a(reviewed_claim)},
        "editorial-decision": {"role": d("editorialRole"), "appointmentReference": s,
                               "decisionEffect": d("decisionEffect"), "claims": a(reference),
                               "transitionAudit": _obj({
                                   "beforeRevision": {"type": "integer", "minimum": 1},
                                   "afterRevision": {"type": "integer", "minimum": 2},
                                   "beforeDigest": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                                   "afterDigest": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                                   "successorCreationReferences": a(s),
                                   "incomingEdges": {"type": "array", "items": _obj({
                                       "edgeId": d("activeV1Id"), "resolution": d("edgeResolution"),
                                       "targetId": d("activeV1Id"), "decisionId": d("id_dec")},
                                       ["edgeId", "resolution", "decisionId"])}},
                                   ["beforeRevision", "afterRevision", "beforeDigest",
                                    "afterDigest", "incomingEdges"])}}
    kind = result["$id"].split("/")[-1].removesuffix(".schema.json")
    result["properties"].update(extra.get(kind, {}))
    return result


def serialize_schema(schema: dict) -> str:
    """Match JSON.stringify(schema, null, 2) plus trailing LF."""
    return json.dumps(schema, ensure_ascii=False, indent=2) + "\n"


def generate_schema_pairs(root: str | Path) -> dict[str, dict[str, str]]:
    """Produce the expected V1/V2 schema bytes, without writing caller files."""
    folder = Path(root)
    with (folder / "v1" / "vocabularies.json").open(encoding="utf-8") as file:
        v1_reg = json.load(file)
    with (folder / "v2" / "vocabularies.json").open(encoding="utf-8") as file:
        v2_reg = json.load(file)
    v1 = generate_v1(v1_reg)
    return {"v1": {name: serialize_schema(value) for name, value in v1.items()},
            "v2": {name: serialize_schema(generate_v2(value, v2_reg))
                   for name, value in v1.items()}}


def check_schemas(root: str | Path) -> None:
    """Refuse drift, missing/extra schemas, malformed JSON and non-file paths."""
    base = Path(root)
    generated = generate_schema_pairs(base)
    for version, expected in generated.items():
        directory = base / version
        actual = sorted(p.name for p in directory.glob("*.schema.json"))
        if actual != sorted(expected):
            raise ValueError(version + " schema inventory drift")
        for name, content in expected.items():
            path = directory / name
            if path.is_symlink() or not path.is_file() or path.read_text(encoding="utf-8") != content:
                raise ValueError(version + " schema drift: " + name)


def write_schemas(root: str | Path, version: str) -> None:
    """Explicit, version-scoped schema generation with safe per-file replacement.

    Not called by check_schemas or by validation. The private consumer is the
    only owner of real registries and generated schema files.
    """
    import os
    import secrets
    import stat

    if version not in ("v1", "v2"):
        raise ValueError("Unsupported schema version")
    base = Path(root)
    directory = base / version
    if base.is_symlink() or directory.is_symlink() or not directory.is_dir():
        raise ValueError("Unsafe schema directory")
    expected = generate_schema_pairs(base)[version]
    present = sorted(p.name for p in directory.glob("*.schema.json"))
    if any(name not in expected for name in present):
        raise ValueError(version + " schema inventory drift")
    for name, serialized in expected.items():
        target = directory / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise ValueError("Unsafe generated schema target")
        temporary = directory / ("." + name + "." + secrets.token_hex(8) + ".tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
            if target.is_symlink():
                raise ValueError("Unsafe generated schema target")
            os.replace(temporary, target)
        finally:
            if temporary.exists() and not temporary.is_symlink():
                temporary.unlink()
    directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
