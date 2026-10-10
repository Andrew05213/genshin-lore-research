"""Claim-specific qualification of snapshot-local story evidence.

This derived decision never resolves a Quest→Talk identity. A legacy
``search_complete=0`` projection cannot certify an invariant; an independent
bounded physical candidate census may do so for a specific exact claim.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence


CONTRACT = "research-story-scope-v1"
STATUSES = frozenset({
    "primary_exact", "primary_candidate_set_invariant", "ambiguous",
    "terminal_unresolved", "background",
})


def qualify_claim(
    *, claim: str, snapshot_scope: str, release_scope_ref: str | None,
    source_quest: str | None,
    candidate_proofs: Sequence[Mapping], declared_candidate_count: int,
    search_complete: bool, candidate_set_verified: bool,
    release_confirmed: bool, role: str = "primary",
) -> dict:
    """Qualify one exact claim, never a whole Talk or an inferred identity.

    Each candidate proof must contain an exact ``evidence_ref``, a
    claim-specific ``supports_claim`` boolean, and a documented basis. A
    caller must independently verify candidate-set closure and exact-recheck
    every ref; this layer cannot infer support from similarity scores, IDs,
    paths, or one selected candidate.
    """
    if not isinstance(claim, str) or not claim.strip():
        raise ValueError("an explicit claim is required")
    if role not in {"primary", "background"}:
        raise ValueError("unknown story role")
    if not isinstance(snapshot_scope, str) or not snapshot_scope:
        raise ValueError("exact snapshot scope required")
    if declared_candidate_count < 0:
        raise ValueError("negative candidate count")
    refs = [proof.get("evidence_ref") for proof in candidate_proofs]
    if any(not isinstance(ref, str) or not ref for ref in refs) or len(refs) != len(set(refs)):
        raise ValueError("candidate refs must be distinct exact evidence refs")
    if any(type(proof.get("supports_claim")) is not bool for proof in candidate_proofs):
        raise ValueError("every known candidate needs a claim-specific support verdict")
    if any(proof.get("source_rechecked") is not True or
           not isinstance(proof.get("support_basis"), str) or
           not proof["support_basis"].strip() for proof in candidate_proofs):
        raise ValueError("every candidate needs exact recheck and explicit support basis")
    if any(proof.get("snapshot") != snapshot_scope for proof in candidate_proofs):
        raise ValueError("cross-snapshot candidate proof rejected")
    complete = bool(search_complete and candidate_set_verified and
                    len(refs) == declared_candidate_count and refs)
    all_support = all(proof["supports_claim"] for proof in candidate_proofs) if complete else None
    if role == "background":
        status = "background"
    elif not release_confirmed or not release_scope_ref or not source_quest or not complete:
        status = "terminal_unresolved"
    elif not all_support:
        status = "ambiguous"
    elif len(refs) == 1:
        status = "primary_exact"
    else:
        status = "primary_candidate_set_invariant"
    return {
        "contract": CONTRACT,
        "exact_claim": claim,
        "snapshot_scope": snapshot_scope,
        "role": role,
        "release_scope_ref": release_scope_ref,
        "source_quest": source_quest,
        "candidate_refs": refs,
        "candidate_count": declared_candidate_count,
        "known_candidate_count": len(refs),
        "candidate_search_complete": bool(search_complete),
        "candidate_search_basis": "independent bounded physical native-ID census; not semantic relation closure" if search_complete else "incomplete",
        "candidate_set_verified": bool(candidate_set_verified),
        "release_scope_confirmed": bool(release_confirmed),
        "all_candidates_support": all_support,
        "qualification_status": status,
        "proof_basis": "claim-specific exact evidence recheck; no identity resolution",
        "unresolved_note": (
            "candidate search is not complete; known candidates cannot establish an invariant"
            if not complete and role == "primary" else None
        ),
        "identity_resolution": "none",
        "relation_closure": False,
    }


def audit_answer_claims(claims: Sequence[Mapping], *, coverage_complete: bool) -> dict:
    """Separate correctness of emitted claims from coverage of the story."""
    allowed = {"primary", "background", "interpretation"}
    support = {"supported", "partially_supported", "unsupported"}
    audited = []
    for item in claims:
        if item.get("role") not in allowed or item.get("support_status") not in support:
            raise ValueError("invalid claim role/support status")
        if not isinstance(item.get("claim"), str) or not item["claim"].strip():
            raise ValueError("empty answer claim")
        if item["role"] == "primary" and item.get("story_qualification") not in {
            "primary_exact", "primary_candidate_set_invariant"
        }:
            raise ValueError("unqualified primary claim")
        if not item.get("evidence_refs"):
            raise ValueError("answer claim lacks exact evidence references")
        if item["role"] == "primary" and not item.get("release_attribution"):
            raise ValueError("primary claim lacks release attribution")
        if item["role"] == "background" and item.get("story_qualification") != "background":
            raise ValueError("background claim lacks background qualification")
        audited.append(dict(item))
    unsupported = sum(x["support_status"] == "unsupported" for x in audited)
    partial = sum(x["support_status"] == "partially_supported" for x in audited)
    return {
        "contract": "research-claim-level-answer-v1",
        "answer_correctness": "PASS" if not unsupported and not partial else "FAIL",
        "coverage_complete": bool(coverage_complete),
        "claim_count": len(audited),
        "unsupported_claim_count": unsupported,
        "partially_supported_claim_count": partial,
        "claims": audited,
    }
