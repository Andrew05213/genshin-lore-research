"""Phase 4B2-only expected occurrence contracts for the readonly release gate.

This module deliberately models only the sealed Phase 4B2 multifamily canary.
It is not a general static-data ontology and never reads the upstream checkout.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable

from .static_phase4b2_manifest import LOCALES, StaticPhase4B2Manifest
from .util import canonical_json, digest_id, normalize_text, sha256_text


EXACT = "exact comparison"
RECOMPUTED = "recomputed invariant"
FK_CHECKED = "enforced by composite FK + explicitly checked FK"
EXCLUDED = "deliberately excluded"


SCHEMA_COVERAGE_LEDGER: dict[str, dict[str, dict[str, str]]] = {
    "source_record": {
        "id": {
            "mode": EXCLUDED,
            "reason": "opaque physical key; exact source-file/pointer coordinates and every reference are checked",
        },
        "source_file_id": {
            "mode": FK_CHECKED,
            "reason": "joined to the sealed snapshot/file coordinate and included in explicit FK audit",
        },
        "pointer": {"mode": EXACT, "reason": "sealed family source coordinate"},
        "record_kind": {"mode": EXACT, "reason": "Phase 4B2 family-specific record kind"},
        "native_id": {"mode": EXACT, "reason": "sealed Excel/Readable/TextMap locator"},
        "raw_sha256": {"mode": RECOMPUTED, "reason": "recomputed from the family raw payload"},
        "raw_json": {
            "mode": RECOMPUTED,
            "reason": "parsed, canonicalized, shape-checked, and bound to raw_sha256/source content",
        },
    },
    "static_evidence_container": {
        "id": {
            "mode": EXCLUDED,
            "reason": "opaque occurrence key; the complete sealed source coordinate set is compared",
        },
        "ingest_run_id": {"mode": EXACT, "reason": "bound release run"},
        "snapshot_id": {"mode": EXACT, "reason": "sealed snapshot"},
        "source_file_id": {
            "mode": FK_CHECKED,
            "reason": "composite ownership FK plus exact sealed file coordinate",
        },
        "source_record_id": {
            "mode": FK_CHECKED,
            "reason": "composite ownership FK plus exact record coordinate",
        },
        "container_kind": {"mode": EXACT, "reason": "sealed Excel/Readable kind"},
        "source_family": {"mode": EXACT, "reason": "sealed table-qualified family"},
        "locale": {"mode": EXACT, "reason": "sealed Readable locale or NULL for Excel"},
        "candidate_namespace": {"mode": EXACT, "reason": "sealed table-qualified family namespace"},
        "candidate_key_json": {"mode": EXACT, "reason": "canonical raw locator, never logical identity"},
        "metadata_json": {"mode": EXACT, "reason": "sealed Phase 4B2 occurrence metadata"},
    },
    "static_segment_occurrence": {
        "id": {
            "mode": EXCLUDED,
            "reason": "opaque occurrence key; full container/ordinal occurrence set is compared",
        },
        "ingest_run_id": {"mode": EXACT, "reason": "bound release run"},
        "snapshot_id": {"mode": EXACT, "reason": "sealed snapshot"},
        "evidence_container_id": {
            "mode": FK_CHECKED,
            "reason": "composite FK plus exact expected Readable container coordinate",
        },
        "source_record_id": {
            "mode": FK_CHECKED,
            "reason": "composite FK plus exact expected Readable record coordinate",
        },
        "container_kind": {"mode": EXACT, "reason": "Phase 4B2 Readable occurrence kind"},
        "locale": {"mode": EXACT, "reason": "sealed Readable locale"},
        "source_pointer": {"mode": EXACT, "reason": "independently derived line pointer"},
        "file_local_ordinal": {"mode": EXACT, "reason": "sealed and independently derived ordinal"},
        "byte_start": {"mode": RECOMPUTED, "reason": "derived from UTF-8 raw text"},
        "byte_end": {"mode": RECOMPUTED, "reason": "derived from UTF-8 raw text"},
        "character_start": {"mode": RECOMPUTED, "reason": "derived from the full Readable string"},
        "character_end": {"mode": RECOMPUTED, "reason": "derived from the full Readable string"},
        "raw_text": {"mode": RECOMPUTED, "reason": "recovered from the exact character span"},
        "normalized_text": {"mode": RECOMPUTED, "reason": "normalize_text(raw_text)"},
        "raw_checksum": {"mode": RECOMPUTED, "reason": "sha256_text(raw_text)"},
        "normalized_checksum": {"mode": RECOMPUTED, "reason": "sha256_text(normalized_text)"},
        "segmentation_version": {"mode": EXACT, "reason": "sealed segmentation version"},
        "metadata_json": {"mode": EXACT, "reason": "sealed snapshot-scoped occurrence metadata"},
    },
    "static_relationship_occurrence": {
        "id": {
            "mode": EXCLUDED,
            "reason": "opaque occurrence key; full sealed source-pointer set is compared",
        },
        "ingest_run_id": {"mode": EXACT, "reason": "bound release run"},
        "snapshot_id": {"mode": EXACT, "reason": "sealed snapshot"},
        "source_container_id": {"mode": FK_CHECKED, "reason": "exact source coordinate and composite FK"},
        "source_record_id": {"mode": FK_CHECKED, "reason": "exact source coordinate and composite FK"},
        "source_family": {"mode": EXACT, "reason": "sealed table-qualified source family"},
        "source_pointer": {"mode": EXACT, "reason": "sealed owning field occurrence"},
        "relation_kind": {"mode": EXACT, "reason": "sealed family-specific relationship kind"},
        "field_role": {"mode": EXACT, "reason": "sealed conservative field role"},
        "target_family": {"mode": EXACT, "reason": "sealed target family"},
        "target_candidate_key_json": {"mode": EXACT, "reason": "canonical raw candidate locator"},
        "candidate_resolution_status": {"mode": EXACT, "reason": "sealed B2 candidate status"},
        "target_evidence_container_id": {
            "mode": FK_CHECKED,
            "reason": "nullable composite FK plus exact target coordinate/absence",
        },
        "ambiguity_json": {"mode": EXACT, "reason": "sealed empty ambiguity payload"},
        "metadata_json": {"mode": EXACT, "reason": "sealed raw-value provenance"},
    },
    "static_text_resolution_occurrence": {
        "id": {
            "mode": EXCLUDED,
            "reason": "opaque occurrence key; full owner/locale set is compared",
        },
        "ingest_run_id": {"mode": EXACT, "reason": "bound release run"},
        "snapshot_id": {"mode": EXACT, "reason": "sealed snapshot"},
        "static_relationship_occurrence_id": {
            "mode": FK_CHECKED,
            "reason": "exact owning relationship and composite FK",
        },
        "source_container_id": {"mode": FK_CHECKED, "reason": "owner composite FK"},
        "source_record_id": {"mode": FK_CHECKED, "reason": "owner composite FK"},
        "source_pointer": {"mode": EXACT, "reason": "sealed owning relationship pointer"},
        "locale": {"mode": EXACT, "reason": "sealed resolution locale"},
        "textmap_hash": {"mode": EXACT, "reason": "sealed locator hash"},
        "resolution_status": {"mode": EXACT, "reason": "sealed per-hash status"},
        "raw_text": {"mode": RECOMPUTED, "reason": "derived from exact TextMap candidate sources/status"},
        "normalized_text": {"mode": RECOMPUTED, "reason": "normalize_text(raw_text) when resolved"},
        "raw_checksum": {"mode": RECOMPUTED, "reason": "sha256_text(raw_text) when resolved"},
        "normalized_checksum": {"mode": RECOMPUTED, "reason": "sha256_text(normalized_text) when resolved"},
        "metadata_json": {"mode": EXACT, "reason": "sealed owner/locator metadata"},
    },
    "static_text_candidate_occurrence": {
        "id": {
            "mode": EXCLUDED,
            "reason": "opaque occurrence key; full resolution/shard/source set is compared",
        },
        "resolution_occurrence_id": {"mode": FK_CHECKED, "reason": "exact resolution owner and composite FK"},
        "ingest_run_id": {"mode": EXACT, "reason": "bound release run"},
        "snapshot_id": {"mode": EXACT, "reason": "sealed snapshot"},
        "source_file_id": {"mode": FK_CHECKED, "reason": "sealed TextMap file and composite FK"},
        "source_record_id": {"mode": FK_CHECKED, "reason": "exact TextMap entry and composite FK"},
        "source_pointer": {"mode": EXACT, "reason": "sealed /{hash} pointer"},
        "shard_kind": {"mode": EXACT, "reason": "sealed main/medium source selection"},
        "locale": {"mode": EXACT, "reason": "sealed TextMap locale"},
        "resolution_status": {"mode": EXACT, "reason": "sealed status copied from owner"},
        "raw_text": {"mode": RECOMPUTED, "reason": "decoded exact TextMap source_record.raw_json"},
        "normalized_text": {"mode": RECOMPUTED, "reason": "normalize_text(raw_text)"},
        "raw_checksum": {"mode": RECOMPUTED, "reason": "sha256_text(raw_text)"},
        "normalized_checksum": {"mode": RECOMPUTED, "reason": "sha256_text(normalized_text)"},
        "metadata_json": {"mode": EXACT, "reason": "sealed exact-source/owner metadata"},
    },
}


@dataclass(frozen=True)
class ExpectedSourceRecord:
    source_record_id: str
    source_file_id: str
    family_key: str
    relative_path: str
    pointer: str
    record_kind: str
    native_id: str
    raw_json: str
    raw_sha256: str


@dataclass(frozen=True)
class ExpectedContainer:
    container_id: str
    source_file_id: str
    source_record_id: str
    family_key: str
    relative_path: str
    record_pointer: str
    ingest_run_id: str
    snapshot_id: str
    container_kind: str
    source_family: str
    locale: str | None
    candidate_namespace: str
    candidate_key_json: str
    metadata_json: str


@dataclass(frozen=True)
class ExpectedSegment:
    family_key: str
    relative_path: str
    record_pointer: str
    ingest_run_id: str
    snapshot_id: str
    evidence_container_id: str
    source_record_id: str
    container_kind: str
    locale: str
    source_pointer: str
    file_local_ordinal: int
    byte_start: int
    byte_end: int
    character_start: int
    character_end: int
    raw_text: str
    normalized_text: str
    raw_checksum: str
    normalized_checksum: str
    segmentation_version: str
    metadata_json: str


@dataclass(frozen=True)
class ExpectedRelationship:
    family_key: str
    source_file_path: str
    source_record_pointer: str
    source_record_native_id: str
    source_family: str
    source_pointer: str
    relation_kind: str
    field_role: str
    target_family: str
    target_candidate_key: dict[str, Any]
    target_coordinate: tuple[str, str] | None
    target_record_native_id: str | None
    expected_raw_value: Any


@dataclass(frozen=True)
class ExpectedTextResolution:
    family_key: str
    owner_coordinate: tuple[str, str, str]
    locale: str
    textmap_hash: str
    resolution_status: str
    raw_text: str | None
    normalized_text: str | None
    raw_checksum: str | None
    normalized_checksum: str | None


@dataclass(frozen=True)
class ExpectedTextCandidate:
    family_key: str
    owner_coordinate: tuple[str, str, str]
    locale: str
    textmap_hash: str
    shard_kind: str
    resolution_status: str
    source_relative_path: str
    source_record_pointer: str
    source_record_id: str
    raw_text: str
    normalized_text: str
    raw_checksum: str
    normalized_checksum: str


@dataclass(frozen=True)
class Phase4B2ExpectedOccurrenceBundle:
    source_records: tuple[ExpectedSourceRecord, ...]
    containers: tuple[ExpectedContainer, ...]
    segments: tuple[ExpectedSegment, ...]
    relationships: tuple[ExpectedRelationship, ...]
    text_resolutions: tuple[ExpectedTextResolution, ...]
    text_candidates: tuple[ExpectedTextCandidate, ...]


@dataclass(frozen=True)
class _ContainerSpec:
    family_key: str
    relative_path: str
    record_pointer: str
    record_kind: str
    native_id: str
    native_field: str | None
    container_kind: str
    source_family: str
    locale: str | None
    candidate_namespace: str
    candidate_key_json: str
    metadata_json: str
    classification: str
    segment_ordinals: tuple[int, ...]

    @property
    def coordinate(self) -> tuple[str, str]:
        return self.relative_path, self.record_pointer


def expected_phase4b2_identity(
    manifest: StaticPhase4B2Manifest,
) -> dict[str, Any]:
    """Reconstruct B2 IDs and persisted run binding without builder code."""

    source = manifest.source
    snapshot_id = (
        f"{source['source_id']}:{source['release_label']}:"
        f"{source['commit'][:12]}"
    )
    selected_paths = [item.relative_path for item in manifest.files]
    config = {
        "family": "static_phase4b2_multifamily",
        "manifest_raw_sha256": manifest.raw_sha256,
        "selected_paths": selected_paths,
        "snapshot": {
            "commit": source["commit"],
            "tree": source["tree"],
        },
        "versions": manifest.versions,
    }
    config_checksum = sha256_text(canonical_json(config))
    parser_build_id = digest_id(
        "pb-static-phase4b2",
        snapshot_id,
        manifest.raw_sha256,
        manifest.versions["parser"],
        manifest.versions["projection"],
        manifest.versions["segmentation"],
        config_checksum,
    )
    ingest_run_id = digest_id(
        "ir-static-phase4b2",
        snapshot_id,
        manifest.raw_sha256,
        parser_build_id,
        config_checksum,
    )
    return {
        "snapshot_id": snapshot_id,
        "config_checksum": config_checksum,
        "parser_build_id": parser_build_id,
        "ingest_run_id": ingest_run_id,
        "scope_json": canonical_json({
            "family": "static_phase4b2_multifamily",
            "manifest_id": manifest.manifest_id,
            "selected_paths": selected_paths,
        }),
        "config_json": canonical_json({
            "manifest_raw_sha256": manifest.raw_sha256,
            "versions": manifest.versions,
            "full_static_scan": False,
            "logical_identity_created": False,
            "textmap_evidence_container_created": False,
        }),
    }


def verify_phase4b2_schema_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    summary: Counter[str] = Counter()
    for table, ledger in SCHEMA_COVERAGE_LEDGER.items():
        actual = tuple(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))
        expected = tuple(ledger)
        if set(actual) != set(expected) or len(actual) != len(expected):
            raise ValueError(
                f"Phase 4B2 verifier schema coverage drift for {table}: "
                f"actual={list(actual)} expected={list(expected)}"
            )
        summary.update(item["mode"] for item in ledger.values())
    return {
        "tables": SCHEMA_COVERAGE_LEDGER,
        "summary": dict(sorted(summary.items())),
    }


def _container_specs(manifest: StaticPhase4B2Manifest) -> tuple[_ContainerSpec, ...]:
    specs: list[_ContainerSpec] = []

    def add_excel(family_key: str, record: dict[str, Any]) -> None:
        table = str(record["table"])
        native_id = str(record["native_id"])
        specs.append(
            _ContainerSpec(
                family_key=family_key,
                relative_path=str(record["relative_path"]),
                record_pointer=str(record["pointer"]),
                record_kind="excel_row",
                native_id=native_id,
                native_field=str(record["native_field"]),
                container_kind="excel_row",
                source_family=table,
                locale=None,
                candidate_namespace=table,
                candidate_key_json=canonical_json({"table": table, "native_id": native_id}),
                metadata_json=canonical_json(
                    {
                        "family_key": family_key,
                        "classification": "raw_excel_row",
                        "manifest_id": manifest.manifest_id,
                        "manifest_raw_sha256": manifest.raw_sha256,
                        "raw_evidence_identity": "snapshot_source_coordinate_occurrence",
                    }
                ),
                classification="raw_excel_row",
                segment_ordinals=(),
            )
        )

    def add_readable(
        family_key: str,
        readable: dict[str, Any],
        ordinals: Iterable[int],
    ) -> None:
        relative_path = str(readable["relative_path"])
        locale = str(readable["locale"])
        classification = str(readable["classification"])
        specs.append(
            _ContainerSpec(
                family_key=family_key,
                relative_path=relative_path,
                record_pointer=str(readable["record_pointer"]),
                record_kind="readable_file",
                native_id=PurePosixPath(relative_path).stem,
                native_field=None,
                container_kind="readable_file",
                source_family="Readable",
                locale=locale,
                candidate_namespace="Readable",
                candidate_key_json=canonical_json(
                    {"locale": locale, "relative_path": relative_path}
                ),
                metadata_json=canonical_json(
                    {
                        "family_key": family_key,
                        "classification": classification,
                        "manifest_id": manifest.manifest_id,
                        "manifest_raw_sha256": manifest.raw_sha256,
                        "raw_evidence_identity": "snapshot_source_coordinate_occurrence",
                    }
                ),
                classification=classification,
                segment_ordinals=tuple(int(value) for value in ordinals),
            )
        )

    book = manifest.families["book1257"]
    for readable in book["readables"]:
        add_readable("book1257", readable, readable["segment_ordinals"])

    weapon = manifest.families["weapon15516"]
    for record in (
        weapon["weapon"]["record"],
        weapon["codex"]["record"],
        weapon["document"]["record"],
        weapon["localization"]["record"],
    ):
        add_excel("weapon15516", record)
    for readable in weapon["readables"]:
        add_readable("weapon15516", readable, weapon["segment_ordinals"])

    reliquary = manifest.families["reliquary15048"]
    add_excel("reliquary15048", reliquary["set"]["record"])
    for codex in reliquary["codex"]:
        add_excel("reliquary15048", codex["record"])
    for piece in reliquary["pieces"]:
        add_excel("reliquary15048", piece["record"])
    for story in reliquary["stories"]:
        add_excel("reliquary15048", story["document"]["record"])
        add_excel("reliquary15048", story["localization"]["record"])
        for readable in story["readables"]:
            add_readable("reliquary15048", readable, story["segment_ordinals"])

    materials = manifest.families["materials"]
    for base in materials["base"]:
        add_excel("materials", base["record"])
    add_excel("materials", materials["codex"]["record"])

    coordinates = [item.coordinate for item in specs]
    if len(specs) != 56 or len(set(coordinates)) != 56:
        raise ValueError(
            "sealed Phase 4B2 container contract is not exactly 56 unique coordinates"
        )
    return tuple(specs)


def _decode_canonical_raw_json(raw_json: Any, label: str) -> Any:
    if not isinstance(raw_json, str):
        raise ValueError(f"{label} raw_json must be a JSON string column")
    try:
        value = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} raw_json is invalid JSON: {exc}") from exc
    if canonical_json(value) != raw_json:
        raise ValueError(f"{label} raw_json is not canonical JSON")
    return value


def _verify_container_source_record(
    row: sqlite3.Row,
    spec: _ContainerSpec,
    manifest_files: dict[str, Any],
) -> tuple[ExpectedSourceRecord, str | None]:
    label = f"{spec.relative_path}#{spec.record_pointer}"
    expected_common = (
        spec.record_pointer,
        spec.record_kind,
        spec.native_id,
    )
    actual_common = (row["record_pointer"], row["record_kind"], row["native_id"])
    if actual_common != expected_common:
        raise ValueError(
            f"Phase 4B2 source_record coordinate/kind/native mismatch at {label}: "
            f"actual={actual_common} expected={expected_common}"
        )
    raw_value = _decode_canonical_raw_json(row["raw_json"], label)
    readable_text: str | None = None
    if spec.record_kind == "excel_row":
        if not isinstance(raw_value, dict):
            raise ValueError(f"Phase 4B2 Excel source_record is not an object at {label}")
        if spec.native_field not in raw_value:
            raise ValueError(f"Phase 4B2 Excel native field missing at {label}")
        if str(raw_value[spec.native_field]) != spec.native_id:
            raise ValueError(f"Phase 4B2 Excel native field mismatch at {label}")
        expected_raw_sha256 = sha256_text(canonical_json(raw_value))
    else:
        if not isinstance(raw_value, dict) or set(raw_value) != {"text"}:
            raise ValueError(f"Phase 4B2 Readable raw record shape mismatch at {label}")
        readable_text = raw_value["text"]
        if not isinstance(readable_text, str):
            raise ValueError(f"Phase 4B2 Readable text is not a string at {label}")
        raw_bytes = readable_text.encode("utf-8")
        file_spec = manifest_files[spec.relative_path]
        if len(raw_bytes) != file_spec.size_bytes or sha256_text(readable_text) != file_spec.sha256:
            raise ValueError(f"Phase 4B2 Readable raw_json/file binding mismatch at {label}")
        if spec.classification == "placeholder_exact_test" and readable_text != "test ":
            raise ValueError(f"Phase 4B2 placeholder content mismatch at {label}")
        if spec.classification == "empty" and readable_text != "":
            raise ValueError(f"Phase 4B2 empty Readable contains content at {label}")
        if spec.classification == "narrative" and readable_text == "":
            raise ValueError(f"Phase 4B2 narrative Readable is empty at {label}")
        expected_raw_sha256 = sha256_text(readable_text)
    if row["raw_sha256"] != expected_raw_sha256:
        raise ValueError(f"Phase 4B2 source_record raw_sha256 mismatch at {label}")
    return (
        ExpectedSourceRecord(
            source_record_id=row["source_record_id"],
            source_file_id=row["source_file_id"],
            family_key=spec.family_key,
            relative_path=spec.relative_path,
            pointer=spec.record_pointer,
            record_kind=spec.record_kind,
            native_id=spec.native_id,
            raw_json=row["raw_json"],
            raw_sha256=expected_raw_sha256,
        ),
        readable_text,
    )


def _line_spans(text: str) -> tuple[tuple[int, int, int, int, int, str], ...]:
    spans: list[tuple[int, int, int, int, int, str]] = []
    character_start = 0
    ordinal = 0
    while character_start <= len(text):
        character_end = character_start
        while character_end < len(text) and text[character_end] not in "\r\n":
            character_end += 1
        raw_text = text[character_start:character_end]
        if raw_text.strip():
            spans.append(
                (
                    ordinal,
                    len(text[:character_start].encode("utf-8")),
                    len(text[:character_end].encode("utf-8")),
                    character_start,
                    character_end,
                    raw_text,
                )
            )
        if character_end == len(text):
            break
        newline_length = (
            2
            if text[character_end] == "\r"
            and character_end + 1 < len(text)
            and text[character_end + 1] == "\n"
            else 1
        )
        character_start = character_end + newline_length
        ordinal += 1
    return tuple(spans)


def verify_phase4b2_sources_containers_segments(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B2Manifest,
    run_id: str,
    snapshot_id: str,
) -> tuple[
    tuple[ExpectedSourceRecord, ...],
    tuple[ExpectedContainer, ...],
    tuple[ExpectedSegment, ...],
]:
    specs = _container_specs(manifest)
    expected_by_coordinate = {item.coordinate: item for item in specs}
    rows = conn.execute(
        """SELECT c.id AS container_id, c.ingest_run_id, c.snapshot_id,
                  c.source_file_id, c.source_record_id, c.container_kind,
                  c.source_family, c.locale, c.candidate_namespace,
                  c.candidate_key_json, c.metadata_json,
                  sf.snapshot_id AS file_snapshot_id, sf.relative_path,
                  sr.pointer AS record_pointer, sr.record_kind, sr.native_id,
                  sr.raw_sha256, sr.raw_json
             FROM static_evidence_container c
             JOIN source_file sf ON sf.id=c.source_file_id
             JOIN source_record sr ON sr.id=c.source_record_id
            WHERE c.ingest_run_id=?""",
        (run_id,),
    ).fetchall()
    actual_coordinates = [(row["relative_path"], row["record_pointer"]) for row in rows]
    if len(actual_coordinates) != len(set(actual_coordinates)):
        raise ValueError("Phase 4B2 actual containers contain duplicate source coordinates")
    if set(actual_coordinates) != set(expected_by_coordinate):
        raise ValueError(
            "Phase 4B2 container occurrence set mismatch: "
            f"missing={sorted(set(expected_by_coordinate) - set(actual_coordinates))} "
            f"unexpected={sorted(set(actual_coordinates) - set(expected_by_coordinate))}"
        )

    manifest_files = {item.relative_path: item for item in manifest.files}
    source_records: list[ExpectedSourceRecord] = []
    containers: list[ExpectedContainer] = []
    readable_text_by_coordinate: dict[tuple[str, str], str] = {}
    for row in rows:
        coordinate = (row["relative_path"], row["record_pointer"])
        spec = expected_by_coordinate[coordinate]
        expected_values = (
            run_id,
            snapshot_id,
            snapshot_id,
            spec.container_kind,
            spec.source_family,
            spec.locale,
            spec.candidate_namespace,
            spec.candidate_key_json,
            spec.metadata_json,
        )
        actual_values = (
            row["ingest_run_id"],
            row["snapshot_id"],
            row["file_snapshot_id"],
            row["container_kind"],
            row["source_family"],
            row["locale"],
            row["candidate_namespace"],
            row["candidate_key_json"],
            row["metadata_json"],
        )
        if actual_values != expected_values:
            raise ValueError(
                f"Phase 4B2 container attribute mismatch at {coordinate}: "
                f"actual={actual_values} expected={expected_values}"
            )
        source_record, readable_text = _verify_container_source_record(
            row, spec, manifest_files
        )
        source_records.append(source_record)
        if readable_text is not None:
            readable_text_by_coordinate[coordinate] = readable_text
        containers.append(
            ExpectedContainer(
                container_id=row["container_id"],
                source_file_id=row["source_file_id"],
                source_record_id=row["source_record_id"],
                family_key=spec.family_key,
                relative_path=spec.relative_path,
                record_pointer=spec.record_pointer,
                ingest_run_id=run_id,
                snapshot_id=snapshot_id,
                container_kind=spec.container_kind,
                source_family=spec.source_family,
                locale=spec.locale,
                candidate_namespace=spec.candidate_namespace,
                candidate_key_json=spec.candidate_key_json,
                metadata_json=spec.metadata_json,
            )
        )

    container_by_coordinate = {
        (item.relative_path, item.record_pointer): item for item in containers
    }
    expected_segments: list[ExpectedSegment] = []
    for spec in specs:
        if spec.container_kind != "readable_file":
            continue
        text = readable_text_by_coordinate[spec.coordinate]
        spans = _line_spans(text) if spec.classification == "narrative" else ()
        derived_ordinals = tuple(item[0] for item in spans)
        if derived_ordinals != spec.segment_ordinals:
            raise ValueError(
                f"Phase 4B2 sealed/derived segment ordinal mismatch at {spec.coordinate}: "
                f"derived={derived_ordinals} sealed={spec.segment_ordinals}"
            )
        container = container_by_coordinate[spec.coordinate]
        for ordinal, byte_start, byte_end, char_start, char_end, raw_text in spans:
            normalized_text = normalize_text(raw_text)
            expected_segments.append(
                ExpectedSegment(
                    family_key=spec.family_key,
                    relative_path=spec.relative_path,
                    record_pointer=spec.record_pointer,
                    ingest_run_id=run_id,
                    snapshot_id=snapshot_id,
                    evidence_container_id=container.container_id,
                    source_record_id=container.source_record_id,
                    container_kind="readable_file",
                    locale=str(spec.locale),
                    source_pointer=f"/text/line/{ordinal}",
                    file_local_ordinal=ordinal,
                    byte_start=byte_start,
                    byte_end=byte_end,
                    character_start=char_start,
                    character_end=char_end,
                    raw_text=raw_text,
                    normalized_text=normalized_text,
                    raw_checksum=sha256_text(raw_text),
                    normalized_checksum=sha256_text(normalized_text),
                    segmentation_version=manifest.versions["segmentation"],
                    metadata_json=canonical_json(
                        {
                            "family_key": spec.family_key,
                            "identity_scope": "snapshot_source_coordinate_occurrence",
                            "cross_language_alignment": "unresolved",
                        }
                    ),
                )
            )
    if len(expected_segments) != 420:
        raise ValueError(
            f"Phase 4B2 expected segment cardinality mismatch: {len(expected_segments)}"
        )

    segment_rows = conn.execute(
        """SELECT s.*, sf.relative_path, sr.pointer AS record_pointer
             FROM static_segment_occurrence s
             JOIN static_evidence_container c ON c.id=s.evidence_container_id
             JOIN source_file sf ON sf.id=c.source_file_id
             JOIN source_record sr ON sr.id=s.source_record_id
            WHERE s.ingest_run_id=?""",
        (run_id,),
    ).fetchall()
    expected_segment_map = {
        (item.relative_path, item.record_pointer, item.file_local_ordinal): item
        for item in expected_segments
    }
    actual_segment_keys = [
        (row["relative_path"], row["record_pointer"], int(row["file_local_ordinal"]))
        for row in segment_rows
    ]
    if len(actual_segment_keys) != len(set(actual_segment_keys)):
        raise ValueError("Phase 4B2 actual segments contain duplicate occurrence coordinates")
    if set(actual_segment_keys) != set(expected_segment_map):
        raise ValueError(
            "Phase 4B2 segment occurrence set mismatch: "
            f"missing={len(set(expected_segment_map) - set(actual_segment_keys))} "
            f"unexpected={len(set(actual_segment_keys) - set(expected_segment_map))}"
        )
    compared_columns = (
        "ingest_run_id",
        "snapshot_id",
        "evidence_container_id",
        "source_record_id",
        "container_kind",
        "locale",
        "source_pointer",
        "file_local_ordinal",
        "byte_start",
        "byte_end",
        "character_start",
        "character_end",
        "raw_text",
        "normalized_text",
        "raw_checksum",
        "normalized_checksum",
        "segmentation_version",
        "metadata_json",
    )
    for row in segment_rows:
        key = (row["relative_path"], row["record_pointer"], int(row["file_local_ordinal"]))
        expected_segment = expected_segment_map[key]
        actual_values = tuple(row[column] for column in compared_columns)
        expected_values = tuple(getattr(expected_segment, column) for column in compared_columns)
        if actual_values != expected_values:
            raise ValueError(
                f"Phase 4B2 segment attribute mismatch at {key}: "
                f"actual={actual_values} expected={expected_values}"
            )

    return (
        tuple(sorted(source_records, key=lambda item: (item.relative_path, item.pointer))),
        tuple(sorted(containers, key=lambda item: (item.relative_path, item.record_pointer))),
        tuple(
            sorted(
                expected_segments,
                key=lambda item: (
                    item.relative_path,
                    item.record_pointer,
                    item.file_local_ordinal,
                ),
            )
        ),
    )


def load_expected_textmap_source_record(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B2Manifest,
    snapshot_id: str,
    family_key: str,
    locale: str,
    shard_kind: str,
    textmap_hash: str,
) -> tuple[ExpectedSourceRecord, str]:
    relative_path = str(manifest.textmap["locales"][locale][shard_kind])
    pointer = f"/{textmap_hash}"
    rows = conn.execute(
        """SELECT sr.id AS source_record_id, sr.source_file_id, sr.pointer,
                  sr.record_kind, sr.native_id, sr.raw_sha256, sr.raw_json,
                  sf.snapshot_id, sf.relative_path
             FROM source_file sf
             JOIN source_record sr ON sr.source_file_id=sf.id
            WHERE sf.snapshot_id=? AND sf.relative_path=? AND sr.pointer=?""",
        (snapshot_id, relative_path, pointer),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError(
            f"Phase 4B2 expected TextMap source coordinate is not unique: "
            f"{relative_path}#{pointer}"
        )
    row = rows[0]
    if (
        row["snapshot_id"] != snapshot_id
        or row["relative_path"] != relative_path
        or row["pointer"] != pointer
        or row["record_kind"] != "textmap_entry"
        or row["native_id"] != textmap_hash
    ):
        raise ValueError(f"Phase 4B2 TextMap source attributes mismatch at {relative_path}#{pointer}")
    raw_text = _decode_canonical_raw_json(row["raw_json"], f"{relative_path}#{pointer}")
    if not isinstance(raw_text, str):
        raise ValueError(f"Phase 4B2 TextMap source raw_json is not a string at {relative_path}#{pointer}")
    expected_sha = sha256_text(raw_text)
    if row["raw_sha256"] != expected_sha:
        raise ValueError(f"Phase 4B2 TextMap source raw_sha256 mismatch at {relative_path}#{pointer}")
    return (
        ExpectedSourceRecord(
            source_record_id=row["source_record_id"],
            source_file_id=row["source_file_id"],
            family_key=family_key,
            relative_path=relative_path,
            pointer=pointer,
            record_kind="textmap_entry",
            native_id=textmap_hash,
            raw_json=row["raw_json"],
            raw_sha256=expected_sha,
        ),
        raw_text,
    )


def verify_phase4b2_relationship_text_occurrence_sets(
    conn: sqlite3.Connection,
    manifest: StaticPhase4B2Manifest,
    run_id: str,
    snapshot_id: str,
    expected_relationships: Iterable[ExpectedRelationship],
    expected_containers: Iterable[ExpectedContainer],
) -> tuple[
    tuple[ExpectedRelationship, ...],
    tuple[ExpectedTextResolution, ...],
    tuple[ExpectedTextCandidate, ...],
    tuple[ExpectedSourceRecord, ...],
]:
    relationships = tuple(expected_relationships)
    if len(relationships) != 128:
        raise ValueError(
            f"Phase 4B2 expected relationship cardinality mismatch: {len(relationships)}"
        )
    container_by_coordinate = {
        (item.relative_path, item.record_pointer): item for item in expected_containers
    }
    relationship_rows = conn.execute(
        """SELECT r.*, sf.relative_path AS source_file_path,
                  sr.pointer AS source_record_pointer,
                  sr.native_id AS source_record_native_id
             FROM static_relationship_occurrence r
             JOIN static_evidence_container sc ON sc.id=r.source_container_id
             JOIN source_file sf ON sf.id=sc.source_file_id
             JOIN source_record sr ON sr.id=r.source_record_id
            WHERE r.ingest_run_id=?""",
        (run_id,),
    ).fetchall()
    actual_relationship_map: dict[tuple[str, str, str], sqlite3.Row] = {}
    for row in relationship_rows:
        key = (
            row["source_file_path"],
            row["source_record_pointer"],
            row["source_pointer"],
        )
        if key in actual_relationship_map:
            raise ValueError(f"Phase 4B2 duplicate relationship coordinate: {key}")
        actual_relationship_map[key] = row
    expected_relationship_map = {
        (item.source_file_path, item.source_record_pointer, item.source_pointer): item
        for item in relationships
    }
    if len(expected_relationship_map) != len(relationships):
        raise ValueError("Phase 4B2 expected relationship coordinates are not unique")
    if set(actual_relationship_map) != set(expected_relationship_map):
        raise ValueError(
            "Phase 4B2 relationship occurrence set mismatch: "
            f"missing={sorted(set(expected_relationship_map) - set(actual_relationship_map))} "
            f"unexpected={sorted(set(actual_relationship_map) - set(expected_relationship_map))}"
        )

    status_by_hash = {
        textmap_hash: status
        for status, hashes in manifest.textmap["hash_statuses"].items()
        for textmap_hash in hashes
    }
    expected_resolutions: list[ExpectedTextResolution] = []
    expected_candidates: list[ExpectedTextCandidate] = []
    text_source_records: dict[str, ExpectedSourceRecord] = {}
    verified_relationship_ids: set[str] = set()
    verified_resolution_ids: set[str] = set()
    verified_candidate_ids: set[str] = set()

    for coordinate, exp in expected_relationship_map.items():
        row = actual_relationship_map[coordinate]
        source_container = container_by_coordinate[
            (exp.source_file_path, exp.source_record_pointer)
        ]
        target_container = (
            None
            if exp.target_coordinate is None
            else container_by_coordinate[exp.target_coordinate]
        )
        expected_relationship_values = (
            run_id,
            snapshot_id,
            source_container.container_id,
            source_container.source_record_id,
            exp.source_family,
            exp.source_pointer,
            exp.relation_kind,
            exp.field_role,
            exp.target_family,
            canonical_json(exp.target_candidate_key),
            "resolved",
            None if target_container is None else target_container.container_id,
            "{}",
            canonical_json(
                {
                    "candidate_resolution_detail":
                        "raw_value_derived_then_manifest_and_target_checked",
                    "family_key": exp.family_key,
                    "manifest_id": manifest.manifest_id,
                    "raw_value": exp.expected_raw_value,
                }
            ),
        )
        relationship_columns = (
            "ingest_run_id",
            "snapshot_id",
            "source_container_id",
            "source_record_id",
            "source_family",
            "source_pointer",
            "relation_kind",
            "field_role",
            "target_family",
            "target_candidate_key_json",
            "candidate_resolution_status",
            "target_evidence_container_id",
            "ambiguity_json",
            "metadata_json",
        )
        actual_relationship_values = tuple(row[column] for column in relationship_columns)
        if actual_relationship_values != expected_relationship_values:
            raise ValueError(
                f"Phase 4B2 relationship attribute mismatch at {coordinate}: "
                f"actual={actual_relationship_values} expected={expected_relationship_values}"
            )
        if row["source_record_native_id"] != exp.source_record_native_id:
            raise ValueError(f"Phase 4B2 relationship source native_id mismatch at {coordinate}")
        verified_relationship_ids.add(row["id"])

        if exp.target_family != "TextMap":
            continue
        textmap_hash = str(exp.target_candidate_key["textmap_hash"])
        expected_status = status_by_hash.get(textmap_hash)
        if expected_status is None:
            raise ValueError(f"Phase 4B2 relationship uses unsealed TextMap hash {textmap_hash}")
        owner_coordinate = coordinate
        for locale in LOCALES:
            shard_kinds = {
                "missing": (),
                "unique_main": ("main",),
                "unique_medium": ("medium",),
                "both_equal": ("main", "medium"),
                "both_conflict": ("main", "medium"),
            }[expected_status]
            candidate_contracts: list[ExpectedTextCandidate] = []
            for shard_kind in shard_kinds:
                source_record, raw_text = load_expected_textmap_source_record(
                    conn,
                    manifest,
                    snapshot_id,
                    exp.family_key,
                    locale,
                    shard_kind,
                    textmap_hash,
                )
                prior_source = text_source_records.get(source_record.source_record_id)
                if prior_source is not None and prior_source != source_record:
                    raise ValueError(
                        f"Phase 4B2 shared TextMap source contract mismatch: "
                        f"{source_record.relative_path}#{source_record.pointer}"
                    )
                text_source_records[source_record.source_record_id] = source_record
                normalized_text = normalize_text(raw_text)
                candidate_contracts.append(
                    ExpectedTextCandidate(
                        family_key=exp.family_key,
                        owner_coordinate=owner_coordinate,
                        locale=locale,
                        textmap_hash=textmap_hash,
                        shard_kind=shard_kind,
                        resolution_status=expected_status,
                        source_relative_path=source_record.relative_path,
                        source_record_pointer=source_record.pointer,
                        source_record_id=source_record.source_record_id,
                        raw_text=raw_text,
                        normalized_text=normalized_text,
                        raw_checksum=sha256_text(raw_text),
                        normalized_checksum=sha256_text(normalized_text),
                    )
                )
            if expected_status == "both_equal":
                if candidate_contracts[0].raw_text != candidate_contracts[1].raw_text:
                    raise ValueError(
                        f"Phase 4B2 both_equal source candidates differ for {locale}/{textmap_hash}"
                    )
                resolved = candidate_contracts[0]
            elif expected_status == "both_conflict":
                if candidate_contracts[0].raw_text == candidate_contracts[1].raw_text:
                    raise ValueError(
                        f"Phase 4B2 both_conflict source candidates are equal for {locale}/{textmap_hash}"
                    )
                resolved = None
            elif candidate_contracts:
                resolved = candidate_contracts[0]
            else:
                resolved = None
            resolution_contract = ExpectedTextResolution(
                family_key=exp.family_key,
                owner_coordinate=owner_coordinate,
                locale=locale,
                textmap_hash=textmap_hash,
                resolution_status=expected_status,
                raw_text=None if resolved is None else resolved.raw_text,
                normalized_text=None if resolved is None else resolved.normalized_text,
                raw_checksum=None if resolved is None else resolved.raw_checksum,
                normalized_checksum=None if resolved is None else resolved.normalized_checksum,
            )
            expected_resolutions.append(resolution_contract)
            expected_candidates.extend(candidate_contracts)

            resolution_rows = conn.execute(
                """SELECT * FROM static_text_resolution_occurrence
                    WHERE ingest_run_id=?
                      AND static_relationship_occurrence_id=?
                      AND locale=?""",
                (run_id, row["id"], locale),
            ).fetchall()
            if len(resolution_rows) != 1:
                raise ValueError(
                    f"Phase 4B2 resolution coordinate is not unique at "
                    f"{owner_coordinate}/{locale}"
                )
            resolution_row = resolution_rows[0]
            expected_resolution_values = (
                run_id,
                snapshot_id,
                row["id"],
                source_container.container_id,
                source_container.source_record_id,
                exp.source_pointer,
                locale,
                textmap_hash,
                expected_status,
                resolution_contract.raw_text,
                resolution_contract.normalized_text,
                resolution_contract.raw_checksum,
                resolution_contract.normalized_checksum,
                canonical_json(
                    {
                        "both_conflict_auto_selected": False,
                        "family_key": exp.family_key,
                        "locator_only": True,
                        "owning_static_source": {
                            "record_pointer": exp.source_record_pointer,
                            "relative_path": exp.source_file_path,
                            "source_pointer": exp.source_pointer,
                        },
                    }
                ),
            )
            resolution_columns = (
                "ingest_run_id",
                "snapshot_id",
                "static_relationship_occurrence_id",
                "source_container_id",
                "source_record_id",
                "source_pointer",
                "locale",
                "textmap_hash",
                "resolution_status",
                "raw_text",
                "normalized_text",
                "raw_checksum",
                "normalized_checksum",
                "metadata_json",
            )
            actual_resolution_values = tuple(
                resolution_row[column] for column in resolution_columns
            )
            if actual_resolution_values != expected_resolution_values:
                raise ValueError(
                    f"Phase 4B2 resolution attribute mismatch at "
                    f"{owner_coordinate}/{locale}: actual={actual_resolution_values} "
                    f"expected={expected_resolution_values}"
                )
            verified_resolution_ids.add(resolution_row["id"])

            candidate_rows = conn.execute(
                """SELECT * FROM static_text_candidate_occurrence
                    WHERE resolution_occurrence_id=? ORDER BY shard_kind""",
                (resolution_row["id"],),
            ).fetchall()
            expected_candidate_by_shard = {
                item.shard_kind: item for item in candidate_contracts
            }
            actual_candidate_by_shard = {item["shard_kind"]: item for item in candidate_rows}
            if (
                len(actual_candidate_by_shard) != len(candidate_rows)
                or set(actual_candidate_by_shard) != set(expected_candidate_by_shard)
            ):
                raise ValueError(
                    f"Phase 4B2 candidate shard set mismatch at {owner_coordinate}/{locale}"
                )
            for shard_kind, candidate_contract in expected_candidate_by_shard.items():
                candidate_row = actual_candidate_by_shard[shard_kind]
                source_record = text_source_records[candidate_contract.source_record_id]
                expected_candidate_values = (
                    resolution_row["id"],
                    run_id,
                    snapshot_id,
                    source_record.source_file_id,
                    source_record.source_record_id,
                    candidate_contract.source_record_pointer,
                    shard_kind,
                    locale,
                    expected_status,
                    candidate_contract.raw_text,
                    candidate_contract.normalized_text,
                    candidate_contract.raw_checksum,
                    candidate_contract.normalized_checksum,
                    canonical_json(
                        {
                            "exact_textmap_source": {
                                "record_pointer": candidate_contract.source_record_pointer,
                                "relative_path": candidate_contract.source_relative_path,
                            },
                            "family_key": exp.family_key,
                            "owning_relationship_id": row["id"],
                            "textmap_hash_is_locator_not_identity": True,
                        }
                    ),
                )
                candidate_columns = (
                    "resolution_occurrence_id",
                    "ingest_run_id",
                    "snapshot_id",
                    "source_file_id",
                    "source_record_id",
                    "source_pointer",
                    "shard_kind",
                    "locale",
                    "resolution_status",
                    "raw_text",
                    "normalized_text",
                    "raw_checksum",
                    "normalized_checksum",
                    "metadata_json",
                )
                actual_candidate_values = tuple(
                    candidate_row[column] for column in candidate_columns
                )
                if actual_candidate_values != expected_candidate_values:
                    raise ValueError(
                        f"Phase 4B2 candidate attribute mismatch at "
                        f"{owner_coordinate}/{locale}/{shard_kind}: "
                        f"actual={actual_candidate_values} expected={expected_candidate_values}"
                    )
                verified_candidate_ids.add(candidate_row["id"])

    actual_relationship_ids = {
        row[0]
        for row in conn.execute(
            "SELECT id FROM static_relationship_occurrence WHERE ingest_run_id=?",
            (run_id,),
        )
    }
    actual_resolution_ids = {
        row[0]
        for row in conn.execute(
            "SELECT id FROM static_text_resolution_occurrence WHERE ingest_run_id=?",
            (run_id,),
        )
    }
    actual_candidate_ids = {
        row[0]
        for row in conn.execute(
            "SELECT id FROM static_text_candidate_occurrence WHERE ingest_run_id=?",
            (run_id,),
        )
    }
    coverage_sets = (
        ("relationship", verified_relationship_ids, actual_relationship_ids),
        ("resolution", verified_resolution_ids, actual_resolution_ids),
        ("candidate", verified_candidate_ids, actual_candidate_ids),
    )
    for label, verified_ids, actual_ids in coverage_sets:
        if verified_ids != actual_ids:
            raise ValueError(
                f"Phase 4B2 {label} occurrence ID coverage mismatch: "
                f"missing={sorted(actual_ids - verified_ids)} extra={sorted(verified_ids - actual_ids)}"
            )
    if (
        len(expected_resolutions) != 144
        or len(expected_candidates) != 129
        or len(text_source_records) == 0
    ):
        raise ValueError(
            "Phase 4B2 expected TextMap occurrence cardinality mismatch: "
            f"resolutions={len(expected_resolutions)} candidates={len(expected_candidates)} "
            f"source_records={len(text_source_records)}"
        )
    return (
        relationships,
        tuple(expected_resolutions),
        tuple(expected_candidates),
        tuple(
            sorted(
                text_source_records.values(),
                key=lambda item: (item.relative_path, item.pointer),
            )
        ),
    )


def verify_phase4b2_source_reference_coverage(
    conn: sqlite3.Connection,
    run_id: str,
    expected_source_records: Iterable[ExpectedSourceRecord],
) -> None:
    expected_ids = {item.source_record_id for item in expected_source_records}
    actual_ids = {
        row[0]
        for row in conn.execute(
            """SELECT source_record_id FROM static_evidence_container WHERE ingest_run_id=?
               UNION
               SELECT source_record_id FROM static_relationship_occurrence WHERE ingest_run_id=?
               UNION
               SELECT source_record_id FROM static_text_resolution_occurrence WHERE ingest_run_id=?
               UNION
               SELECT source_record_id FROM static_text_candidate_occurrence WHERE ingest_run_id=?""",
            (run_id, run_id, run_id, run_id),
        )
    }
    if actual_ids != expected_ids:
        raise ValueError(
            "Phase 4B2 referenced source_record set mismatch: "
            f"missing={sorted(expected_ids - actual_ids)} unexpected={sorted(actual_ids - expected_ids)}"
        )


__all__ = [
    "ExpectedContainer",
    "ExpectedRelationship",
    "ExpectedSegment",
    "ExpectedSourceRecord",
    "ExpectedTextCandidate",
    "ExpectedTextResolution",
    "Phase4B2ExpectedOccurrenceBundle",
    "SCHEMA_COVERAGE_LEDGER",
    "expected_phase4b2_identity",
    "load_expected_textmap_source_record",
    "verify_phase4b2_schema_coverage",
    "verify_phase4b2_relationship_text_occurrence_sets",
    "verify_phase4b2_source_reference_coverage",
    "verify_phase4b2_sources_containers_segments",
]
