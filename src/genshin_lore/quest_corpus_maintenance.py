"""Public orchestration for the sealed AQ corpus release gate."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ._quest_corpus_db_audit import audit_database
from ._quest_corpus_release_contract import (
    QuestCorpusVerificationError,
    build_expectation,
    load_json_object,
    manifest_root_for,
    require_build_report_shape,
    verification_error,
    verify_report_binding,
)
from ._readonly_database import open_readonly_database


_FINGERPRINT_CHUNK_BYTES = 1024 * 1024


def _streaming_sha256(path: Path) -> str:
    """Hash a release artifact without materializing the artifact in memory."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(_FINGERPRINT_CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _fingerprint(path: Path) -> dict[str, Any]:
    try:
        stat = path.stat()
        digest = _streaming_sha256(path)
    except OSError as exc:
        raise verification_error(f"cannot fingerprint database {path}: {exc}") from exc
    return {
        "sha256": digest,
        "size": stat.st_size,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _assert_unchanged(path: Path, before: dict[str, Any]) -> None:
    after = _fingerprint(path)
    if before != after:
        raise verification_error(
            "database fingerprint changed during verification: "
            f"before={before}, after={after}"
        )


def verify_quest_corpus_evidence_db(
    db_path: str | Path,
    build_report_path: str | Path,
    corpus_manifest_path: str | Path,
    *,
    manifest_root: str | Path | None = None,
    source_root: str | Path | None = None,
) -> dict[str, Any]:
    """Verify a sealed AQ corpus database without changing it."""

    database_path = Path(db_path).resolve()
    if not database_path.is_file():
        raise verification_error(f"database file not found: {database_path}")
    build_path = Path(build_report_path).resolve()
    build_report = load_json_object(build_path, "build report")
    require_build_report_shape(build_report)
    corpus_path = Path(corpus_manifest_path).resolve()
    root = manifest_root_for(corpus_path, manifest_root)
    expected = build_expectation(corpus_path, root)
    verify_report_binding(build_report, expected)

    before = _fingerprint(database_path)
    try:
        with open_readonly_database(database_path) as readonly:
            if int(readonly.conn.execute("PRAGMA query_only").fetchone()[0]) != 1:
                raise verification_error("readonly database did not enable PRAGMA query_only")
            if int(readonly.conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
                raise verification_error("readonly database did not enable PRAGMA foreign_keys")
            source_proof = None
            if expected.manifest_set.corpus_manifest.scalable_scope is not None:
                if source_root is None:
                    raise verification_error("scalable verification requires explicit pinned source_root")
                from .quest_talk_source_verifier import verify_source_closure
                source_proof = verify_source_closure(readonly.conn, expected, Path(source_root))
            details = audit_database(readonly.conn, expected, build_report)
            if source_proof is not None:
                independent_graph = source_proof.pop("graph_projection")
                if independent_graph != details["graph_projection"]:
                    raise verification_error("independent source graph reconstruction mismatch")
                from .util import canonical_json
                source_proof["graph_projection_sha256"] = hashlib.sha256(canonical_json(independent_graph).encode()).hexdigest()
    except QuestCorpusVerificationError:
        _assert_unchanged(database_path, before)
        raise
    except Exception as exc:
        _assert_unchanged(database_path, before)
        raise verification_error(f"cannot verify readonly database {database_path}: {exc}") from exc
    after = _fingerprint(database_path)
    if before != after:
        raise verification_error(
            f"database fingerprint changed during verification: before={before}, after={after}"
        )

    return {
        "status": "pass",
        "source_closure": source_proof,
        "verifier_schema_version": "1",
        "protocol": "quest-corpus-evidence-db-v1",
        "database": {
            "path": str(database_path),
            "fingerprint_before": before,
            "fingerprint_after": after,
            "unchanged": True,
            "schema_version": details["schema_version"],
        },
        "database_fingerprint_before": before,
        "database_fingerprint_after": after,
        "database_fingerprint_unchanged": True,
        "manifest_binding": {
            "manifest_id": expected.manifest_set.corpus_manifest.manifest_id,
            "corpus_manifest_path": str(corpus_path),
            "raw_sha256": expected.master_manifest_sha256,
            "audit_cache_sha256": expected.audit_cache_sha256,
            "selected_paths": list(expected.selected_paths),
            "snapshot_id": expected.snapshot_id,
            "source_id": expected.source_manifest["source_id"],
            "release_label": expected.source_manifest["release_label"],
            "commit": expected.source_manifest["commit"],
            "tree": expected.source_manifest["tree"],
        },
        "run_binding": details["run"],
        "parser_build_id": expected.parser_build_id,
        "ingest_run_id": expected.ingest_run_id,
        "corpus_config_checksum": expected.config_checksum,
        "counts": details["counts"],
        "graph_projection": details["graph_projection"],
        "text_hydration": details["text_hydration"],
        "foreign_key_check": details["foreign_key_check"],
        "mcp_readonly_compatible": True,
        "blockers": [],
    }


__all__ = ["QuestCorpusVerificationError", "verify_quest_corpus_evidence_db"]
