"""R3 text-backed evidence uplift for the six remaining Wave01 families.

This is a family-specific projection wave over the shared physical TextMap
occurrence shard.  It does not infer relations or logical identity and it does
not change the accepted Schema 19 database.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .batch_m0_baseline import SOURCE_SNAPSHOT_ID
from .batch_m3_r1_projection import (
    FAMILY_BY_NAME,
    _iter_array_records,
    derive_ingest_run_id_v2,
    derive_parser_build_id_v2,
    verify_source_pin,
)
from .family_evidence import FamilyEvidenceResult
from .util import canonical_json, sha256_bytes

R3_ARTIFACT_CONTRACT = "batch-r3-wave01-remaining-text-uplift-v1"
R3_COVERAGE = "R1_declared_text_fields_processed"
TEXTMAP_SHARD_SIZE = 1_468_526_592
TEXTMAP_SHARD_SHA256 = "7a32e4d40a26025162d344e7cf5d4fc7472f21cbe512767182c6df0d71f432fc"
TEXTMAP_CORPUS_IDENTITY = {
    "contract_version": "snapshot-textmap-physical-v2",
    "family": "__snapshot_textmap_physical__",
    "ingest_run_id": "ir-batch-m1-textmap:fb4eac91a0fec3995a1a973b3e8d5cd2904fcec7",
    "parser_build_id": "pb-batch-m1-textmap:87032d7a26f43990aed5c1740de1c0a32c6e559e",
    "snapshot_id": SOURCE_SNAPSHOT_ID,
    "source_record_granularity": "one_object_container_per_file",
    "container_count": "6",
    "row_count": "2474525",
    "rowset_sha256": "934b666c252c5a4def295672136902ff187f5bd37af5742bbe2bc88da06d09d0",
    "manifest_sha256": "e4b8423729a2726df2f681894cf54a92231ea6eca78acb94e2e42f18fa9f8c40",
}
R3_FAMILY_ORDER = (
    "activity_archive",
    "avatar_config_archive",
    "material_config_archive",
    "monster_archive",
    "reliquary_config_archive",
    "weapon_config_archive",
)
R3_DEFINITIONS: dict[str, tuple[str, ...]] = {
    "activity_archive": ("NameTextMapHash",),
    "avatar_config_archive": (
        "nameTextMapHash", "descTextMapHash", "coopPicNameHash",
        "gachaCardNameHash", "gachaImageNameHash",
    ),
    "material_config_archive": (
        "nameTextMapHash", "descTextMapHash", "effectDescTextMapHash",
        "interactionTitleTextMapHash", "specialDescTextMapHash",
        "typeDescTextMapHash",
    ),
    "monster_archive": ("nameTextMapHash",),
    "reliquary_config_archive": ("nameTextMapHash", "descTextMapHash"),
    "weapon_config_archive": (
        "nameTextMapHash", "descTextMapHash", "gachaCardNameHash",
    ),
}


class R3ProjectionError(RuntimeError):
    """The R3 source, artifact, or manifest violates its frozen contract."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _id(*parts: str) -> str:
    return _sha("\0".join(parts).encode("utf-8", "surrogateescape"))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_manifest_sha256(value: Mapping[str, Any]) -> str:
    body = dict(value)
    body.pop("manifest_sha256", None)
    return sha256_bytes(canonical_json(body).encode("utf-8"))


def _identity(source_sha: str, family: str) -> tuple[str, str, str]:
    implementation = sha256_file(Path(__file__))
    config = _sha(canonical_json({
        "contract": R3_ARTIFACT_CONTRACT,
        "family": family,
        "text_fields": R3_DEFINITIONS[family],
        "relations": [],
    }).encode("utf-8"))
    parser = derive_parser_build_id_v2(family, implementation, config)
    source_seal = _sha(canonical_json({
        "snapshot": SOURCE_SNAPSHOT_ID,
        "source_sha256": source_sha,
        "textmap_sha256": TEXTMAP_SHARD_SHA256,
    }).encode("utf-8"))
    return parser, derive_ingest_run_id_v2(
        family, parser, config, source_seal,
    ), config


