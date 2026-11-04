"""V2 claim-specific authority and rights regression tests using synthetic fixtures."""
from copy import deepcopy
import unittest

from english_action.content.contracts import digest, evaluate_publication, publication_claim_paths, value_at


def _id(prefix, index):
    return f"{prefix}_01940000-0000-7000-8000-{index:012x}"


def _verified(prefix, index, extra):
    return {"_id": _id(prefix, index), "schemaVersion": "2.0.0",
            "lifecycleStatus": "active", "editorialStatus": "verified", **extra}


LEX = _verified("lex", 3001, {"language":"en", "lemma":"synthetic", "pos":"NOUN", "entryKind":"word"})
SNS = _verified("sns", 3001, {"entryId":LEX["_id"], "definition":"Test-only meaning",
                              "usageNote":"Do not use as authentic guidance"})
CON = _verified("con", 3001, {"senseId":SNS["_id"], "constructionType":"verb_pattern",
                              "pattern":"synthetic pattern", "constraints":["test-only constraint"],
                              "separable":False})
FRM = _verified("frm", 3001, {"entryId":LEX["_id"], "form":"synthetics", "pronunciationVariety":"en-GB"})
EQV = _verified("eqv", 3001, {"sourceSenseId":SNS["_id"], "direction":"en_to_vi",
                              "alignmentMode":"gloss_only",
                              "translationEvidenceStatus":"independently_reviewed",
                              "glossVi":"nghĩa thử nghiệm", "equivalenceType":"approximate",
                              "restrictions":["test-only restriction"]})
CASES = [
    (LEX, [], "/entryKind"), (SNS, [LEX], "/usageNote"),
    (CON, [LEX, SNS], "/constraints/0"), (FRM, [LEX], "/pronunciationVariety"),
    (EQV, [LEX, SNS], "/restrictions/0"),
]


def _trusted(subject, dependencies):
    source = _verified("src", 3001, {"title":"Synthetic provenance fixture",
        "rights":{"status":"redistributable","scopes":["dictionary_publication"],
                  "reviewerId":"rights","permissionReference":"test-rights"}})
    records = [*map(deepcopy, dependencies), deepcopy(subject), source]
    options = {"subject_ids":[subject["_id"]], "owner_id":"test-owner",
        "effective_at":"2026-10-30T00:00:00Z",
        "appointments":[{"appointmentKey":"test-rights", "reviewerId":"rights",
            "appointedByOwnerId":"test-owner", "role":"rights",
            "scope":{"subjectIds":[source["_id"]]},
            "effectiveFrom":"2026-01-01T00:00:00Z", "ownerAttestationRef":"test-only"}],
        "verify_owner_attestation":lambda **_:True,
        "verify_evidence":lambda **_:True,
        "verify_rights":lambda **_:True}
    sequence = 4000
    for record in list(records):
        if record["_id"].startswith("src_"):
            continue
        paths = publication_claim_paths(record)
        evidence = []
        for path in paths:
            sequence += 1
            evidence.append(_verified("evd", sequence, {
                "sourceId":source["_id"], "assertionIds":[record["_id"]],
                "claim":{"subjectId":record["_id"],"fieldPath":path,
                         "valueDigest":digest(value_at(record,path)),
                         "specificity":"exact_value"},
                "independence":"independent_reviewed","reviewStatus":"accepted"}))
        record["evidenceIds"] = [r["_id"] for r in evidence]
        records.extend(evidence)
        sequence += 1
        records.append(_verified("rev",sequence,{
            "reviewerId":"test-ler", "role":"lead_editorial",
            "appointmentReference":"test-review", "subjectIds":[record["_id"]],
            "outcome":"accepted",
            "claims":[{"subjectId":record["_id"], "fieldPath":path,
                       "valueDigest":digest(value_at(record,path))} for path in paths]}))
        options["appointments"].append({
            "appointmentKey":"test-review","reviewerId":"test-ler",
            "appointedByOwnerId":"test-owner","role":"lead_editorial",
            "scope":{"subjectIds":[record["_id"]],"fieldPaths":paths},
            "effectiveFrom":"2026-01-01T00:00:00Z","ownerAttestationRef":"test-only"})
    return records, options


