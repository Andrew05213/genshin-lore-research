from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

from .artifact_io import (
    normalized_path,
    require_existing_file,
    require_new_output,
    write_json_exclusive,
)
from .db import Database
from .pipeline import TEXTMAP_GOLD_QUERY, ingest_full_quest, ingest_phase2, ingest_slice
from .discovery import build_talk_index, dialog_identity_audit, talk_scope_audit
from .errors import ManifestError
from .retrieval import compare_languages, exact_search, get_context, get_document, get_passage, get_quest_traversal
from .source import SourceBundle, load_manifest, snapshot_id
from .quest_evidence import (
    ingest_quest_7008_complete_evidence_bundle,
    ingest_quest_7008_evidence_slice,
)
from .quest_corpus_evidence import (
    ingest_quest_corpus_evidence_bundle,
)
from .quest_corpus_maintenance import (
    QuestCorpusVerificationError,
    verify_quest_corpus_evidence_db,
)
from .static_book1_evidence import ingest_static_book1_evidence_bundle
from .static_book1_maintenance import (
    StaticBook1VerificationError,
    verify_static_book1_evidence_db,
)
from .static_phase4b2_evidence import ingest_static_phase4b2_evidence_bundle
from .static_phase4b2_maintenance import (
    StaticPhase4B2VerificationError,
    verify_static_phase4b2_evidence_db,
)
from .static_phase4b3_evidence import (
    Phase4B3EvidenceError,
    ingest_static_phase4b3_evidence_bundle,
)
from .static_phase4b3_maintenance import (
    Phase4B3VerificationError,
    verify_static_phase4b3_evidence_db,
)
from .static_phase4b5_reliquary_evidence import (
    Phase4B5EvidenceError,
    ingest_static_phase4b5_reliquary_evidence_bundle,
)
from .static_phase4b5_reliquary_maintenance import (
    Phase4B5VerificationError,
    verify_static_phase4b5_reliquary_evidence_db,
)
from .static_phase4b6_material_evidence import (
    Phase4B6EvidenceError,
    ingest_static_phase4b6_material_evidence_bundle,
)
from .static_phase4b6_material_maintenance import (
    Phase4B6VerificationError,
    verify_static_phase4b6_material_evidence_db,
)
from .character_phase4c2_evidence import (
    Phase4C2EvidenceError,
    ingest_character_phase4c2_evidence_bundle,
)
from .character_phase4c2_maintenance import (
    Phase4C2VerificationError,
    verify_character_phase4c2_evidence_db,
)
from .character_phase4c4b_evidence import (
    Phase4C4BEvidenceError,
    ingest_character_phase4c4b_evidence_bundle,
)
from .character_phase4c4b_maintenance import (
    Phase4C4BVerificationError,
    verify_character_phase4c4b_evidence_db,
)
from ._readonly_database import open_readonly_database
from .character_retrieval import (
    CharacterNotFound,
    get_character_row_evidence,
    get_character_voice_container,
    get_character_voice_context,
    list_character_rows,
    search_character_text_evidence,
)
from .tutorial_retrieval import (
    TutorialNotFound,
    get_tutorial_context,
    get_tutorial_row_evidence,
    list_tutorial_rows,
    search_tutorial_text_evidence,
)
from .wave1_rebuild_maintenance import (
    Wave1RebuildVerificationError,
    verify_wave1_full_rebuild_db,
)
from .character_phase4c5b_evidence import Phase4C5BEvidenceError, ingest_character_phase4c5b_evidence_bundle
from .character_phase4c5b_maintenance import Phase4C5BVerificationError, verify_character_phase4c5b_evidence_db
from .tutorial_phase4d2a_evidence import (
    TutorialPhase4D2AEvidenceError,
    ingest_tutorial_phase4d2a_evidence_bundle,
)
from .tutorial_phase4d2a_maintenance import (
    TutorialPhase4D2AVerificationError,
    verify_tutorial_phase4d2a_evidence_db,
)
from .tutorial_phase4d2b_evidence import (
    TutorialPhase4D2BEvidenceError,
    ingest_tutorial_phase4d2b_evidence_bundle,
)
from .tutorial_phase4d2b_maintenance import (
    TutorialPhase4D2BVerificationError,
    verify_tutorial_phase4d2b_evidence_db,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = PROJECT_ROOT / "manifests/sources/anime-game-data-7.0.json"
DEFAULT_ROOTS = [PROJECT_ROOT / "cache/upstream-agd-git", PROJECT_ROOT / "cache/samples", PROJECT_ROOT / "cache/AnimeGameData"]


def emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def roots_from(args: argparse.Namespace) -> list[str | Path]:
    return args.source_root if args.source_root else DEFAULT_ROOTS


def _normalized_output_path(path: str | Path) -> str:
    return normalized_path(path)


def _validate_aq_build_paths(args: argparse.Namespace) -> tuple[Path, Path | None]:
    database = require_new_output(args.db, "AQ database")
    require_existing_file(args.manifest, "AQ source manifest")
    require_existing_file(args.corpus_manifest, "AQ corpus manifest")
    require_existing_file(args.audit_cache, "AQ discovery cache")
    for root in roots_from(args):
        if not Path(root).is_dir():
            raise ValueError(f"AQ source root is not a directory: {root}")
    report = None
    if args.report:
        report = require_new_output(
            args.report,
            "AQ build report",
            aliases=(database, args.manifest, args.corpus_manifest, args.audit_cache),
        )
    return database, report


def _validate_aq_verify_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    database = require_existing_file(args.db, "AQ database")
    build_report = require_existing_file(args.build_report, "AQ build report")
    corpus_manifest = require_existing_file(args.corpus_manifest, "AQ corpus manifest")
    if not args.report:
        raise ValueError("AQ verification requires an exclusive --report path")
    report = require_new_output(
        args.report,
        "AQ verification report",
        aliases=(database, build_report, corpus_manifest),
    )
    return database, report


def _validate_b1_build_paths(args: argparse.Namespace) -> Path | None:
    inputs = (
        require_existing_file(args.manifest, "Book1 source manifest"),
        require_existing_file(args.book1_manifest, "Book1 sealed manifest"),
    )
    database = Path(args.db)
    if database.is_symlink() or (database.exists() and not database.is_file()):
        raise ValueError(f"Book1 database is not a regular file target: {database}")
    if any(normalized_path(database) == normalized_path(item) for item in inputs):
        raise ValueError("static Book1 database aliases a manifest input")
    if not args.report:
        return None
    if normalized_path(database) == normalized_path(args.report):
        raise ValueError(
            "static Book1 database and report must not resolve to the same file"
        )
    return require_new_output(
        args.report,
        "static Book1 build report",
        aliases=(database, *inputs),
    )


def _validate_b1_verify_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    database = require_existing_file(args.db, "Book1 database")
    build_report = require_existing_file(args.build_report, "Book1 build report")
    source_manifest = require_existing_file(args.manifest, "Book1 source manifest")
    book1_manifest = require_existing_file(args.book1_manifest, "Book1 sealed manifest")
    source_root = Path(args.source_root)
    if not source_root.is_dir():
        raise ValueError(f"Book1 source root is not a directory: {source_root}")
    report = require_new_output(
        args.report,
        "Book1 verification report",
        aliases=(database, build_report, source_manifest, book1_manifest),
    )
    return database, report


def _validate_wave1_verify_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    database = require_existing_file(args.db, "Wave1 final database")
    inputs = (
        require_existing_file(args.source_manifest, "Wave1 source manifest"),
        require_existing_file(args.audit_cache, "Wave1 AQ audit cache"),
        require_existing_file(args.corpus_manifest, "Wave1 AQ corpus manifest"),
        require_existing_file(args.book1_manifest, "Wave1 Book1 manifest"),
        require_existing_file(args.phase4b2_manifest, "Wave1 Phase 4B2 manifest"),
        require_existing_file(args.phase4b3_manifest, "Wave1 Phase 4B3 manifest"),
        require_existing_file(args.phase4b5_manifest, "Wave1 Phase 4B5 manifest"),
        require_existing_file(args.phase4b6_manifest, "Wave1 Phase 4B6 manifest"),
        require_existing_file(args.phase4c2_manifest, "Wave1 Phase 4C2 manifest"),
    )
    source_root = Path(args.source_root)
    manifest_root = Path(args.manifest_root)
    if not source_root.is_dir():
        raise ValueError(f"Wave1 source root is not a directory: {source_root}")
    if not manifest_root.is_dir():
        raise ValueError(f"Wave1 manifest root is not a directory: {manifest_root}")
    report = require_new_output(
        args.report,
        "Wave1 full rebuild verification report",
        aliases=(database, *inputs),
    )
    resolved_source_root = source_root.resolve()
    resolved_report = report.parent.resolve(strict=True) / report.name
    if (
        resolved_report == resolved_source_root
        or resolved_source_root in resolved_report.parents
    ):
        raise ValueError(
            "Wave1 verification report must be outside the sealed source root"
        )
    return database, report


def _validate_phase4b6_cli_paths(
    args: argparse.Namespace,
    *,
    build_report: bool = False,
) -> tuple[Path, Path, Path, Path, Path | None]:
    """Validate Phase 4B6 CLI paths before dispatching to the public APIs.

    The B6 builder owns semantic/source preflight and requires an existing
    schema-9 database to append to.  This boundary only rejects malformed
    filesystem arguments so a missing or aliased output can never cause the
    API (or report writer) to create a target accidentally.
    """

    source_root = Path(args.source_root)
    manifest = Path(args.manifest)
    database = Path(args.db)
    report = Path(args.report)
    input_build_report = Path(args.build_report) if build_report else None

    if _normalized_output_path(database) == _normalized_output_path(report):
        phase = "verification" if build_report else "build"
        raise ValueError(
            f"static Phase 4B6 database and {phase} report must not resolve to the same file"
        )
    if report.exists():
        phase = "verification" if build_report else "build"
        raise ValueError(
            f"static Phase 4B6 {phase} report already exists: {report}"
        )
    if not source_root.is_dir():
        raise ValueError(f"static Phase 4B6 source root is not a directory: {source_root}")
    if not manifest.is_file():
        raise ValueError(f"static Phase 4B6 manifest does not exist as a file: {manifest}")
    if not database.is_file():
        raise ValueError(
            "static Phase 4B6 --db must be an existing schema-9 database "
            f"(input/output target): {database}"
        )
    if input_build_report is not None and not input_build_report.is_file():
        raise ValueError(
            f"static Phase 4B6 build report does not exist as a file: {input_build_report}"
        )

    # A pre-existing non-directory parent would make the post-build report
    # write fail after the database API has run.  Reject it before dispatch.
    parent = report.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not parent.is_dir():
        raise ValueError(f"static Phase 4B6 report parent is not a directory: {report.parent}")

    return source_root, manifest, database, report, input_build_report


def _write_cli_report(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _validate_phase4c2_cli_paths(
    args: argparse.Namespace,
    *,
    verification: bool = False,
) -> tuple[Path, Path, Path, Path, Path | None]:
    source_root = Path(args.source_root)
    manifest = Path(args.manifest)
    database = Path(args.db)
    report = Path(args.report)
    build_report = Path(args.build_report) if verification else None
    if _normalized_output_path(database) == _normalized_output_path(report):
        raise ValueError("Phase 4C2 database and report must not resolve to the same file")
    if report.exists():
        raise ValueError(f"Phase 4C2 report already exists: {report}")
    if not source_root.is_dir():
        raise ValueError(f"Phase 4C2 source root is not a directory: {source_root}")
    if not manifest.is_file():
        raise ValueError(f"Phase 4C2 manifest does not exist as a file: {manifest}")
    if not database.is_file():
        raise ValueError(f"Phase 4C2 --db must be an existing combined evidence database copy: {database}")
    if build_report is not None and not build_report.is_file():
        raise ValueError(f"Phase 4C2 build report does not exist as a file: {build_report}")
    parent = report.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not parent.is_dir():
        raise ValueError(f"Phase 4C2 report parent is not a directory: {report.parent}")
    return source_root, manifest, database, report, build_report


def _validate_phase4c4b_cli_paths(
    args: argparse.Namespace,
    *,
    verification: bool = False,
) -> tuple[Path, Path, Path, Path, Path | None]:
    source_root = Path(args.source_root)
    manifest = Path(args.manifest)
    database = Path(args.db)
    report = Path(args.report)
    build_report = Path(args.build_report) if verification else None
    if _normalized_output_path(database) == _normalized_output_path(report):
        raise ValueError("Phase 4C4B database and report must not resolve to the same file")
    if report.exists():
        raise ValueError(f"Phase 4C4B report already exists: {report}")
    if not source_root.is_dir():
        raise ValueError(f"Phase 4C4B source root is not a directory: {source_root}")
    if not manifest.is_file():
        raise ValueError(f"Phase 4C4B manifest does not exist as a file: {manifest}")
    if not database.is_file():
        raise ValueError(f"Phase 4C4B --db must be an existing combined evidence database copy: {database}")
    if build_report is not None and not build_report.is_file():
        raise ValueError(f"Phase 4C4B build report does not exist as a file: {build_report}")
    parent = report.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not parent.is_dir():
        raise ValueError(f"Phase 4C4B report parent is not a directory: {report.parent}")
    return source_root, manifest, database, report, build_report

def _validate_phase4c5b_cli_paths(
    args: argparse.Namespace,
    *,
    verification: bool = False,
) -> tuple[Path, Path, Path, Path, Path | None]:
    source_root = Path(args.source_root)
    manifest = Path(args.manifest)
    database = Path(args.db)
    report = Path(args.report)
    build_report = Path(args.build_report) if verification else None
    if _normalized_output_path(database) == _normalized_output_path(report):
        raise ValueError("Phase 4C5B database and report must not resolve to the same file")
    if report.exists():
        raise ValueError(f"Phase 4C5B report already exists: {report}")
    if not source_root.is_dir():
        raise ValueError(f"Phase 4C5B source root is not a directory: {source_root}")
    if not manifest.is_file():
        raise ValueError(f"Phase 4C5B manifest does not exist as a file: {manifest}")
    if not database.is_file():
        raise ValueError(f"Phase 4C5B --db must be an existing Schema 9 private database copy: {database}")
    if build_report is not None and not build_report.is_file():
        raise ValueError(f"Phase 4C5B build report does not exist as a file: {build_report}")
    parent = report.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    if not parent.is_dir():
        raise ValueError(f"Phase 4C5B report parent is not a directory: {report.parent}")
    return source_root, manifest, database, report, build_report

def _dialog_passage_id(db: Database, namespace: str, talk_id: int | str, node_id: int | str) -> str:
    row = db.conn.execute(
        """SELECT p.stable_id
           FROM passage p JOIN document d ON d.stable_id=p.document_id
           WHERE d.content_type='talk' AND d.native_namespace=?
             AND d.native_id=? AND p.native_id=?
           ORDER BY p.stable_id LIMIT 1""",
        (namespace, str(talk_id), str(node_id)),
    ).fetchone()
    if row is None:
        raise KeyError(f"dialog passage not found: {namespace}/{talk_id}/{node_id}")
    return str(row[0])


def run_gold_tests(db_path: str | Path) -> dict[str, Any]:
    failures: list[str] = []
    checks: dict[str, Any] = {}
    with Database(db_path) as db:
        snapshot = db.conn.execute("SELECT id FROM source_snapshot WHERE status='active' ORDER BY observed_at DESC LIMIT 1").fetchone()
        if snapshot is None:
            failures.append("no active snapshot")
            return {"passed": False, "failures": failures, "checks": checks}
        snapshot_id_value = str(snapshot[0])
        conflicts = int(db.conn.execute("SELECT COUNT(*) FROM textmap_conflict WHERE snapshot_id=?", (snapshot_id_value,)).fetchone()[0])
        checks["textmap_conflicts"] = conflicts
        if conflicts != 0:
            failures.append(f"expected zero TextMap conflicts, got {conflicts}")
        stages = int(db.conn.execute("SELECT COUNT(*) FROM document_relation WHERE to_document_id=? AND relation_type='stage_of'", ("agd:quest:7008",)).fetchone()[0])
        talks = int(db.conn.execute("SELECT COUNT(*) FROM document_relation WHERE to_document_id=? AND relation_type='talk_of'", ("agd:quest:7008",)).fetchone()[0])
        checks["quest_7008_subquests"] = stages
        checks["quest_7008_talk_references"] = talks
        if stages != 19:
            failures.append(f"expected 19 subquests, got {stages}")
        if talks != 15:
            failures.append(f"expected 15 talk references, got {talks}")
        talk_document = db.conn.execute(
            "SELECT stable_id FROM document WHERE content_type='talk' AND native_namespace='QUEST' AND native_id='700814'"
        ).fetchone()
        if talk_document is None:
            failures.append("Quest Talk 700814 document not found")
            return {"passed": False, "failures": failures, "checks": checks}
        talk_document_id = str(talk_document[0])
        branch_id = _dialog_passage_id(db, "QUEST", 700814, 70081408)
        merge_id = _dialog_passage_id(db, "QUEST", 700814, 70081411)
        talk_nodes = int(db.conn.execute("SELECT COUNT(*) FROM passage WHERE document_id=?", (talk_document_id,)).fetchone()[0])
        checks["talk_700814_dialog_nodes"] = talk_nodes
        if talk_nodes != 14:
            failures.append(f"expected 14 dialog nodes, got {talk_nodes}")
        context = get_context(db, branch_id, before=2, after=2, snapshot_id=snapshot_id_value)
        checks["talk_700814_branch_nodes"] = context["branch_nodes"]
        checks["talk_700814_merge_nodes"] = context["merge_nodes"]
        if context["branch_nodes"] != [branch_id]:
            failures.append(f"unexpected branch nodes: {context['branch_nodes']}")
        if context["merge_nodes"] != [merge_id]:
            failures.append(f"unexpected merge nodes: {context['merge_nodes']}")
        multilingual = 0
        for row in db.conn.execute("SELECT stable_id FROM passage WHERE document_id=?", (talk_document_id,)):
            languages = {text[0] for text in db.conn.execute("SELECT language FROM passage_text WHERE passage_id=? AND snapshot_id=?", (row[0], snapshot_id_value)).fetchall()}
            if languages == {"CHS", "EN", "JP"}:
                multilingual += 1
        checks["talk_700814_three_language_nodes"] = multilingual
        if multilingual != 14:
            failures.append(f"expected 14 three-language dialog nodes, got {multilingual}")
        exact_query_chs = exact_search(db, TEXTMAP_GOLD_QUERY, snapshot_id_value, language="CHS")
        exact_query_all = exact_search(db, TEXTMAP_GOLD_QUERY, snapshot_id_value)
        checks["exact_textmap_query_hits_chs"] = len(exact_query_chs)
        checks["exact_textmap_query_hits_all_languages"] = len(exact_query_all)
        if len(exact_query_chs) != 9:
            failures.append(f"expected 9 exact CHS 燃珀 hits, got {len(exact_query_chs)}")
        subtitle_hits = exact_search(db, "死神，你可真是个令人恼火的家伙", snapshot_id_value)
        checks["exact_subtitle_hits"] = len(subtitle_hits)
        if len(subtitle_hits) < 1 or not any(hit.get("evidence_type") == "passage" and hit.get("document_id", "").startswith("agd:subtitle:") for hit in subtitle_hits):
            failures.append("subtitle exact search did not return a subtitle passage")
        readable_status = {
            stem: db.conn.execute("SELECT status FROM document WHERE stable_id=?", (f"agd:readable:{stem}",)).fetchone()[0]
            for stem in ("Book1", "Weapon15516", "Relic15048_1", "Book1257")
            if db.conn.execute("SELECT 1 FROM document WHERE stable_id=?", (f"agd:readable:{stem}",)).fetchone()
        }
        checks["readable_status"] = readable_status
        if readable_status.get("Book1257") != "placeholder":
            failures.append(f"Book1257 status is not placeholder: {readable_status.get('Book1257')}")
        for stem in ("Book1", "Weapon15516", "Relic15048_1"):
            if readable_status.get(stem) != "active":
                failures.append(f"{stem} was not imported as active readable")
        checks["fallbacks"] = []
    return {"passed": not failures, "failures": failures, "checks": checks}


def run_phase2_gold_tests(db_path: str | Path) -> dict[str, Any]:
    """Gold contract for the deterministic Talk discovery slice."""

    failures: list[str] = []
    checks: dict[str, Any] = {}
    with Database(db_path) as db:
        snapshot = db.conn.execute("SELECT id FROM source_snapshot WHERE status='active' ORDER BY observed_at DESC LIMIT 1").fetchone()
        if snapshot is None:
            return {"passed": False, "failures": ["no active snapshot"], "checks": checks}
        snapshot_id_value = str(snapshot[0])
        talk_documents = db.conn.execute(
            """SELECT DISTINCT d.stable_id, d.native_namespace, d.native_id
               FROM document d JOIN document_relation dr ON dr.from_document_id=d.stable_id
               WHERE dr.to_document_id='agd:quest:7008' AND dr.relation_type='talk_of'
               ORDER BY CAST(d.native_id AS INTEGER), d.stable_id"""
        ).fetchall()
        refs = [int(row[2]) for row in talk_documents]
        talk_document_ids = [str(row[0]) for row in talk_documents]
        checks["quest_7008_talk_ids"] = refs
        if len(refs) != 15:
            failures.append(f"expected 15 Quest 7008 Talk references, got {len(refs)}")
        statuses = {int(row[2]): db.conn.execute("SELECT status FROM document WHERE stable_id=?", (row[0],)).fetchone()[0] for row in talk_documents}
        checks["quest_7008_talk_statuses"] = statuses
        if any(status != "active" for status in statuses.values()):
            failures.append(f"not all Quest 7008 Talks are active: {statuses}")
        source_rows = db.conn.execute(
            """SELECT d.native_id, d.status, sf.relative_path, sr.record_kind, sr.native_id AS record_native_id
               FROM document d JOIN document_version dv ON dv.document_id=d.stable_id AND dv.snapshot_id=?
               JOIN source_record sr ON sr.id=dv.source_record_id JOIN source_file sf ON sf.id=sr.source_file_id
               WHERE d.stable_id IN (%s)
               ORDER BY CAST(d.native_id AS INTEGER), d.stable_id""" % ",".join("?" for _ in talk_document_ids),
            (snapshot_id_value, *talk_document_ids),
        ).fetchall()
        checks["talk_source_records"] = [dict(row) for row in source_rows]
        if len(source_rows) != 15 or any(row["record_kind"] != "talk_file" or not row["relative_path"].startswith("BinOutput/Talk/") or row["native_id"] != row["record_native_id"] for row in source_rows):
            failures.append("resolved Talk document versions do not have matching native-ID raw Talk provenance")
        language_counts = {
            int(row[0]): int(row[1])
            for row in db.conn.execute(
                """SELECT d.native_id, COUNT(pt.id) FROM document d JOIN passage p ON p.document_id=d.stable_id
                   JOIN passage_text pt ON pt.passage_id=p.stable_id AND pt.snapshot_id=?
                   WHERE d.stable_id IN (%s) GROUP BY d.stable_id""" % ",".join("?" for _ in talk_document_ids),
                (snapshot_id_value, *talk_document_ids),
            ).fetchall()
        }
        checks["talk_language_text_counts"] = language_counts
        expected_nodes = {int(row[0]): int(row[1]) for row in db.conn.execute("SELECT d.native_id, COUNT(p.stable_id) FROM document d JOIN passage p ON p.document_id=d.stable_id WHERE d.stable_id IN (%s) GROUP BY d.stable_id" % ",".join("?" for _ in talk_document_ids), talk_document_ids)}
        checks["talk_node_counts"] = expected_nodes
        if any(language_counts.get(talk_id, 0) != expected_nodes.get(talk_id, 0) * 3 for talk_id in refs):
            failures.append("one or more resolved Talks are not aligned across CHS/EN/JP")
        branch_id = _dialog_passage_id(db, "QUEST", 700814, 70081408)
        merge_id = _dialog_passage_id(db, "QUEST", 700814, 70081411)
        context = get_context(db, branch_id, before=2, after=2, snapshot_id=snapshot_id_value)
        checks["talk_700814_branch_nodes"] = context["branch_nodes"]
        checks["talk_700814_merge_nodes"] = context["merge_nodes"]
        if context["branch_nodes"] != [branch_id] or context["merge_nodes"] != [merge_id]:
            failures.append("Talk 700814 branch/merge regression")
        checks["discovery_issues"] = {
            code: int(db.conn.execute("SELECT COUNT(*) FROM ingest_issue WHERE snapshot_id=? AND code=?", (snapshot_id_value, code)).fetchone()[0])
            for code in ("talk_raw_ambiguous", "talk_raw_unresolved", "talk_raw_genuinely_absent", "talk_discovery_revalidation_failed")
        }
        if any(value for value in checks["discovery_issues"].values()):
            failures.append(f"Phase 2 wrote unresolved/ambiguous Talk data as an active reference: {checks['discovery_issues']}")
    return {"passed": not failures, "failures": failures, "checks": checks}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="genshin-lore")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("init-db", "migrate"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--db", required=True)

    inspect = sub.add_parser("inspect-source")
    inspect.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    inspect.add_argument("--source-root", action="append")

    identity = sub.add_parser("audit-dialog-identity")
    identity.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    identity.add_argument("--source-root", action="append")
    identity.add_argument("--discovery-cache-dir", default=str(PROJECT_ROOT / "cache/discovery"))
    identity.add_argument("--report")

    scope = sub.add_parser("audit-talk-scope")
    scope.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    scope.add_argument("--source-root", action="append")
    scope.add_argument("--discovery-cache-dir", default=str(PROJECT_ROOT / "cache/discovery"))
    scope.add_argument("--report")

    ingest = sub.add_parser("ingest-slice")
    ingest.add_argument("--db", required=True)
    ingest.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ingest.add_argument("--source-root", action="append")
    ingest.add_argument("--report")

    ingest2 = sub.add_parser("ingest-phase2")
    ingest2.add_argument("--db", required=True)
    ingest2.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ingest2.add_argument("--source-root", action="append")
    ingest2.add_argument("--discovery-cache-dir", default=str(PROJECT_ROOT / "cache/discovery"))
    ingest2.add_argument("--report")

    evidence7008 = sub.add_parser("build-quest7008-evidence-db")
    evidence7008.add_argument("--db", required=True)
    evidence7008.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    evidence7008.add_argument("--source-root", action="append")
    evidence7008.add_argument("--candidate-scope-manifest")
    evidence7008.add_argument("--audit-cache")
    evidence7008.add_argument("--report")

    evidence7008_complete = sub.add_parser("build-quest7008-complete-evidence-db")
    evidence7008_complete.add_argument("--db", required=True)
    evidence7008_complete.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    evidence7008_complete.add_argument("--source-root", action="append")
    evidence7008_complete.add_argument("--bundle-manifest")
    evidence7008_complete.add_argument("--audit-cache")
    evidence7008_complete.add_argument("--report")

    evidence_corpus = sub.add_parser("build-quest-corpus-evidence-db")
    evidence_corpus.add_argument("--db", required=True)
    evidence_corpus.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    evidence_corpus.add_argument("--source-root", action="append")
    evidence_corpus.add_argument(
        "--corpus-manifest",
        default=str(PROJECT_ROOT / "manifests/corpora/anime-game-data-7.0-aq-7000-7014.json"),
    )
    evidence_corpus.add_argument(
        "--audit-cache",
        default=str(PROJECT_ROOT / "cache/discovery/talk-index-anime-game-data_7_0_26df1dfbdf05-c7f76e60ca30ebdb683e.json"),
    )
    evidence_corpus.add_argument("--manifest-root", default=str(PROJECT_ROOT))
    evidence_corpus.add_argument("--report")

    static_book1 = sub.add_parser("build-static-book1-evidence-db")
    static_book1.add_argument("--db", required=True)
    static_book1.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    static_book1.add_argument("--source-root", action="append")
    static_book1.add_argument(
        "--book1-manifest",
        default=str(
            PROJECT_ROOT
            / "manifests/static/anime-game-data-7.0-book1-canary.json"
        ),
    )
    static_book1.add_argument("--report")

    static_phase4b2 = sub.add_parser("build-static-phase4b2-evidence-db")
    static_phase4b2.add_argument("--db", required=True)
    static_phase4b2.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    static_phase4b2.add_argument("--source-root", action="append")
    static_phase4b2.add_argument(
        "--phase4b2-manifest",
        default=str(
            PROJECT_ROOT
            / "manifests/static/anime-game-data-7.0-phase4b2-multifamily-canary.json"
        ),
    )
    static_phase4b2.add_argument("--report")

    static_phase4b3 = sub.add_parser("build-static-phase4b3-evidence-db")
    static_phase4b3.add_argument("--db", required=True)
    static_phase4b3.add_argument("--manifest", required=True)
    static_phase4b3.add_argument("--source-root", required=True)
    static_phase4b3.add_argument("--report")

    static_phase4b5 = sub.add_parser("build-static-phase4b5-reliquary-evidence-db")
    static_phase4b5.add_argument("--db", required=True)
    static_phase4b5.add_argument("--manifest", required=True)
    static_phase4b5.add_argument("--source-root", required=True)
    static_phase4b5.add_argument("--report")

    static_phase4b6 = sub.add_parser("build-static-phase4b6-material-evidence-db")
    static_phase4b6.add_argument("--source-root", required=True)
    static_phase4b6.add_argument("--manifest", required=True)
    static_phase4b6.add_argument("--db", required=True)
    static_phase4b6.add_argument("--report", required=True)

    character_phase4c2 = sub.add_parser(
        "build-character-phase4c2-canary-evidence-db",
        help=(
            "POSIX-only Character canary builder; requires an "
            "offline DELETE-journal database copy"
        ),
        description=(
            "POSIX-only Phase 4C2 Character canary builder. "
            "The input must be an offline, process-unique SQLite "
            "copy in DELETE journal mode with no sidecars."
        ),
    )
    character_phase4c2.add_argument("--source-root", required=True)
    character_phase4c2.add_argument("--manifest", required=True)
    character_phase4c2.add_argument("--db", required=True)
    character_phase4c2.add_argument("--report", required=True)

    character_phase4c4b = sub.add_parser(
        "build-character-phase4c4b-bounded-evidence-db",
        help=(
            "POSIX-only exact-six builder; --db is an existing "
            "process-unique offline private copy"
        ),
        description=(
            "POSIX-only Phase 4C4B exact-six bounded Character projection. "
            "The existing --db must be a process-unique offline private copy. "
            "The builder requires DELETE journal mode and no -journal/-wal/-shm "
            "sidecars, and atomically replaces that private copy only on "
            "success. This separately bound canary run does not change C2."
        ),
    )
    character_phase4c4b.add_argument("--source-root", required=True)
    character_phase4c4b.add_argument("--manifest", required=True)
    character_phase4c4b.add_argument("--db", required=True)
    character_phase4c4b.add_argument("--report", required=True)

    character_phase4c5b = sub.add_parser(
        "build-character-phase4c5b-full-evidence-db",
        help="POSIX-only full Character boundary builder for a private Schema 9 copy",
        description=(
            "POSIX-only Phase 4C5B full Character/Fetter closure projection. "
            "--db must be an existing process-unique offline Schema 9 private copy "
            "in DELETE journal mode with no SQLite sidecars. The exact migration "
            "0010 is applied on a temporary copy and atomically replaces --db only "
            "after independent verification; existing artifacts are never edited."
        ),
    )
    character_phase4c5b.add_argument("--source-root", required=True)
    character_phase4c5b.add_argument("--manifest", required=True)
    character_phase4c5b.add_argument("--db", required=True)
    character_phase4c5b.add_argument("--report", required=True)

    tutorial_phase4d2a = sub.add_parser(
        "build-tutorial-phase4d2a-canary-evidence-db",
        help="POSIX-only Schema 11 Core Tutorial raw-evidence canary builder",
        description=(
            "Build the fixed Phase 4D2A Core Tutorial physical-evidence canary "
            "on an existing Schema 10/11 private copy. The builder requires "
            "DELETE journal mode, no SQLite sidecars, and atomically replaces "
            "the copy only after the exact source/SQL contract passes."
        ),
    )
    tutorial_phase4d2a.add_argument("--source-root", required=True)
    tutorial_phase4d2a.add_argument("--manifest", required=True)
    tutorial_phase4d2a.add_argument("--db", required=True)
    tutorial_phase4d2a.add_argument("--report", required=True)

    tutorial_phase4d2b = sub.add_parser(
        "build-tutorial-phase4d2b-full-boundary-evidence-db",
        help="POSIX-only Schema 11 full Core Tutorial physical-evidence builder",
        description=(
            "Build the exact Phase 4D2B full Core Tutorial physical-evidence "
            "boundary on an existing Schema 10/11 private copy. The pinned "
            "Phase 4D1 artifacts and source closure are checked before an "
            "atomic DELETE-journal append; no logical Tutorial or image rows "
            "are created."
        ),
    )
    tutorial_phase4d2b.add_argument("--source-root", required=True)
    tutorial_phase4d2b.add_argument("--manifest", required=True)
    tutorial_phase4d2b.add_argument("--db", required=True)
    tutorial_phase4d2b.add_argument("--report", required=True)

    phase4d5_append = sub.add_parser(
        "append-phase4d5-combined",
        help="Atomically append the accepted D2B Tutorial boundary to a C5C copy",
        description=(
            "Validate the byte-sealed C5C Schema 10 artifact, invoke the existing "
            "atomic Phase 4D2B append once, and independently verify the combined "
            "Schema 11 release. Re-entry is read-only and idempotent."
        ),
    )
    phase4d5_append.add_argument("--source-root", required=True)
    phase4d5_append.add_argument("--manifest", required=True)
    phase4d5_append.add_argument("--db", required=True)
    phase4d5_append.add_argument("--report", required=True)

    phase4d5_verify = sub.add_parser(
        "verify-phase4d5-combined",
        help="Read-only aggregate verifier for the Schema 11 AQ+Static+Character+Tutorial release",
    )
    phase4d5_verify.add_argument("--db", required=True)
    phase4d5_verify.add_argument("--report", required=True)

    verify_corpus = sub.add_parser("verify-quest-corpus-evidence-db")
    verify_corpus.add_argument("--db", required=True)
    verify_corpus.add_argument("--build-report", required=True)
    verify_corpus.add_argument("--corpus-manifest", required=True)
    verify_corpus.add_argument("--manifest-root")
    verify_corpus.add_argument("--report")

    verify_static_book1 = sub.add_parser("verify-static-book1-evidence-db")
    verify_static_book1.add_argument("--db", required=True)
    verify_static_book1.add_argument("--build-report", required=True)
    verify_static_book1.add_argument("--manifest", required=True)
    verify_static_book1.add_argument("--book1-manifest", required=True)
    verify_static_book1.add_argument("--source-root", required=True)
    verify_static_book1.add_argument("--report", required=True)

    verify_static_phase4b2 = sub.add_parser("verify-static-phase4b2-evidence-db")
    verify_static_phase4b2.add_argument("--db", required=True)
    verify_static_phase4b2.add_argument("--build-report", required=True)
    verify_static_phase4b2.add_argument("--phase4b2-manifest", required=True)
    verify_static_phase4b2.add_argument("--report")

    verify_static_phase4b3 = sub.add_parser("verify-static-phase4b3-evidence-db")
    verify_static_phase4b3.add_argument("--db", required=True)
    verify_static_phase4b3.add_argument("--build-report", required=True)
    verify_static_phase4b3.add_argument("--manifest", required=True)
    verify_static_phase4b3.add_argument("--source-root", required=True)
    verify_static_phase4b3.add_argument("--report")

    verify_static_phase4b5 = sub.add_parser("verify-static-phase4b5-reliquary-evidence-db")
    verify_static_phase4b5.add_argument("--db", required=True)
    verify_static_phase4b5.add_argument("--build-report", required=True)
    verify_static_phase4b5.add_argument("--manifest", required=True)
    verify_static_phase4b5.add_argument("--source-root", required=True)
    verify_static_phase4b5.add_argument("--report")

    verify_static_phase4b6 = sub.add_parser("verify-static-phase4b6-material-evidence-db")
    verify_static_phase4b6.add_argument("--source-root", required=True)
    verify_static_phase4b6.add_argument("--manifest", required=True)
    verify_static_phase4b6.add_argument("--db", required=True)
    verify_static_phase4b6.add_argument("--build-report", required=True)
    verify_static_phase4b6.add_argument("--report", required=True)

    verify_character_phase4c2 = sub.add_parser("verify-character-phase4c2-canary-evidence-db")
    verify_character_phase4c2.add_argument("--source-root", required=True)
    verify_character_phase4c2.add_argument("--manifest", required=True)
    verify_character_phase4c2.add_argument("--db", required=True)
    verify_character_phase4c2.add_argument("--build-report", required=True)
    verify_character_phase4c2.add_argument("--report", required=True)

    verify_character_phase4c4b = sub.add_parser(
        "verify-character-phase4c4b-bounded-evidence-db",
        help=(
            "read-only verifier for a process-unique private copy; "
            "rejects SQLite sidecars"
        ),
        description=(
            "Phase 4C4B verifier opens --db read-only, requires DELETE journal "
            "mode and absent -journal/-wal/-shm sidecars, and never writes the "
            "private copy."
        ),
    )
    verify_character_phase4c4b.add_argument("--source-root", required=True)
    verify_character_phase4c4b.add_argument("--manifest", required=True)
    verify_character_phase4c4b.add_argument("--db", required=True)
    verify_character_phase4c4b.add_argument("--build-report", required=True)
    verify_character_phase4c4b.add_argument("--report", required=True)

    verify_character_phase4c5b = sub.add_parser(
        "verify-character-phase4c5b-full-evidence-db",
        help="read-only independent verifier for a full-boundary private artifact",
        description=(
            "Phase 4C5B verifier opens --db read-only, requires exact Schema 10, "
            "DELETE journal mode, absent SQLite sidecars, and the complete build "
            "report binding. It never writes the database."
        ),
    )
    verify_character_phase4c5b.add_argument("--source-root", required=True)
    verify_character_phase4c5b.add_argument("--manifest", required=True)
    verify_character_phase4c5b.add_argument("--report", required=True)
    verify_character_phase4c5b.add_argument("--db", required=True)
    verify_character_phase4c5b.add_argument("--build-report", required=True)

    verify_tutorial_phase4d2a = sub.add_parser(
        "verify-tutorial-phase4d2a-canary-evidence-db",
        help="read-only independent verifier for the Schema 11 Core Tutorial canary",
        description=(
            "Verify the fixed Phase 4D2A canary with a read-only SQLite connection, "
            "exact Schema 11/provenance/occurrence tuples, DELETE journal mode, "
            "and absent SQLite sidecars. The database is never written."
        ),
    )
    verify_tutorial_phase4d2a.add_argument("--source-root", required=True)
    verify_tutorial_phase4d2a.add_argument("--manifest", required=True)
    verify_tutorial_phase4d2a.add_argument("--db", required=True)
    verify_tutorial_phase4d2a.add_argument("--build-report", required=True)
    verify_tutorial_phase4d2a.add_argument("--report", required=True)

    verify_tutorial_phase4d2b = sub.add_parser(
        "verify-tutorial-phase4d2b-full-boundary-evidence-db",
        help="read-only independent verifier for the full Schema 11 Core Tutorial boundary",
        description=(
            "Verify the exact Phase 4D2B full Core Tutorial boundary using a "
            "read-only SQLite connection, pinned source closure, exact SQL "
            "tuples, Schema 11 seal, and build report. The database is never "
            "written."
        ),
    )
    verify_tutorial_phase4d2b.add_argument("--source-root", required=True)
    verify_tutorial_phase4d2b.add_argument("--manifest", required=True)
    verify_tutorial_phase4d2b.add_argument("--db", required=True)
    verify_tutorial_phase4d2b.add_argument("--build-report", required=True)
    verify_tutorial_phase4d2b.add_argument("--report", required=True)

    verify_wave1 = sub.add_parser("verify-wave1-full-rebuild-db")
    verify_wave1.add_argument("--db", required=True)
    verify_wave1.add_argument("--source-root", required=True)
    verify_wave1.add_argument("--source-manifest", required=True)
    verify_wave1.add_argument("--audit-cache", required=True)
    verify_wave1.add_argument("--manifest-root", required=True)
    verify_wave1.add_argument("--corpus-manifest", required=True)
    verify_wave1.add_argument("--book1-manifest", required=True)
    verify_wave1.add_argument("--phase4b2-manifest", required=True)
    verify_wave1.add_argument("--phase4b3-manifest", required=True)
    verify_wave1.add_argument("--phase4b5-manifest", required=True)
    verify_wave1.add_argument("--phase4b6-manifest", required=True)
    verify_wave1.add_argument("--phase4c2-manifest", required=True)
    verify_wave1.add_argument("--report", required=True)

    ingest3 = sub.add_parser("ingest-full-quest")
    ingest3.add_argument("--db", required=True)
    ingest3.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    ingest3.add_argument("--source-root", action="append")
    ingest3.add_argument("--discovery-cache-dir", default=str(PROJECT_ROOT / "cache/discovery"))
    ingest3.add_argument("--report")

    gold = sub.add_parser("gold-tests")
    gold.add_argument("--db", required=True)

    gold2 = sub.add_parser("phase2-gold-tests")
    gold2.add_argument("--db", required=True)

    exact = sub.add_parser("exact-search")
    exact.add_argument("--db", required=True)
    exact.add_argument("--language", choices=("CHS", "EN", "JP"))
    exact.add_argument("--include-placeholders", action="store_true")
    exact.add_argument("query")

    character_rows = sub.add_parser("list-character-rows")
    character_rows.add_argument("--db", required=True)
    character_rows.add_argument("--ingest-run-id", required=True)
    character_rows.add_argument("--table-name")
    character_rows.add_argument("--native-locator-field")
    character_rows.add_argument("--native-locator-value")
    character_rows.add_argument("--avatar-id")
    character_rows.add_argument("--limit", type=int, default=50)
    character_rows.add_argument("--offset", type=int, default=0)

    character_row = sub.add_parser("get-character-row-evidence")
    character_row.add_argument("--db", required=True)
    character_row.add_argument("--ingest-run-id", required=True)
    character_row.add_argument("--raw-slot-limit", type=int, default=500)
    character_row.add_argument("--raw-slot-offset", type=int, default=0)
    character_row.add_argument("--relationship-limit", type=int, default=500)
    character_row.add_argument("--relationship-offset", type=int, default=0)
    character_row.add_argument("row_occurrence_id")

    character_text = sub.add_parser("search-character-text-evidence")
    character_text.add_argument("--db", required=True)
    character_text.add_argument("--ingest-run-id", required=True)
    character_text.add_argument("--locale", required=True, choices=("CHS", "EN", "JP"))
    character_text.add_argument("--limit", type=int, default=50)
    character_text.add_argument("--offset", type=int, default=0)
    character_text.add_argument("query")

    character_voice = sub.add_parser("get-character-voice-container")
    character_voice.add_argument("--db", required=True)
    character_voice.add_argument("--ingest-run-id", required=True)
    character_voice.add_argument("--limit", type=int, default=500)
    character_voice.add_argument("--offset", type=int, default=0)
    character_voice.add_argument("voice_container_id")

    character_context = sub.add_parser("get-character-voice-context")
    character_context.add_argument("--db", required=True)
    character_context.add_argument("--ingest-run-id", required=True)
    character_context.add_argument("--before", type=int, default=2)
    character_context.add_argument("--after", type=int, default=2)
    character_context.add_argument("audio_variant_occurrence_id")

    tutorial_rows = sub.add_parser("list-tutorial-rows")
    tutorial_rows.add_argument("--db", required=True)
    tutorial_rows.add_argument("--ingest-run-id", required=True)
    tutorial_rows.add_argument("--table-name")
    tutorial_rows.add_argument("--native-locator-field")
    tutorial_rows.add_argument("--native-locator-value")
    tutorial_rows.add_argument("--limit", type=int, default=50)
    tutorial_rows.add_argument("--offset", type=int, default=0)

    tutorial_row = sub.add_parser("get-tutorial-row-evidence")
    tutorial_row.add_argument("--db", required=True)
    tutorial_row.add_argument("--ingest-run-id", required=True)
    tutorial_row.add_argument("--raw-field-limit", type=int, default=500)
    tutorial_row.add_argument("--raw-field-offset", type=int, default=0)
    tutorial_row.add_argument("--raw-slot-limit", type=int)
    tutorial_row.add_argument("--raw-slot-offset", type=int)
    tutorial_row.add_argument("--relationship-limit", type=int, default=500)
    tutorial_row.add_argument("--relationship-offset", type=int, default=0)
    tutorial_row.add_argument("row_occurrence_id")

    tutorial_text = sub.add_parser("search-tutorial-text-evidence")
    tutorial_text.add_argument("--db", required=True)
    tutorial_text.add_argument("--ingest-run-id", required=True)
    tutorial_text.add_argument("--locale", required=True, choices=("CHS", "EN", "JP"))
    tutorial_text.add_argument("--limit", type=int, default=50)
    tutorial_text.add_argument("--offset", type=int, default=0)
    tutorial_text.add_argument("query")

    tutorial_context = sub.add_parser("get-tutorial-context")
    tutorial_context.add_argument("--db", required=True)
    tutorial_context.add_argument("--ingest-run-id", required=True)
    tutorial_context.add_argument(
        "--center-detail-array-index", "--detail-array-index", "--center-index",
        dest="center_detail_array_index", type=int,
    )
    tutorial_context.add_argument("--before", type=int, default=2)
    tutorial_context.add_argument("--after", type=int, default=2)
    tutorial_context.add_argument("row_occurrence_id")
    tutorial_context.add_argument("center_detail_array_index_positional", nargs="?", type=int)

    for name in ("get-passage", "get-context", "get-document", "compare-languages", "get-quest-traversal"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--db", required=True)
        cmd.add_argument("id")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command in {"init-db", "migrate"}:
        with Database(args.db) as db:
            db.initialize()
            emit({"ok": True, "db": str(Path(args.db).resolve()), "fts5": db.fts5})
        return 0
    if args.command == "inspect-source":
        manifest = load_manifest(args.manifest)
        inventory = SourceBundle(roots_from(args)).inventory()
        emit({"manifest": manifest, "source_roots": [str(Path(root).resolve()) for root in roots_from(args)], "inventory_count": len(inventory), "inventory": inventory})
        return 0
    if args.command == "audit-dialog-identity":
        manifest = load_manifest(args.manifest)
        bundle = SourceBundle(roots_from(args))
        index = build_talk_index(
            bundle,
            manifest,
            snapshot_id(manifest),
            cache_dir=args.discovery_cache_dir,
        )
        audit = dialog_identity_audit(index)
        audit.update({"source_roots": list(index.source_roots), "cache_path": index.cache_path})
        if args.report:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        emit(audit)
        return 0 if audit["status"] == "unique" else 2
    if args.command == "audit-talk-scope":
        manifest = load_manifest(args.manifest)
        bundle = SourceBundle(roots_from(args))
        index = build_talk_index(
            bundle,
            manifest,
            snapshot_id(manifest),
            cache_dir=args.discovery_cache_dir,
        )
        audit = talk_scope_audit(index)
        audit["source_roots"] = list(index.source_roots)
        audit["cache_path"] = index.cache_path
        if args.report:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        emit(audit)
        return 0 if audit["parent_identity"]["status"] == "unique" and audit["dialog_identity"]["status"] == "unique" else 2
    if args.command == "ingest-slice":
        report = ingest_slice(args.db, roots_from(args), args.manifest)
        if args.report:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        emit(report)
        return 0 if report.get("status") == "active" else 2
    if args.command == "ingest-phase2":
        report = ingest_phase2(args.db, roots_from(args), args.manifest, discovery_cache_dir=args.discovery_cache_dir)
        if args.report:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        emit(report)
        return 0 if report.get("status") == "active" else 2
    if args.command == "build-quest7008-evidence-db":
        report = ingest_quest_7008_evidence_slice(
            args.db,
            roots_from(args),
            args.manifest,
            candidate_scope_manifest_path=args.candidate_scope_manifest,
            audit_cache_path=args.audit_cache,
        )
        if args.report:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        emit(report)
        return 0 if report.get("status") == "complete" else 2
    if args.command == "build-quest7008-complete-evidence-db":
        report = ingest_quest_7008_complete_evidence_bundle(
            args.db,
            roots_from(args),
            args.manifest,
            bundle_manifest_path=args.bundle_manifest,
            audit_cache_path=args.audit_cache,
        )
        if args.report:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        emit(report)
        return 0 if report.get("status") == "complete" else 2
    if args.command == "build-quest-corpus-evidence-db":
        try:
            database, report_path = _validate_aq_build_paths(args)
            report = ingest_quest_corpus_evidence_bundle(
                database,
                roots_from(args),
                args.manifest,
                corpus_manifest_path=args.corpus_manifest,
                audit_cache_path=args.audit_cache,
                manifest_root=args.manifest_root,
            )
            if report_path is not None:
                write_json_exclusive(report_path, report)
        except (OSError, ValueError, TypeError, ManifestError, sqlite3.Error) as exc:
            print(f"quest corpus build failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "complete" else 2
    if args.command == "build-static-book1-evidence-db":
        try:
            report_path = _validate_b1_build_paths(args)
            report = ingest_static_book1_evidence_bundle(
                args.db,
                roots_from(args),
                args.manifest,
                args.book1_manifest,
            )
            if report_path is not None:
                write_json_exclusive(report_path, report)
        except (OSError, ValueError, TypeError, ManifestError, sqlite3.Error) as exc:
            print(f"static Book1 build failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "complete" else 2
    if args.command == "build-static-phase4b2-evidence-db":
        report_path = Path(args.report) if args.report else None
        if (
            report_path is not None
            and _normalized_output_path(args.db)
            == _normalized_output_path(report_path)
        ):
            print(
                "static Phase 4B2 database and report must not resolve to the same file",
                file=sys.stderr,
            )
            return 2
        if report_path is not None and report_path.exists():
            print(
                f"static Phase 4B2 build report already exists: {report_path}",
                file=sys.stderr,
            )
            return 2
        report = ingest_static_phase4b2_evidence_bundle(
            args.db,
            roots_from(args),
            args.manifest,
            args.phase4b2_manifest,
        )
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        emit(report)
        return 0 if report.get("status") == "complete" else 2
    if args.command == "build-static-phase4b3-evidence-db":
        report_path = Path(args.report) if args.report else None
        if report_path is not None and _normalized_output_path(args.db) == _normalized_output_path(report_path):
            print(
                "static Phase 4B3 database and build report must not resolve to the same file",
                file=sys.stderr,
            )
            return 2
        if report_path is not None and report_path.exists():
            print(f"static Phase 4B3 build report already exists: {report_path}", file=sys.stderr)
            return 2
        try:
            report = ingest_static_phase4b3_evidence_bundle(
                args.db,
                args.source_root,
                args.manifest,
            )
        except Phase4B3EvidenceError as exc:
            print(f"static Phase 4B3 build failed: {exc}", file=sys.stderr)
            return 2
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with report_path.open("x", encoding="utf-8") as output:
                output.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        emit(report)
        return 0 if report.get("status") == "complete" else 2
    if args.command == "build-static-phase4b5-reliquary-evidence-db":
        report_path = Path(args.report) if args.report else None
        if report_path is not None and _normalized_output_path(args.db) == _normalized_output_path(report_path):
            print(
                "static Phase 4B5 database and build report must not resolve to the same file",
                file=sys.stderr,
            )
            return 2
        if report_path is not None and report_path.exists():
            print(f"static Phase 4B5 build report already exists: {report_path}", file=sys.stderr)
            return 2
        try:
            report = ingest_static_phase4b5_reliquary_evidence_bundle(
                args.db,
                args.source_root,
                args.manifest,
            )
        except Phase4B5EvidenceError as exc:
            print(f"static Phase 4B5 build failed: {exc}", file=sys.stderr)
            return 2
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with report_path.open("x", encoding="utf-8") as output:
                output.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        emit(report)
        return 0 if report.get("status") == "complete" else 2
    if args.command == "build-static-phase4b6-material-evidence-db":
        try:
            _validate_phase4b6_cli_paths(args)
            report = ingest_static_phase4b6_material_evidence_bundle(
                args.db,
                args.source_root,
                args.manifest,
            )
            _write_cli_report(Path(args.report), report)
        except (Phase4B6EvidenceError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"static Phase 4B6 build failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "complete" else 2
    if args.command == "build-character-phase4c2-canary-evidence-db":
        try:
            _validate_phase4c2_cli_paths(args)
            report = ingest_character_phase4c2_evidence_bundle(
                args.db, args.source_root, args.manifest,
            )
            _write_cli_report(Path(args.report), report)
        except (Phase4C2EvidenceError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Phase 4C2 Character canary build failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "complete" else 2
    if args.command == "build-character-phase4c4b-bounded-evidence-db":
        try:
            _validate_phase4c4b_cli_paths(args)
            report = ingest_character_phase4c4b_evidence_bundle(
                args.db, args.source_root, args.manifest,
            )
            _write_cli_report(Path(args.report), report)
        except (Phase4C4BEvidenceError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Phase 4C4B Character bounded build failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "complete" else 2
    if args.command == "build-character-phase4c5b-full-evidence-db":
        try:
            _validate_phase4c5b_cli_paths(args)
            report = ingest_character_phase4c5b_evidence_bundle(
                args.db, args.source_root, args.manifest, report_path=args.report,
            )
        except (Phase4C5BEvidenceError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Phase 4C5B Character full-boundary build failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "complete" else 2
    if args.command == "build-tutorial-phase4d2a-canary-evidence-db":
        report_path = Path(args.report)
        if _normalized_output_path(args.db) == _normalized_output_path(report_path):
            print("Tutorial Phase 4D2A database and report must not resolve to the same file", file=sys.stderr)
            return 2
        if report_path.exists():
            print(f"Tutorial Phase 4D2A build report already exists: {report_path}", file=sys.stderr)
            return 2
        try:
            if not Path(args.source_root).is_dir():
                raise ValueError(f"Tutorial Phase 4D2A source root is not a directory: {args.source_root}")
            if not Path(args.manifest).is_file():
                raise ValueError(f"Tutorial Phase 4D2A manifest does not exist as a file: {args.manifest}")
            if not Path(args.db).is_file():
                raise ValueError(f"Tutorial Phase 4D2A --db must be an existing private copy: {args.db}")
            report = ingest_tutorial_phase4d2a_evidence_bundle(args.db, args.source_root, args.manifest)
            _write_cli_report(report_path, report)
        except (TutorialPhase4D2AEvidenceError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Tutorial Phase 4D2A canary build failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "complete" else 2
    if args.command == "build-tutorial-phase4d2b-full-boundary-evidence-db":
        report_path = Path(args.report)
        if _normalized_output_path(args.db) == _normalized_output_path(report_path):
            print("Tutorial Phase 4D2B database and report must not resolve to the same file", file=sys.stderr)
            return 2
        if report_path.exists():
            print(f"Tutorial Phase 4D2B build report already exists: {report_path}", file=sys.stderr)
            return 2
        try:
            if not Path(args.source_root).is_dir():
                raise ValueError(f"Tutorial Phase 4D2B source root is not a directory: {args.source_root}")
            if not Path(args.manifest).is_file():
                raise ValueError(f"Tutorial Phase 4D2B manifest does not exist as a file: {args.manifest}")
            if not Path(args.db).is_file():
                raise ValueError(f"Tutorial Phase 4D2B --db must be an existing private copy: {args.db}")
            report = ingest_tutorial_phase4d2b_evidence_bundle(args.db, args.source_root, args.manifest)
            _write_cli_report(report_path, report)
        except (TutorialPhase4D2BEvidenceError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Tutorial Phase 4D2B full-boundary build failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "complete" else 2
    if args.command == "append-phase4d5-combined":
        report_path = Path(args.report)
        if _normalized_output_path(args.db) == _normalized_output_path(report_path):
            print("Phase 4D5 database and report must not resolve to the same file", file=sys.stderr)
            return 2
        if report_path.exists():
            print(f"Phase 4D5 append report already exists: {report_path}", file=sys.stderr)
            return 2
        from .phase4d5_combined_gate import (
            Phase4D5CombinedGateError,
            append_tutorial_phase4d5_to_c5c_database,
        )
        try:
            report = append_tutorial_phase4d5_to_c5c_database(
                args.db, args.source_root, args.manifest, report_path=report_path
            )
        except (Phase4D5CombinedGateError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Phase 4D5 combined append failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "complete" else 2
    if args.command == "verify-phase4d5-combined":
        report_path = Path(args.report)
        if _normalized_output_path(args.db) == _normalized_output_path(report_path):
            print("Phase 4D5 database and report must not resolve to the same file", file=sys.stderr)
            return 2
        if report_path.exists():
            print(f"Phase 4D5 verification report already exists: {report_path}", file=sys.stderr)
            return 2
        from .phase4d5_combined_gate import (
            Phase4D5CombinedGateError,
            verify_phase4d5_combined_database,
        )
        try:
            report = verify_phase4d5_combined_database(args.db)
            _write_cli_report(report_path, report)
        except (Phase4D5CombinedGateError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Phase 4D5 combined verification failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "pass" else 2
    if args.command == "verify-static-phase4b2-evidence-db":
        report_path = Path(args.report) if args.report else None
        if (
            report_path is not None
            and _normalized_output_path(args.db)
            == _normalized_output_path(report_path)
        ):
            print(
                "static Phase 4B2 database and verification report must not resolve to the same file",
                file=sys.stderr,
            )
            return 2
        if report_path is not None and report_path.exists():
            print(
                f"static Phase 4B2 verification report already exists: {report_path}",
                file=sys.stderr,
            )
            return 2
        try:
            report = verify_static_phase4b2_evidence_db(
                args.db,
                args.build_report,
                args.phase4b2_manifest,
            )
        except StaticPhase4B2VerificationError as exc:
            print(f"static Phase 4B2 verification failed: {exc}", file=sys.stderr)
            return 2
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        emit(report)
        return 0
    if args.command == "verify-static-book1-evidence-db":
        try:
            database, report_path = _validate_b1_verify_paths(args)
            report = verify_static_book1_evidence_db(
                database,
                args.build_report,
                args.manifest,
                args.book1_manifest,
                args.source_root,
            )
            write_json_exclusive(report_path, report)
        except (
            StaticBook1VerificationError,
            OSError,
            ValueError,
            TypeError,
            ManifestError,
        ) as exc:
            print(f"static Book1 verification failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "pass" else 2
    if args.command == "verify-static-phase4b3-evidence-db":
        report_path = Path(args.report) if args.report else None
        if report_path is not None and _normalized_output_path(args.db) == _normalized_output_path(report_path):
            print(
                "static Phase 4B3 database and verification report must not resolve to the same file",
                file=sys.stderr,
            )
            return 2
        if report_path is not None and report_path.exists():
            print(f"static Phase 4B3 verification report already exists: {report_path}", file=sys.stderr)
            return 2
        try:
            report = verify_static_phase4b3_evidence_db(
                args.db,
                args.build_report,
                args.manifest,
                args.source_root,
            )
        except Phase4B3VerificationError as exc:
            print(f"static Phase 4B3 verification failed: {exc}", file=sys.stderr)
            return 2
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with report_path.open("x", encoding="utf-8") as output:
                output.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        emit(report)
        return 0
    if args.command == "verify-static-phase4b5-reliquary-evidence-db":
        report_path = Path(args.report) if args.report else None
        if report_path is not None and _normalized_output_path(args.db) == _normalized_output_path(report_path):
            print(
                "static Phase 4B5 database and verification report must not resolve to the same file",
                file=sys.stderr,
            )
            return 2
        if report_path is not None and report_path.exists():
            print(f"static Phase 4B5 verification report already exists: {report_path}", file=sys.stderr)
            return 2
        try:
            report = verify_static_phase4b5_reliquary_evidence_db(
                args.db,
                args.build_report,
                args.manifest,
                args.source_root,
            )
        except Phase4B5VerificationError as exc:
            print(f"static Phase 4B5 verification failed: {exc}", file=sys.stderr)
            return 2
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            with report_path.open("x", encoding="utf-8") as output:
                output.write(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        emit(report)
        return 0
    if args.command == "verify-static-phase4b6-material-evidence-db":
        try:
            _validate_phase4b6_cli_paths(args, build_report=True)
            report = verify_static_phase4b6_material_evidence_db(
                args.db,
                args.build_report,
                args.manifest,
                args.source_root,
            )
            _write_cli_report(Path(args.report), report)
        except (
            Phase4B6VerificationError,
            OSError,
            ValueError,
            TypeError,
            KeyError,
        ) as exc:
            print(f"static Phase 4B6 verification failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "pass" else 2
    if args.command == "verify-character-phase4c2-canary-evidence-db":
        try:
            _validate_phase4c2_cli_paths(args, verification=True)
            report = verify_character_phase4c2_evidence_db(
                args.db, args.build_report, args.manifest, args.source_root,
            )
            _write_cli_report(Path(args.report), report)
        except (Phase4C2VerificationError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Phase 4C2 Character canary verification failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "pass" else 2
    if args.command == "verify-character-phase4c4b-bounded-evidence-db":
        try:
            _validate_phase4c4b_cli_paths(args, verification=True)
            report = verify_character_phase4c4b_evidence_db(
                args.db, args.build_report, args.manifest, args.source_root,
            )
            _write_cli_report(Path(args.report), report)
        except (Phase4C4BVerificationError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Phase 4C4B Character bounded verification failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "pass" else 2
    if args.command == "verify-character-phase4c5b-full-evidence-db":
        try:
            _validate_phase4c5b_cli_paths(args, verification=True)
            report = verify_character_phase4c5b_evidence_db(
                args.db, args.build_report, args.manifest, args.source_root,
            )
            _write_cli_report(Path(args.report), report)
        except (Phase4C5BVerificationError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Phase 4C5B Character full-boundary verification failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("oracle") else 2
    if args.command == "verify-tutorial-phase4d2a-canary-evidence-db":
        report_path = Path(args.report)
        if _normalized_output_path(args.db) == _normalized_output_path(report_path):
            print("Tutorial Phase 4D2A database and report must not resolve to the same file", file=sys.stderr)
            return 2
        if report_path.exists():
            print(f"Tutorial Phase 4D2A verification report already exists: {report_path}", file=sys.stderr)
            return 2
        try:
            if not Path(args.source_root).is_dir():
                raise ValueError(f"Tutorial Phase 4D2A source root is not a directory: {args.source_root}")
            if not Path(args.manifest).is_file():
                raise ValueError(f"Tutorial Phase 4D2A manifest does not exist as a file: {args.manifest}")
            if not Path(args.db).is_file():
                raise ValueError(f"Tutorial Phase 4D2A --db does not exist as a file: {args.db}")
            if not Path(args.build_report).is_file():
                raise ValueError(f"Tutorial Phase 4D2A build report does not exist as a file: {args.build_report}")
            report = verify_tutorial_phase4d2a_evidence_db(args.db, args.build_report, args.manifest, args.source_root)
            _write_cli_report(report_path, report)
        except (TutorialPhase4D2AVerificationError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Tutorial Phase 4D2A canary verification failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("oracle") else 2
    if args.command == "verify-tutorial-phase4d2b-full-boundary-evidence-db":
        report_path = Path(args.report)
        if _normalized_output_path(args.db) == _normalized_output_path(report_path):
            print("Tutorial Phase 4D2B database and verification report must not resolve to the same file", file=sys.stderr)
            return 2
        if report_path.exists():
            print(f"Tutorial Phase 4D2B verification report already exists: {report_path}", file=sys.stderr)
            return 2
        try:
            if not Path(args.source_root).is_dir():
                raise ValueError(f"Tutorial Phase 4D2B source root is not a directory: {args.source_root}")
            if not Path(args.manifest).is_file():
                raise ValueError(f"Tutorial Phase 4D2B manifest does not exist as a file: {args.manifest}")
            if not Path(args.db).is_file():
                raise ValueError(f"Tutorial Phase 4D2B --db does not exist as a file: {args.db}")
            if not Path(args.build_report).is_file():
                raise ValueError(f"Tutorial Phase 4D2B build report does not exist as a file: {args.build_report}")
            report = verify_tutorial_phase4d2b_evidence_db(args.db, args.build_report, args.manifest, args.source_root)
            _write_cli_report(report_path, report)
        except (TutorialPhase4D2BVerificationError, OSError, ValueError, TypeError, KeyError) as exc:
            print(f"Tutorial Phase 4D2B full-boundary verification failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("oracle") else 2
    if args.command == "verify-wave1-full-rebuild-db":
        try:
            database, report_path = _validate_wave1_verify_paths(args)
            report = verify_wave1_full_rebuild_db(
                db_path=database,
                source_root=args.source_root,
                source_manifest_path=args.source_manifest,
                audit_cache_path=args.audit_cache,
                manifest_root=args.manifest_root,
                corpus_manifest_path=args.corpus_manifest,
                book1_manifest_path=args.book1_manifest,
                phase4b2_manifest_path=args.phase4b2_manifest,
                phase4b3_manifest_path=args.phase4b3_manifest,
                phase4b5_manifest_path=args.phase4b5_manifest,
                phase4b6_manifest_path=args.phase4b6_manifest,
                phase4c2_manifest_path=args.phase4c2_manifest,
            )
            write_json_exclusive(report_path, report)
        except (
            Wave1RebuildVerificationError,
            OSError,
            ValueError,
            TypeError,
            KeyError,
            ManifestError,
        ) as exc:
            print(f"Wave1 full rebuild verification failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "pass" else 2
    if args.command == "verify-quest-corpus-evidence-db":
        try:
            database, report_path = _validate_aq_verify_paths(args)
            report = verify_quest_corpus_evidence_db(
                database,
                args.build_report,
                args.corpus_manifest,
                manifest_root=args.manifest_root,
            )
            write_json_exclusive(report_path, report)
        except (QuestCorpusVerificationError, OSError, ValueError, TypeError) as exc:
            print(f"quest corpus verification failed: {exc}", file=sys.stderr)
            return 2
        emit(report)
        return 0 if report.get("status") == "pass" else 2
    if args.command == "ingest-full-quest":
        report = ingest_full_quest(args.db, roots_from(args), args.manifest, discovery_cache_dir=args.discovery_cache_dir)
        if args.report:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        emit(report)
        return 0 if report.get("status") == "active" else 2
    if args.command == "gold-tests":
        result = run_gold_tests(args.db)
        emit(result)
        return 0 if result["passed"] else 1
    if args.command == "phase2-gold-tests":
        result = run_phase2_gold_tests(args.db)
        emit(result)
        return 0 if result["passed"] else 1
    tutorial_commands = {
        "list-tutorial-rows",
        "get-tutorial-row-evidence",
        "search-tutorial-text-evidence",
        "get-tutorial-context",
    }
    if args.command in tutorial_commands:
        try:
            with open_readonly_database(args.db) as db:
                if args.command == "list-tutorial-rows":
                    result = list_tutorial_rows(
                        db,
                        ingest_run_id=args.ingest_run_id,
                        table_name=args.table_name,
                        native_locator_field=args.native_locator_field,
                        native_locator_value=args.native_locator_value,
                        limit=args.limit,
                        offset=args.offset,
                    )
                elif args.command == "get-tutorial-row-evidence":
                    result = get_tutorial_row_evidence(
                        db,
                        ingest_run_id=args.ingest_run_id,
                        row_occurrence_id=args.row_occurrence_id,
                        raw_field_limit=args.raw_field_limit,
                        raw_field_offset=args.raw_field_offset,
                        raw_slot_limit=args.raw_slot_limit,
                        raw_slot_offset=args.raw_slot_offset,
                        relationship_limit=args.relationship_limit,
                        relationship_offset=args.relationship_offset,
                    )
                elif args.command == "search-tutorial-text-evidence":
                    result = search_tutorial_text_evidence(
                        db,
                        ingest_run_id=args.ingest_run_id,
                        locale=args.locale,
                        query=args.query,
                        limit=args.limit,
                        offset=args.offset,
                    )
                else:
                    center_index = (
                        args.center_detail_array_index
                        if args.center_detail_array_index is not None
                        else args.center_detail_array_index_positional
                    )
                    result = get_tutorial_context(
                        db,
                        ingest_run_id=args.ingest_run_id,
                        row_occurrence_id=args.row_occurrence_id,
                        center_detail_array_index=center_index,
                        before=args.before,
                        after=args.after,
                    )
        except (FileNotFoundError, sqlite3.Error, ValueError, TypeError) as exc:
            print(f"Tutorial retrieval failed: {exc}", file=sys.stderr)
            return 2
        emit(result.to_dict() if isinstance(result, TutorialNotFound) else result)
        return 0

    character_commands = {
        "list-character-rows",
        "get-character-row-evidence",
        "search-character-text-evidence",
        "get-character-voice-container",
        "get-character-voice-context",
    }
    if args.command in character_commands:
        try:
            with open_readonly_database(args.db) as db:
                if args.command == "list-character-rows":
                    result = list_character_rows(
                        db,
                        ingest_run_id=args.ingest_run_id,
                        table_name=args.table_name,
                        native_locator_field=args.native_locator_field,
                        native_locator_value=args.native_locator_value,
                        avatar_id=args.avatar_id,
                        limit=args.limit,
                        offset=args.offset,
                    )
                elif args.command == "get-character-row-evidence":
                    result = get_character_row_evidence(
                        db,
                        ingest_run_id=args.ingest_run_id,
                        row_occurrence_id=args.row_occurrence_id,
                        raw_slot_limit=args.raw_slot_limit,
                        raw_slot_offset=args.raw_slot_offset,
                        relationship_limit=args.relationship_limit,
                        relationship_offset=args.relationship_offset,
                    )
                elif args.command == "search-character-text-evidence":
                    result = search_character_text_evidence(
                        db,
                        ingest_run_id=args.ingest_run_id,
                        locale=args.locale,
                        query=args.query,
                        limit=args.limit,
                        offset=args.offset,
                    )
                elif args.command == "get-character-voice-container":
                    result = get_character_voice_container(
                        db,
                        ingest_run_id=args.ingest_run_id,
                        voice_container_id=args.voice_container_id,
                        limit=args.limit,
                        offset=args.offset,
                    )
                else:
                    result = get_character_voice_context(
                        db,
                        ingest_run_id=args.ingest_run_id,
                        audio_variant_occurrence_id=args.audio_variant_occurrence_id,
                        before=args.before,
                        after=args.after,
                    )
        except (FileNotFoundError, sqlite3.Error, ValueError, TypeError) as exc:
            print(f"Character retrieval failed: {exc}", file=sys.stderr)
            return 2
        emit(result.to_dict() if isinstance(result, CharacterNotFound) else result)
        return 0
    with Database(args.db) as db:
        if args.command == "exact-search":
            emit(exact_search(db, args.query, language=args.language, include_placeholders=args.include_placeholders))
        elif args.command == "get-passage":
            emit(get_passage(db, args.id))
        elif args.command == "get-context":
            emit(get_context(db, args.id))
        elif args.command == "get-document":
            emit(get_document(db, args.id))
        elif args.command == "compare-languages":
            emit(compare_languages(db, args.id))
        elif args.command == "get-quest-traversal":
            emit(get_quest_traversal(db, args.id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