def build_r3_artifact(
    source_root: str | Path,
    textmap_shard: str | Path,
    output: str | Path,
) -> dict[str, Any]:
    root = Path(source_root).resolve()
    textmap = Path(textmap_shard).resolve()
    target = Path(output).resolve()
    verify_source_pin(root)
    if (
        not textmap.is_file()
        or textmap.stat().st_size != TEXTMAP_SHARD_SIZE
        or sha256_file(textmap) != TEXTMAP_SHARD_SHA256
    ):
        raise R3ProjectionError("TextMap shard seal mismatch")
    if target.exists():
        raise R3ProjectionError("R3 output must be absent")
    target.parent.mkdir(parents=True, exist_ok=True)

    required_hashes: set[str] = set()
    for family in R3_FAMILY_ORDER:
        definition = FAMILY_BY_NAME[family]
        raw = (root / definition.source_file).read_bytes()
        for _ordinal, _pointer, _token, pairs, _token_sha in _iter_array_records(raw):
            required_hashes.update(
                str(value) for key, value in (pairs or ())
                if key in R3_DEFINITIONS[family]
            )

    candidates: dict[str, list[tuple[Any, ...]]] = {
        value: [] for value in required_hashes
    }
    tm = sqlite3.connect(
        f"file:{textmap.as_posix()}?mode=ro&immutable=1", uri=True,
    )
    try:
        actual_identity = dict(tm.execute("SELECT key,value FROM shard_metadata"))
        if any(actual_identity.get(key) != value for key, value in TEXTMAP_CORPUS_IDENTITY.items()):
            raise R3ProjectionError("TextMap corpus identity mismatch")
        sql = (
            "SELECT key_text,locale,shard_tier,relative_path,source_record_pointer,pair_ordinal,"
            "pointer,raw_key_json,raw_text_json,raw_text,raw_key_sha256,"
            "raw_text_sha256,raw_record_sha256 FROM textmap_pair_occurrence "
            "ORDER BY relative_path,pair_ordinal"
        )
        for row in tm.execute(sql):
            key = str(row[0])
            if key in candidates:
                candidates[key].append(tuple(row[1:]))
    finally:
        tm.close()

    report: dict[str, Any] = {
        "contract": R3_ARTIFACT_CONTRACT,
        "snapshot_id": SOURCE_SNAPSHOT_ID,
        "family_order": list(R3_FAMILY_ORDER),
        "corpus_complete": False,
        "product_accepted": False,
        "textmap_corpus": {**TEXTMAP_CORPUS_IDENTITY, "artifact_size_bytes": TEXTMAP_SHARD_SIZE, "artifact_sha256": TEXTMAP_SHARD_SHA256},
        "families": [],
    }
    db = sqlite3.connect(target)
    try:
        db.execute("PRAGMA page_size=65536")
        db.execute("PRAGMA journal_mode=OFF")
        db.execute("PRAGMA synchronous=OFF")
        db.execute(
            "CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL) WITHOUT ROWID"
        )
        for family in R3_FAMILY_ORDER:
            fields = R3_DEFINITIONS[family]
            db.executescript(f"""
CREATE TABLE {family}_row_occurrence(occurrence_id TEXT PRIMARY KEY,ingest_run_id TEXT NOT NULL,parser_build_id TEXT NOT NULL,source_file_path TEXT NOT NULL,source_ordinal INTEGER NOT NULL,source_pointer TEXT NOT NULL,raw_record_json TEXT NOT NULL,raw_record_sha256 TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE {family}_text_owner_occurrence(owner_id TEXT PRIMARY KEY,row_occurrence_id TEXT NOT NULL,field_name TEXT NOT NULL,field_pair_ordinal INTEGER NOT NULL,raw_hash_text TEXT NOT NULL,resolution_status TEXT NOT NULL,candidate_count INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE {family}_text_resolution_occurrence(resolution_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,locale TEXT NOT NULL,resolution_status TEXT NOT NULL,candidate_count INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE {family}_text_candidate_occurrence(candidate_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,textmap_snapshot_id TEXT NOT NULL,textmap_parser_build_id TEXT NOT NULL,textmap_ingest_run_id TEXT NOT NULL,locale TEXT NOT NULL,shard_tier TEXT NOT NULL,relative_path TEXT NOT NULL,source_record_pointer TEXT NOT NULL,pair_ordinal INTEGER NOT NULL,pointer TEXT NOT NULL,raw_key_json TEXT NOT NULL,raw_text_json TEXT NOT NULL,raw_text TEXT NOT NULL,raw_key_sha256 TEXT NOT NULL,raw_text_sha256 TEXT NOT NULL,raw_record_sha256 TEXT NOT NULL) WITHOUT ROWID;
CREATE INDEX idx_{family}_row_order ON {family}_row_occurrence(source_ordinal,occurrence_id);
CREATE INDEX idx_{family}_owner_row ON {family}_text_owner_occurrence(row_occurrence_id,field_name,field_pair_ordinal);
CREATE INDEX idx_{family}_text_search ON {family}_text_candidate_occurrence(raw_text,locale,owner_id,candidate_id);
""")
            definition = FAMILY_BY_NAME[family]
            raw = (root / definition.source_file).read_bytes()
            source_sha = _sha(raw)
            parser, run, config = _identity(source_sha, family)
            counts = {
                "raw_rows": 0, "text_owners": 0, "text_resolutions": 0,
                "text_candidates": 0, "missing_locale_resolutions": 0,
                "ambiguous_locale_resolutions": 0,
            }
            for ordinal, pointer, token, pairs, token_sha in _iter_array_records(raw):
                row_id = _id(family, run, definition.source_file, pointer, token_sha)
                db.execute(
                    f"INSERT INTO {family}_row_occurrence VALUES(?,?,?,?,?,?,?,?)",
                    (row_id, run, parser, definition.source_file, ordinal,
                     pointer, token, token_sha),
                )
                counts["raw_rows"] += 1
                for pair_ordinal, (key, value) in enumerate(pairs or ()):
                    if key not in fields:
                        continue
                    raw_hash = str(value)
                    owner_id = _id(family, run, row_id, key, str(pair_ordinal))
                    physical_candidates = candidates.get(raw_hash, ())
                    locale_counts: dict[str, int] = {}
                    for candidate in physical_candidates:
                        locale = str(candidate[0])
                        locale_counts[locale] = locale_counts.get(locale, 0) + 1
                    statuses = {
                        locale: (
                            "missing" if locale_counts.get(locale, 0) == 0
                            else "resolved_unique" if locale_counts[locale] == 1
                            else "ambiguous"
                        ) for locale in ("CHS", "EN", "JP")
                    }
                    status = (
                        "ambiguous" if "ambiguous" in statuses.values()
                        else "partial_missing" if "missing" in statuses.values()
                        else "resolved_all_locales"
                    )
                    db.execute(
                        f"INSERT INTO {family}_text_owner_occurrence VALUES(?,?,?,?,?,?,?)",
                        (owner_id, row_id, key, pair_ordinal, raw_hash, status,
                         len(physical_candidates)),
                    )
                    counts["text_owners"] += 1
                    for locale in ("CHS", "EN", "JP"):
                        locale_status = statuses[locale]
                        locale_count = locale_counts.get(locale, 0)
                        db.execute(
                            f"INSERT INTO {family}_text_resolution_occurrence VALUES(?,?,?,?,?)",
                            (_id(family, run, owner_id, locale), owner_id, locale,
                             locale_status, locale_count),
                        )
                        counts["text_resolutions"] += 1
                        counts["missing_locale_resolutions"] += locale_status == "missing"
                        counts["ambiguous_locale_resolutions"] += locale_status == "ambiguous"
                    for candidate in physical_candidates:
                        candidate_id = _id(
                            family, run, owner_id, str(candidate[2]),
                            str(candidate[4]),
                        )
                        db.execute(
                            f"INSERT INTO {family}_text_candidate_occurrence VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (candidate_id, owner_id, TEXTMAP_CORPUS_IDENTITY["snapshot_id"],
                             TEXTMAP_CORPUS_IDENTITY["parser_build_id"],
                             TEXTMAP_CORPUS_IDENTITY["ingest_run_id"], *candidate),
                        )
                        counts["text_candidates"] += 1
            report["families"].append({
                "family": family,
                "family_contract_version": definition.family_contract_version.replace("-v1", "-v2"),
                "source_file": definition.source_file,
                "source_file_sha256": source_sha,
                "parser_build_id": parser,
                "ingest_run_id": run,
                "configuration_sha256": config,
                "raw_rows_complete": True,
                "declared_text_fields": list(fields),
                "declared_text_fields_processed": True,
                "declared_relations": [],
                "declared_relations_processed": True,
                "query_available": True,
                "search_available": True,
                "product_accepted": False,
                "counts": counts,
            })
        db.execute(
            "INSERT INTO metadata VALUES('contract',?)",
            (canonical_json(R3_ARTIFACT_CONTRACT),),
        )
        db.execute(
            "INSERT INTO metadata VALUES('report',?)", (canonical_json(report),),
        )
        db.commit()
    finally:
        db.close()
    os.chmod(target, 0o600)
    report["artifact"] = {
        "path": str(target), "size_bytes": target.stat().st_size,
        "sha256": sha256_file(target),
    }
    report["manifest_sha256"] = canonical_manifest_sha256(report)
    return report