class ClaimAuthorityTests(unittest.TestCase):
    def test_exact_claim_evidence_review_and_rights_for_five_entity_types(self):
        for subject,dependencies,path in CASES:
            with self.subTest(entity=subject["_id"],path=path):
                records, options = _trusted(subject,dependencies)
                good = evaluate_publication(records,options)
                self.assertFalse(good["eligible"])  # permanent independent migration lock
                self.assertEqual({i["code"] for i in good["issues"]},
                                 {"QUALITY_PARITY_NOT_VERIFIED"},good["issues"])
                proof = next(r for r in records if r["_id"].startswith("evd_") and
                             r["claim"]["subjectId"] == subject["_id"] and
                             r["claim"]["fieldPath"] == path)
                absent = evaluate_publication([r for r in records if r is not proof],options)
                self.assertFalse(absent["eligible"])
                self.assertTrue(any(i["code"]=="CLAIM_PATH_UNVERIFIED" and
                                    i["entityId"]==subject["_id"] and i["path"]==path
                                    for i in absent["issues"]),absent["issues"])
                no_review = deepcopy(records)
                review = next(r for r in no_review if r["_id"].startswith("rev_") and
                              subject["_id"] in r.get("subjectIds",[]))
                review["claims"] = [x for x in review["claims"] if x["fieldPath"]!=path]
                unreviewed = evaluate_publication(no_review,options)
                self.assertTrue(any(i["code"]=="CLAIM_PATH_UNVERIFIED" and
                                    i["entityId"]==subject["_id"] and i["path"]==path
                                    for i in unreviewed["issues"]),unreviewed["issues"])
                denied_rights = evaluate_publication(records,options|{"verify_rights":lambda **_:False})
                self.assertFalse(denied_rights["eligible"])
                self.assertTrue(any(i["code"]=="CLAIM_PATH_UNVERIFIED" and
                                    i["entityId"]==subject["_id"] and i["path"]==path
                                    for i in denied_rights["issues"]),denied_rights["issues"])

    def test_definition_review_does_not_certify_grammar_countability(self):
        lex = _verified("lex",1301,{"language":"en","lemma":"synthetic","pos":"NOUN",
                                    "entryKind":"word","evidenceIds":[_id("evd",1301)]})
        sense = _verified("sns",1301,{"entryId":lex["_id"],"definition":"synthetic definition",
                                         "grammar":{"countability":"uncountable"},
                                         "evidenceIds":[_id("evd",1302)]})
        source = _verified("src",1301,{"title":"Synthetic no citation",
            "rights":{"status":"redistributable","scopes":["dictionary_publication"],
                      "reviewerId":"fake","permissionReference":"fake"}})
        def evidence(index,record,path):
            return _verified("evd",index,{"sourceId":source["_id"],"assertionIds":[record["_id"]],
                "claim":{"subjectId":record["_id"],"fieldPath":path,
                         "valueDigest":digest(value_at(record,path)),"specificity":"exact_value"},
                "independence":"independent_reviewed","reviewStatus":"accepted"})
        def review(index,record,path):
            return _verified("rev",index,{"reviewerId":"fake","role":"lead_editorial",
                "appointmentReference":"fake","subjectIds":[record["_id"]],
                "outcome":"accepted","claims":[{"subjectId":record["_id"],"fieldPath":path,
                    "valueDigest":digest(value_at(record,path))}]})
        rows=[lex,sense,source,evidence(1301,lex,"/lemma"),evidence(1302,sense,"/definition"),
              review(1301,lex,"/lemma"),review(1302,sense,"/definition")]
        evaluated=evaluate_publication(rows,{"verify_rights":lambda **_:True})
        self.assertFalse(evaluated["eligible"])
        self.assertTrue(any(i["code"]=="CLAIM_PATH_UNVERIFIED" and
                            i["entityId"]==sense["_id"] and i["path"]=="/grammar/countability"
                            for i in evaluated["issues"]),evaluated["issues"])
