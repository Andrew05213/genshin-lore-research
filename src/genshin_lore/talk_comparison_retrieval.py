"""Family-specific read-only query surface for the Phase 3.1C3 pilot.

The repository exposes comparison evidence, not logical passage identity.  It
accepts one exact, explicitly named compare run and one explicit C3B release
report.  The report is a byte-bound prefilter; tracked manifest facts and all
business row-set seals are independently checked against the database.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any, Mapping

from ._readonly_database import ReadonlyDatabase, open_readonly_database
from .talk_comparison_phase31c3b_contract import (
    CONTRACT_VERSION,
    FAMILY,
    MANIFEST_PATH,
    MANIFEST_SHA256,
    MATCHER_METHOD,
    SCOPE_KEY,
    SIDES,
    SNAPSHOT_ID,
    canonical,
    load_manifest,
)


ROOT = Path(__file__).resolve().parents[2]
TRACKED_MANIFEST = ROOT / MANIFEST_PATH
RELEASE_REPORT_SHA256 = "38684e2d198e2b640d1a3444e1aa2e705944b7c16b8e5b28cdb21e9685482c53"
ARTIFACT_SHA256 = "27b0e80c23fb91869da2cbc81d06671f5ac26cef02023b46bf6fd6e50acd0abc"
CURSOR_SEAL_DOMAIN = b"genshin-lore/talk-comparison/cursor-corruption-v1\0"
ARTIFACT_SIZE = 991232
CURSOR_VERSION = "talk-comparison-match-set-cursor-v1"
EXPECTED_SEALS: dict[str, tuple[int, str]] = {
    "compare_run": (1, "5be089181b28ae8de8fbb3e34fc7dc9975bf2c8dd2737ae285aca681fad8de70"),
    "comparison_absence_assertion": (0, "37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570"),
    "comparison_change_label": (28, "d4f8c2712268650c3460a637a63f2b416e18d0446d14cea540a2acf61b262358"),
    "comparison_change_observation": (14, "552b675956f4f4a9f2dfe5c3821d9b2502bb64e275bf486b5b5e910f342705a4"),
    "comparison_match_anchor": (28, "9d3818b73db7120d095fa6617161db60ee93b327d005f7960444d84a47b6989b"),
    "comparison_match_candidate": (14, "a64b21c61a67571ca3cec1439248a73663eb4f7c634cc48fe9d8d62e43b8b6d0"),
    "comparison_match_decision": (14, "da9ccf78446388a1b9d1b030c08e73687d553742a0a37c7b62edd68093885660"),
    "comparison_match_set": (14, "fc9b229f741d665ae28164f091f1addf7c02d14e282f4bc1c11a7191faf91198"),
    "comparison_scope_proof": (2, "630262f7b13dbdbdaa03cb455b326b05e55c120a5ae01b0234381c5807c764ce"),
    "comparison_side": (2, "e1cda74f184f0ec0f3ebf072f7e1a9fbeb17842cf5f14a0db32308828929c983"),
    "evidence_container": (2, "df915a6674fa29481dfc27e27a846e51b9207e6560afbeeda4a895fdf6534851"),
    "ingest_run": (2, "97a624d8d6035e30d7f22849366b1c4ac36c7c41177d42acbd8ecb8300117ec9"),
    "matcher_build": (1, "6007d055ff36bd20c6728a879aa718d21b588a0818c33025a2457451373e9a8b"),
    "occurrence_edge": (28, "343686db2a943b9b5a97a6797ea6399a1bd20d840f1f0a58e4e22ee76d947cd6"),
    "parser_build": (2, "3172d1a6650aa51c4dd4e9b2c7ce6177028784197cf3b0efc0134b27999b02f7"),
    "passage_occurrence": (28, "0f003f4f3a0109fc93f538089e4376957b4bdaf52ce95027e643276b972411c8"),
    "passage_occurrence_text": (84, "5cd892887642a05f19c31b34a0cc68ad1c8d00b84a1cd65040200e71c7daf8ea"),
    "source_file": (7, "299165070799af5d012118dd6bb7721b2b709f45d57c12ea81e3e497771e44a8"),
    "source_record": (43, "f147df42f454d875922c84feb019111981d07a079b8aa93841f79d0a3836d384"),
    "source_snapshot": (1, "d42bbd26f887bc1772214b5d5c59a4a25d918ead2ae172b43a34e13bd62ac21f"),
}


class TalkComparisonRepositoryError(RuntimeError):
    """The requested pilot or its runtime binding is not eligible."""


class TalkComparisonNotFound(LookupError):
    """The exact match-set occurrence does not exist in the compare run."""


def _strict_json(raw: bytes, label: str) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise TalkComparisonRepositoryError(f"duplicate JSON key in {label}: {key}")
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(
                              TalkComparisonRepositoryError(f"non-standard JSON constant in {label}: {value}")))
    except TalkComparisonRepositoryError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TalkComparisonRepositoryError(f"invalid JSON in {label}: {exc}") from exc


def _sidecars(path: Path) -> None:
    for suffix in ("-journal", "-wal", "-shm"):
        if Path(str(path) + suffix).exists():
            raise TalkComparisonRepositoryError(f"SQLite sidecar is present: {path.name}{suffix}")


def _read_fingerprinted(path_value: str | Path, label: str, *,
                        capture_bytes: bool = False) -> tuple[dict[str, Any], bytes | None]:
    path = Path(os.path.abspath(os.fspath(path_value)))
    _sidecars(path) if label == "database" else None
    try:
        before = os.lstat(path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise TalkComparisonRepositoryError(f"{label} must be a regular non-symlink file")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            opened = os.fstat(descriptor)
            identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != identity:
                raise TalkComparisonRepositoryError(f"{label} identity changed before hashing")
            digest = hashlib.sha256()
            captured = bytearray() if capture_bytes else None
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
                if captured is not None:
                    captured.extend(chunk)
            after_open = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after = os.lstat(path)
    except TalkComparisonRepositoryError:
        raise
    except OSError as exc:
        raise TalkComparisonRepositoryError(f"cannot fingerprint {label}: {exc}") from exc
    final_identity = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    open_identity = (after_open.st_dev, after_open.st_ino, after_open.st_size, after_open.st_mtime_ns)
    if identity != final_identity or identity != open_identity:
        raise TalkComparisonRepositoryError(f"{label} changed while hashing")
    _sidecars(path) if label == "database" else None
    fingerprint = {"path": path, "identity": identity, "size_bytes": before.st_size,
                   "sha256": digest.hexdigest()}
    return fingerprint, bytes(captured) if captured is not None else None


def _fingerprint(path_value: str | Path, label: str) -> dict[str, Any]:
    return _read_fingerprinted(path_value, label)[0]


def _table_seal(conn: sqlite3.Connection, table: str) -> tuple[int, str]:
    columns = [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')]
    if table == "source_file":
        columns = [column for column in columns if column != "root_path"]
    if not columns:
        raise TalkComparisonRepositoryError(f"required business table is absent: {table}")
    quoted = ",".join(f'"{column}"' for column in columns)
    rows = [tuple(row) for row in conn.execute(
        f'SELECT {quoted} FROM "{table}" ORDER BY {quoted}'
    )]
    raw = (json.dumps(rows, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")) + "\n").encode("utf-8")
    return len(rows), hashlib.sha256(raw).hexdigest()


def _json_column(value: Any) -> Any:
    return None if value is None else _strict_json(str(value).encode("utf-8"), "database JSON column")


def _envelope(compare_run_id: str, data: Any) -> dict[str, Any]:
    return {
        "family": FAMILY,
        "family_contract_version": CONTRACT_VERSION,
        "compare_run_id": compare_run_id,
        "evidence_only": True,
        "coverage": "bounded_comparison_pilot",
        "corpus_complete": False,
        "data": data,
    }


class TalkComparisonRepository:
    """Exact-run, family-specific query repository for the standalone pilot."""

    def __init__(self, database: str | Path, release_report: str | Path,
                 compare_run_id: str) -> None:
        if not isinstance(compare_run_id, str) or not compare_run_id:
            raise TalkComparisonRepositoryError("exact compare_run_id is required")
        self._manifest = load_manifest(ROOT, TRACKED_MANIFEST)
        expected_run = str(self._manifest["compare_run"]["compare_run_id"])
        if compare_run_id != expected_run:
            raise TalkComparisonRepositoryError("compare_run_id is not the exact eligible pilot run")
        self.compare_run_id = compare_run_id
        self._report_path = Path(os.path.abspath(os.fspath(release_report)))
        report_fp, report_raw = _read_fingerprinted(
            self._report_path, "release report", capture_bytes=True)
        if report_fp["sha256"] != RELEASE_REPORT_SHA256:
            raise TalkComparisonRepositoryError("C3B release report byte seal mismatch")
        report = _strict_json(report_raw or b"", "C3B release report")
        artifact = report.get("artifact") if isinstance(report, dict) else None
        comparison = report.get("comparison") if isinstance(report, dict) else None
        if (report.get("report_kind") != "phase31c3b_release_report" or report.get("status") != "pass"
                or report.get("family") != FAMILY or report.get("family_contract_version") != CONTRACT_VERSION
                or not isinstance(artifact, dict) or artifact.get("sha256") != ARTIFACT_SHA256
                or artifact.get("size_bytes") != ARTIFACT_SIZE or not isinstance(comparison, dict)
                or comparison.get("compare_run_id") != compare_run_id):
            raise TalkComparisonRepositoryError("C3B release report contract mismatch")
        self._db: ReadonlyDatabase = open_readonly_database(database)
        try:
            db_fp = _fingerprint(self._db.path, "database")
            if db_fp["sha256"] != ARTIFACT_SHA256 or db_fp["size_bytes"] != ARTIFACT_SIZE:
                raise TalkComparisonRepositoryError("pilot artifact fingerprint mismatch")
            if self._db.file_identity != db_fp["identity"]:
                raise TalkComparisonRepositoryError("pilot database identity changed during startup")
            versions = [int(row[0]) for row in self._db.conn.execute(
                "SELECT version FROM schema_migration ORDER BY version")]
            if versions != list(range(1, 8)):
                raise TalkComparisonRepositoryError("pilot must have exact Schema 7 ledger")
            actual_seals = {table: _table_seal(self._db.conn, table) for table in EXPECTED_SEALS}
            if actual_seals != EXPECTED_SEALS:
                raise TalkComparisonRepositoryError("pilot business row-set seals mismatch")
            if self._db.conn.execute("PRAGMA foreign_key_check").fetchall():
                raise TalkComparisonRepositoryError("pilot foreign-key check failed")
            run = self._db.conn.execute(
                "SELECT family,status,metadata_json FROM compare_run WHERE id=?", (compare_run_id,)
            ).fetchone()
            metadata = _json_column(run[2]) if run is not None else None
            if (run is None or run[0] != FAMILY or run[1] != "complete" or not isinstance(metadata, dict)
                    or metadata.get("family_contract_version") != CONTRACT_VERSION
                    or metadata.get("scope_key") != SCOPE_KEY
                    or metadata.get("source_snapshot_id") != SNAPSHOT_ID
                    or metadata.get("logical_identity_created") is not False):
                raise TalkComparisonRepositoryError("exact compare-run business contract mismatch")
            self._startup_db = db_fp
            self._startup_report = report_fp
        except Exception:
            self._db.close()
            raise

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "TalkComparisonRepository":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def _assert_stable(self) -> None:
        db = _fingerprint(self._db.path, "database")
        report = _fingerprint(self._report_path, "release report")
        for current, original, label in ((db, self._startup_db, "database"),
                                         (report, self._startup_report, "release report")):
            if current["identity"] != original["identity"] or current["sha256"] != original["sha256"]:
                raise TalkComparisonRepositoryError(f"{label} changed after repository startup")

    def _call(self, operation: Any) -> Any:
        self._assert_stable()
        try:
            result = operation()
        finally:
            self._assert_stable()
        return result

    def summary(self) -> dict[str, Any]:
        def query() -> dict[str, Any]:
            row = self._db.conn.execute(
                "SELECT matcher_build_id,family,status,started_at,completed_at,metadata_json "
                "FROM compare_run WHERE id=?", (self.compare_run_id,)).fetchone()
            sides = {str(item[0]): str(item[1]) for item in self._db.conn.execute(
                "SELECT side,ingest_run_id FROM comparison_side WHERE compare_run_id=? ORDER BY side",
                (self.compare_run_id,))}
            counts = {
                "match_sets": self._db.conn.execute("SELECT COUNT(*) FROM comparison_match_set WHERE compare_run_id=?", (self.compare_run_id,)).fetchone()[0],
                "pair_candidates": self._db.conn.execute("SELECT COUNT(*) FROM comparison_match_candidate WHERE compare_run_id=? AND candidate_kind='pair'", (self.compare_run_id,)).fetchone()[0],
                "accepted_decisions": self._db.conn.execute("SELECT COUNT(*) FROM comparison_match_decision WHERE compare_run_id=? AND decision='accepted'", (self.compare_run_id,)).fetchone()[0],
                "present_both_observations": self._db.conn.execute("SELECT COUNT(*) FROM comparison_change_observation WHERE compare_run_id=? AND presence_outcome='present_both'", (self.compare_run_id,)).fetchone()[0],
                "absence_assertions": self._db.conn.execute("SELECT COUNT(*) FROM comparison_absence_assertion WHERE compare_run_id=?", (self.compare_run_id,)).fetchone()[0],
            }
            labels = {str(item[0]): int(item[1]) for item in self._db.conn.execute(
                "SELECT label,COUNT(*) FROM comparison_change_label WHERE compare_run_id=? GROUP BY label ORDER BY label",
                (self.compare_run_id,))}
            return _envelope(self.compare_run_id, {
                "status": str(row[2]), "scope_key": SCOPE_KEY,
                "source_snapshot_id": SNAPSHOT_ID, "matcher_method": MATCHER_METHOD,
                "matcher_build_id": str(row[0]), "started_at": str(row[3]),
                "completed_at": str(row[4]), "sides": sides, "counts": counts,
                "labels": labels, "controlled_replay": True,
                "real_version_difference_claimed": False,
                "logical_identity_created": False,
                "corpus_complete_semantics": "false; bounded to 14 occurrence match sets from one controlled replay, not full AQ or real version history",
            })
        return self._call(query)

    @staticmethod
    def _cursor_seal(unsigned: Mapping[str, Any]) -> str:
        digest = hashlib.sha256()
        digest.update(CURSOR_SEAL_DOMAIN)
        digest.update(MANIFEST_SHA256.encode("ascii"))
        digest.update(b"\0")
        digest.update(ARTIFACT_SHA256.encode("ascii"))
        digest.update(b"\0")
        digest.update(canonical(unsigned).encode("utf-8"))
        return digest.hexdigest()

    @classmethod
    def _cursor(cls, after_pointer: str, after_id: str, after_ordinal: int,
                compare_run_id: str) -> str:
        unsigned = {
            "v": CURSOR_VERSION,
            "family": FAMILY,
            "family_contract_version": CONTRACT_VERSION,
            "operation": "list-match-sets",
            "compare_run_id": compare_run_id,
            "filters": {},
            "after_pointer": after_pointer,
            "after_id": after_id,
            "after_ordinal": after_ordinal,
        }
        raw = canonical({**unsigned, "seal": cls._cursor_seal(unsigned)}).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _decode_cursor(self, value: str | None) -> tuple[str, str, int]:
        if value is None:
            return "", "", 0
        if not isinstance(value, str) or not value or len(value) > 2048:
            raise ValueError("cursor must be a non-empty opaque string")
        try:
            raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
            parsed = _strict_json(raw, "cursor")
        except (ValueError, TalkComparisonRepositoryError) as exc:
            raise ValueError("invalid or tampered cursor") from exc
        required = {
            "v", "family", "family_contract_version", "operation",
            "compare_run_id", "filters", "after_pointer", "after_id",
            "after_ordinal", "seal",
        }
        if (not isinstance(parsed, dict) or set(parsed) != required
                or parsed["v"] != CURSOR_VERSION or parsed["family"] != FAMILY
                or parsed["family_contract_version"] != CONTRACT_VERSION
                or parsed["operation"] != "list-match-sets"
                or parsed["compare_run_id"] != self.compare_run_id
                or parsed["filters"] != {}
                or not isinstance(parsed["after_pointer"], str) or not parsed["after_pointer"]
                or not isinstance(parsed["after_id"], str) or not parsed["after_id"]
                or isinstance(parsed["after_ordinal"], bool)
                or not isinstance(parsed["after_ordinal"], int) or parsed["after_ordinal"] < 1
                or not isinstance(parsed["seal"], str)):
            raise ValueError("cursor scope does not match this operation and compare run")
        unsigned = {key: parsed[key] for key in required if key != "seal"}
        if not hmac.compare_digest(parsed["seal"], self._cursor_seal(unsigned)):
            raise ValueError("cursor corruption seal mismatch")
        return parsed["after_pointer"], parsed["after_id"], parsed["after_ordinal"]

    def list_match_sets(self, *, limit: int = 50, cursor: str | None = None) -> dict[str, Any]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        after_pointer, after_id, after_ordinal = self._decode_cursor(cursor)
        def query() -> dict[str, Any]:
            base_sql = """FROM comparison_match_set ms
                     JOIN comparison_match_anchor a ON a.match_set_id=ms.id AND a.compare_run_id=ms.compare_run_id AND a.side='baseline'
                     JOIN passage_occurrence ba ON ba.passage_occurrence_id=a.passage_occurrence_id AND ba.ingest_run_id=a.ingest_run_id AND ba.evidence_container_id=a.evidence_container_id
                    WHERE ms.compare_run_id=?"""
            if after_id:
                exact = int(self._db.conn.execute(
                    "SELECT COUNT(*) " + base_sql + " AND ba.source_pointer=? AND ms.id=?",
                    (self.compare_run_id, after_pointer, after_id)).fetchone()[0])
                ordinal = int(self._db.conn.execute(
                    "SELECT COUNT(*) " + base_sql
                    + " AND (ba.source_pointer<? OR (ba.source_pointer=? AND ms.id<=?))",
                    (self.compare_run_id, after_pointer, after_pointer, after_id)).fetchone()[0])
                if exact != 1 or ordinal != after_ordinal:
                    raise ValueError("cursor boundary is not an exact physical match-set row")
            rows = self._db.conn.execute(
                """SELECT ms.id,ba.source_pointer
                     FROM comparison_match_set ms
                     JOIN comparison_match_anchor a ON a.match_set_id=ms.id AND a.compare_run_id=ms.compare_run_id AND a.side='baseline'
                     JOIN passage_occurrence ba ON ba.passage_occurrence_id=a.passage_occurrence_id AND ba.ingest_run_id=a.ingest_run_id AND ba.evidence_container_id=a.evidence_container_id
                    WHERE ms.compare_run_id=? AND (ba.source_pointer>? OR (ba.source_pointer=? AND ms.id>?))
                    ORDER BY ba.source_pointer,ms.id LIMIT ?""",
                (self.compare_run_id, after_pointer, after_pointer, after_id, limit + 1)).fetchall()
            page = rows[:limit]
            items = [self._match_set(str(row[0]), include_text=False) for row in page]
            next_cursor = None
            if len(rows) > limit and page:
                next_cursor = self._cursor(str(page[-1][1]), str(page[-1][0]),
                                           after_ordinal + len(page), self.compare_run_id)
            return _envelope(self.compare_run_id, {"items": items, "next_cursor": next_cursor, "limit": limit})
        return self._call(query)

    def get_match_set(self, match_set_id: str) -> dict[str, Any]:
        if not isinstance(match_set_id, str) or not match_set_id:
            raise ValueError("match_set_id must be a non-empty string")
        return self._call(lambda: _envelope(self.compare_run_id,
                                             self._match_set(match_set_id, include_text=True)))

    def _match_set(self, match_set_id: str, *, include_text: bool) -> dict[str, Any]:
        match = self._db.conn.execute(
            "SELECT * FROM comparison_match_set WHERE id=? AND compare_run_id=?",
            (match_set_id, self.compare_run_id)).fetchone()
        if match is None:
            raise TalkComparisonNotFound(match_set_id)
        anchors: dict[str, Any] = {}
        for row in self._db.conn.execute(
            """SELECT a.side,a.ingest_run_id,a.passage_occurrence_id,a.evidence_container_id,
                      p.source_record_id,p.source_pointer,p.namespace,p.talk_id,p.node_id,
                      p.sequence_key,p.raw_checksum,p.normalized_checksum,p.metadata_json
                 FROM comparison_match_anchor a JOIN passage_occurrence p
                   ON p.passage_occurrence_id=a.passage_occurrence_id
                  AND p.ingest_run_id=a.ingest_run_id AND p.evidence_container_id=a.evidence_container_id
                WHERE a.match_set_id=? AND a.compare_run_id=? ORDER BY a.side""",
            (match_set_id, self.compare_run_id)):
            anchor = {
                "side": row[0], "ingest_run_id": row[1], "passage_occurrence_id": row[2],
                "evidence_container_id": row[3], "source_record_id": row[4],
                "source_pointer": row[5], "namespace": row[6], "talk_id": row[7],
                "node_id": row[8], "sequence_key": row[9], "raw_checksum": row[10],
                "normalized_checksum": row[11], "metadata": _json_column(row[12]),
            }
            if include_text:
                anchor["texts"] = [
                    {"id": text[0], "language": text[1], "raw_text": text[2],
                     "normalized_text": text[3], "source_record_id": text[4],
                     "raw_checksum": text[5], "normalized_checksum": text[6]}
                    for text in self._db.conn.execute(
                        """SELECT id,language,raw_text,normalized_text,source_record_id,raw_checksum,normalized_checksum
                             FROM passage_occurrence_text
                            WHERE passage_occurrence_id=? AND ingest_run_id=? AND evidence_container_id=?
                            ORDER BY language,id""", (row[2], row[1], row[3]))
                ]
            anchors[str(row[0])] = anchor
        candidates = [{
            "id": row[0], "candidate_kind": row[1], "rank": row[2],
            "confidence": row[3], "features": _json_column(row[4]),
            "justification": row[5], "provenance": _json_column(row[6]),
        } for row in self._db.conn.execute(
            "SELECT id,candidate_kind,rank,confidence,features_json,justification,provenance "
            "FROM comparison_match_candidate WHERE match_set_id=? AND compare_run_id=? ORDER BY rank,id",
            (match_set_id, self.compare_run_id))]
        decisions = [{"id": row[0], "candidate_id": row[1], "decision": row[2],
                      "justification": row[3], "provenance": _json_column(row[4]),
                      "created_at": row[5]} for row in self._db.conn.execute(
            "SELECT id,candidate_id,decision,justification,provenance,created_at FROM comparison_match_decision "
            "WHERE match_set_id=? AND compare_run_id=? ORDER BY id", (match_set_id, self.compare_run_id))]
        observations = []
        for row in self._db.conn.execute(
            "SELECT id,presence_outcome,observation_status,absence_assertion_id,absence_missing_side,evidence_json,provenance "
            "FROM comparison_change_observation WHERE match_set_id=? AND compare_run_id=? ORDER BY id",
            (match_set_id, self.compare_run_id)):
            labels = [str(item[0]) for item in self._db.conn.execute(
                "SELECT label FROM comparison_change_label WHERE observation_id=? AND compare_run_id=? AND match_set_id=? ORDER BY label",
                (row[0], self.compare_run_id, match_set_id))]
            observations.append({"id": row[0], "presence_outcome": row[1],
                                 "observation_status": row[2], "absence_assertion_id": row[3],
                                 "absence_missing_side": row[4], "evidence": _json_column(row[5]),
                                 "provenance": _json_column(row[6]), "labels": labels})
        return {
            "match_set": {"id": match[0], "resolution_status": match[2], "method": match[3],
                          "algorithm_version": match[4], "justification": match[5],
                          "provenance": _json_column(match[6])},
            "anchors": anchors, "candidates": candidates, "decisions": decisions,
            "observations": observations, "logical_identity_created": False,
            "container_scope": "single_match_set_physical_comparison_container",
        }


__all__ = ["TalkComparisonNotFound", "TalkComparisonRepository",
           "TalkComparisonRepositoryError"]