def load_r3_report(artifact: str | Path) -> dict[str, Any]:
    path = Path(artifact).resolve()
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        row = db.execute("SELECT value FROM metadata WHERE key='report'").fetchone()
        value = json.loads(row[0]) if row else None
    finally:
        db.close()
    if not isinstance(value, dict) or value.get("contract") != R3_ARTIFACT_CONTRACT:
        raise R3ProjectionError("R3 artifact contract mismatch")
    return value


def qualify_r3_artifact(
    artifact: str | Path, manifest_path: str | Path,
) -> dict[str, Any]:
    path = Path(artifact).resolve()
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise R3ProjectionError("R3 tracked manifest is unreadable") from exc
    if not isinstance(manifest, dict):
        raise R3ProjectionError("R3 tracked manifest must be an object")
    if (
        manifest.get("contract") != R3_ARTIFACT_CONTRACT
        or manifest.get("snapshot_id") != SOURCE_SNAPSHOT_ID
        or manifest.get("family_order") != list(R3_FAMILY_ORDER)
        or manifest.get("corpus_complete") is not False
        or manifest.get("product_accepted") is not False
    ):
        raise R3ProjectionError("R3 tracked manifest boundary mismatch")
    if manifest.get("manifest_sha256") != canonical_manifest_sha256(manifest):
        raise R3ProjectionError("R3 tracked manifest canonical seal mismatch")
    binding = manifest.get("artifact")
    if (
        not isinstance(binding, dict)
        or binding.get("size_bytes") != path.stat().st_size
        or binding.get("sha256") != sha256_file(path)
    ):
        raise R3ProjectionError("R3 artifact size/SHA binding mismatch")
    internal = load_r3_report(path)
    for key in (
        "contract", "snapshot_id", "family_order", "corpus_complete",
        "product_accepted", "textmap_corpus", "families",
    ):
        if manifest.get(key) != internal.get(key):
            raise R3ProjectionError(f"R3 internal/tracked manifest mismatch: {key}")
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        tables = {str(row[0]) for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        suffixes = (
            "row_occurrence", "text_owner_occurrence",
            "text_resolution_occurrence", "text_candidate_occurrence",
        )
        expected = {"metadata"} | {
            f"{family}_{suffix}" for family in R3_FAMILY_ORDER
            for suffix in suffixes
        }
        if tables != expected:
            raise R3ProjectionError("R3 table closure mismatch")
        corpus_tuple = (TEXTMAP_CORPUS_IDENTITY["snapshot_id"],
                        TEXTMAP_CORPUS_IDENTITY["parser_build_id"],
                        TEXTMAP_CORPUS_IDENTITY["ingest_run_id"])
        for item in internal["families"]:
            family = str(item["family"])
            if family not in R3_DEFINITIONS:
                raise R3ProjectionError(f"R3 undeclared family: {family}")
            if item.get("declared_text_fields") != list(R3_DEFINITIONS[family]):
                raise R3ProjectionError(f"R3 text-field contract mismatch: {family}")
            if item.get("declared_relations") != []:
                raise R3ProjectionError(f"R3 relation allowlist is not empty: {family}")
            run = str(item["ingest_run_id"])
            parser = str(item["parser_build_id"])
            counts = item["counts"]
            row = db.execute(
                f"SELECT COUNT(*),COUNT(DISTINCT ingest_run_id),MIN(ingest_run_id),"
                f"COUNT(DISTINCT parser_build_id),MIN(parser_build_id) "
                f"FROM {family}_row_occurrence"
            ).fetchone()
            if tuple(row) != (counts["raw_rows"], 1, run, 1, parser):
                raise R3ProjectionError(f"R3 row/run/parser mismatch: {family}")
            actual = {
                "text_owners": db.execute(f"SELECT COUNT(*) FROM {family}_text_owner_occurrence").fetchone()[0],
                "text_resolutions": db.execute(f"SELECT COUNT(*) FROM {family}_text_resolution_occurrence").fetchone()[0],
                "text_candidates": db.execute(f"SELECT COUNT(*) FROM {family}_text_candidate_occurrence").fetchone()[0],
                "missing_locale_resolutions": db.execute(f"SELECT COUNT(*) FROM {family}_text_resolution_occurrence WHERE resolution_status='missing'").fetchone()[0],
                "ambiguous_locale_resolutions": db.execute(f"SELECT COUNT(*) FROM {family}_text_resolution_occurrence WHERE resolution_status='ambiguous'").fetchone()[0],
            }
            if any(actual[key] != counts[key] for key in actual):
                raise R3ProjectionError(f"R3 count mismatch: {family}")
            candidate_scope = db.execute(
                f"SELECT COUNT(DISTINCT textmap_snapshot_id || char(0) || "
                f"textmap_parser_build_id || char(0) || textmap_ingest_run_id),"
                f"MIN(textmap_snapshot_id),MIN(textmap_parser_build_id),MIN(textmap_ingest_run_id) "
                f"FROM {family}_text_candidate_occurrence"
            ).fetchone()
            if counts["text_candidates"] and tuple(candidate_scope) != (1, *corpus_tuple):
                raise R3ProjectionError(f"R3 TextMap corpus scope mismatch: {family}")
            dangling = db.execute(
                f"SELECT COUNT(*) FROM {family}_text_candidate_occurrence c "
                f"LEFT JOIN {family}_text_owner_occurrence o ON o.owner_id=c.owner_id "
                "WHERE o.owner_id IS NULL"
            ).fetchone()[0]
            if dangling:
                raise R3ProjectionError(f"R3 dangling candidate: {family}")
    finally:
        db.close()
    return manifest


def _decode_cursor(value: str | None, scope: Mapping[str, Any]) -> list[Any] | None:
    if value is None:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(
            (value + "=" * (-len(value) % 4)).encode()
        ))
    except Exception as exc:
        raise ValueError("cursor is invalid") from exc
    if payload.get("scope") != dict(scope) or not isinstance(payload.get("position"), list):
        raise ValueError("cursor scope mismatch")
    return list(payload["position"])


def _encode_cursor(scope: Mapping[str, Any], position: list[Any]) -> str:
    return base64.urlsafe_b64encode(canonical_json({
        "scope": dict(scope), "position": position,
    }).encode()).decode().rstrip("=")


@dataclass
class R3TextUpliftProvider:
    artifact: Path
    family: str
    qualification: Mapping[str, Any]

    def __post_init__(self) -> None:
        self.artifact = self.artifact.resolve()
        self.item = next(
            (item for item in self.qualification["families"] if item["family"] == self.family),
            None,
        )
        if self.item is None:
            raise R3ProjectionError("R3 family absent")
        self.family_contract_version = str(self.item["family_contract_version"])
        self.conn = sqlite3.connect(
            f"file:{self.artifact.as_posix()}?mode=ro&immutable=1",
            uri=True, check_same_thread=False,
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA query_only=ON")

    def close(self) -> None:
        self.conn.close()

    def capability(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "family_contract_version": self.family_contract_version,
            "evidence_available": True,
            "eligible_runs": [{
                "ingest_run_id": self.item["ingest_run_id"],
                "parser_build_id": self.item["parser_build_id"],
                "snapshot_id": SOURCE_SNAPSHOT_ID,
                "family_contract_version": self.family_contract_version,
                "coverage": R3_COVERAGE,
                "corpus_complete": False,
            }],
            "supported_operations": [
                "list_family_evidence", "get_family_evidence", "search_family_text",
            ],
            "supported_filters": {
                "list_family_evidence": ["source_ordinal"],
                "get_family_evidence": [],
                "search_family_text": ["locale", "field_name"],
            },
            "pagination_contract": {
                "kind": "deterministic_keyset",
                "limit": {"default": 50, "minimum": 1, "maximum": 100},
            },
            "coverage_semantics": {
                "raw_rows_complete": True,
                "declared_text_fields_processed": True,
                "declared_relations_processed": True,
                "query_available": True,
                "product_accepted": False,
                "full_7_0_claimed": False,
            },
            "corpus_complete": False,
            "evidence_only": True,
        }

    def _result(self, status: str, data: dict[str, Any]) -> FamilyEvidenceResult:
        return FamilyEvidenceResult(
            status, self.family, str(self.item["ingest_run_id"]),
            self.family_contract_version, R3_COVERAGE, False, data,
        )

    def _scope_error(
        self, ingest_run_id: str, family_contract_version: str,
    ) -> FamilyEvidenceResult | None:
        if (
            ingest_run_id != self.item["ingest_run_id"]
            or family_contract_version != self.family_contract_version
        ):
            return FamilyEvidenceResult(
                "not_found", self.family, ingest_run_id, family_contract_version,
                "unavailable", False,
                {"status": "NotFound", "reason": "exact_run_or_contract_mismatch"},
            )
        return None

    def list_evidence(
        self, *, ingest_run_id: str, family_contract_version: str,
        filters: dict[str, Any] | None = None, cursor: str | None = None,
        limit: int = 50,
    ) -> FamilyEvidenceResult:
        error = self._scope_error(ingest_run_id, family_contract_version)
        if error is not None:
            return error
        selected = filters or {}
        if set(selected) - {"source_ordinal"}:
            raise ValueError("unsupported filters")
        scope = {"family": self.family, "run": ingest_run_id, "filters": selected}
        position = _decode_cursor(cursor, scope)
        after = int(position[0]) if position else -1
        sql = f"SELECT * FROM {self.family}_row_occurrence WHERE source_ordinal>?"
        params: list[Any] = [after]
        if "source_ordinal" in selected:
            sql += " AND source_ordinal=?"
            params.append(selected["source_ordinal"])
        rows = self.conn.execute(
            sql + " ORDER BY source_ordinal,occurrence_id LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
        return self._result("ok", {
            "items": [dict(row) for row in rows[:limit]],
            "next_cursor": _encode_cursor(
                scope, [int(rows[limit - 1]["source_ordinal"])]
            ) if len(rows) > limit else None,
        })

    def get_evidence(
        self, *, ingest_run_id: str, family_contract_version: str,
        occurrence_id: str,
    ) -> FamilyEvidenceResult:
        error = self._scope_error(ingest_run_id, family_contract_version)
        if error is not None:
            return error
        row = self.conn.execute(
            f"SELECT * FROM {self.family}_row_occurrence WHERE occurrence_id=?",
            (occurrence_id,),
        ).fetchone()
        if row is None:
            return self._result("not_found", {
                "status": "NotFound", "reason": "occurrence_not_found",
            })
        owners = [dict(item) for item in self.conn.execute(
            f"SELECT * FROM {self.family}_text_owner_occurrence "
            "WHERE row_occurrence_id=? ORDER BY field_name,field_pair_ordinal,owner_id",
            (occurrence_id,),
        )]
        for owner in owners:
            owner["resolutions"] = [dict(item) for item in self.conn.execute(
                f"SELECT * FROM {self.family}_text_resolution_occurrence "
                "WHERE owner_id=? ORDER BY locale,resolution_id", (owner["owner_id"],),
            )]
            owner["candidates"] = [dict(item) for item in self.conn.execute(
                f"SELECT * FROM {self.family}_text_candidate_occurrence "
                "WHERE owner_id=? ORDER BY relative_path,pair_ordinal,candidate_id",
                (owner["owner_id"],),
            )]
        return self._result("ok", {"item": dict(row), "text_owners": owners})

    def search_text(
        self, *, ingest_run_id: str, family_contract_version: str,
        query: str, filters: dict[str, Any] | None = None,
        cursor: str | None = None, limit: int = 50,
    ) -> FamilyEvidenceResult:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must contain non-whitespace text")
        error = self._scope_error(ingest_run_id, family_contract_version)
        if error is not None:
            return error
        selected = filters or {}
        if set(selected) - {"locale", "field_name"}:
            raise ValueError("unsupported filters")
        scope = {
            "family": self.family, "run": ingest_run_id,
            "query": query, "filters": selected,
        }
        position = _decode_cursor(cursor, scope)
        sql = (
            f"SELECT c.*,o.row_occurrence_id,o.field_name,r.source_ordinal "
            f"FROM {self.family}_text_candidate_occurrence c "
            f"JOIN {self.family}_text_owner_occurrence o ON o.owner_id=c.owner_id "
            f"JOIN {self.family}_row_occurrence r ON r.occurrence_id=o.row_occurrence_id "
            "WHERE instr(c.raw_text,?)>0"
        )
        params: list[Any] = [query]
        if position is not None:
            if len(position) != 4:
                raise ValueError("cursor position is invalid")
            sql += " AND (r.source_ordinal,c.relative_path,c.pair_ordinal,c.candidate_id)>(?,?,?,?)"
            params.extend(position)
        if "locale" in selected:
            sql += " AND c.locale=?"
            params.append(selected["locale"])
        if "field_name" in selected:
            sql += " AND o.field_name=?"
            params.append(selected["field_name"])
        rows = self.conn.execute(
            sql + " ORDER BY r.source_ordinal,c.relative_path,c.pair_ordinal,c.candidate_id LIMIT ?",
            (*params, limit + 1),
        ).fetchall()
        return self._result("ok", {
            "items": [dict(row) for row in rows[:limit]],
            "next_cursor": _encode_cursor(scope, [
                int(rows[limit - 1]["source_ordinal"]),
                str(rows[limit - 1]["relative_path"]),
                int(rows[limit - 1]["pair_ordinal"]),
                str(rows[limit - 1]["candidate_id"]),
            ]) if len(rows) > limit else None,
        })


def build_r3_providers(
    artifact: str | Path, manifest_path: str | Path,
) -> tuple[R3TextUpliftProvider, ...]:
    qualification = qualify_r3_artifact(artifact, manifest_path)
    return tuple(
        R3TextUpliftProvider(Path(artifact), family, qualification)
        for family in R3_FAMILY_ORDER
    )


__all__ = [
    "R3_ARTIFACT_CONTRACT", "R3_COVERAGE", "R3_DEFINITIONS",
    "R3_FAMILY_ORDER", "R3ProjectionError", "R3TextUpliftProvider",
    "build_r3_artifact", "build_r3_providers", "canonical_manifest_sha256",
    "load_r3_report", "qualify_r3_artifact", "sha256_file",
]
