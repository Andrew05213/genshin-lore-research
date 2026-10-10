from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from .character_phase4c4b_release_contract import (
    Phase4C4BReleaseContractError,
    Phase4C4BReleaseExpected,
    build_phase4c4b_release_expected,
    verify_phase4c4b_release_occurrence_rows,
)
from .character_contracts import C2_CONTRACT, C5B_CONTRACT
from .character_phase4c2_schema import (
    CharacterPhase4C2SchemaError,
    verify_character_phase4c2_schema_signature,
)
from .util import canonical_json, digest_id, sha256_bytes


RUN_FAMILY = "character_phase4c4b_bounded_six_avatar"
_ORACLE_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")


class Phase4C4BVerificationError(RuntimeError):
    pass


def _sqlite_sidecar_paths(target: Path) -> tuple[Path, ...]:
    return tuple(Path(f"{target}{suffix}") for suffix in _ORACLE_SIDECAR_SUFFIXES)


def _assert_no_sqlite_sidecars(target: Path, *, stage: str) -> None:
    present = [
        str(sidecar)
        for sidecar in _sqlite_sidecar_paths(target)
        if sidecar.exists()
    ]
    if present:
        raise Phase4C4BVerificationError(
            f"Phase 4C4B verifier requires sidecars absent {stage}: {present}"
        )


def _fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "sha256": sha256_bytes(path.read_bytes()),
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _load_build_report(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase4C4BVerificationError(
            f"cannot load Phase 4C4B build report: {exc}"
        ) from exc
    keys = {
        "status",
        "db_path",
        "snapshot_id",
        "parser_build_id",
        "ingest_run_id",
        "config_checksum",
        "manifest",
        "database_fingerprint",
        "counts",
        "status_counts",
        "table_row_counts",
        "text_owner_field_counts",
        "occurrence_set_digests",
        "scope",
    }
    if not isinstance(value, dict) or set(value) != keys:
        raise Phase4C4BVerificationError(
            "Phase 4C4B build report keys mismatch"
        )
    return value


def _expected_scope(expected: Phase4C4BReleaseExpected) -> dict[str, Any]:
    return {
        "avatar_ids": list(expected.manifest.boundary["avatar_ids"]),
        "positive_avatar_ids": list(expected.manifest.boundary["positive_avatar_ids"]),
        "boundary_avatar_ids": list(expected.manifest.boundary["boundary_avatar_ids"]),
        "coverage": "canary_only",
        "corpus_complete": False,
        "selected_paths": [
            item["relative_path"]
            for item in expected.derived["files"]
        ],
        "full_character_scan": False,
        "full_voice_scan": False,
        "logical_identity_created": False,
        "logical_passage_created": False,
        "mcp_capability_added": False,
        "future_context_scope": "voice_container_only",
    }


def _validate_report(
    build: dict[str, Any],
    target: Path,
    before: dict[str, Any],
    expected: Phase4C4BReleaseExpected,
) -> None:
    if (
        build["status"] != "complete"
        or Path(build["db_path"]).resolve() != target.resolve()
    ):
        raise Phase4C4BVerificationError(
            "Phase 4C4B build report status/path mismatch"
        )
    for key in (
        "snapshot_id",
        "parser_build_id",
        "ingest_run_id",
        "config_checksum",
    ):
        if build[key] != getattr(expected, key):
            raise Phase4C4BVerificationError(
                f"Phase 4C4B build report {key} mismatch"
            )
    if build["manifest"] != {
        "manifest_id": expected.manifest.manifest_id,
        "raw_sha256": expected.manifest.raw_sha256,
    }:
        raise Phase4C4BVerificationError(
            "Phase 4C4B build report manifest binding mismatch"
        )
    if build["database_fingerprint"] != before:
        raise Phase4C4BVerificationError(
            "Phase 4C4B build report database fingerprint mismatch"
        )
    for key in (
        "counts",
        "status_counts",
        "table_row_counts",
        "text_owner_field_counts",
        "occurrence_set_digests",
    ):
        if build[key] != expected.derived[key]:
            raise Phase4C4BVerificationError(
                f"Phase 4C4B build report {key} mismatch"
            )
    if build["scope"] != _expected_scope(expected):
        raise Phase4C4BVerificationError(
            "Phase 4C4B build report scope mismatch"
        )


def _same_family_run_ids(conn: sqlite3.Connection) -> list[str]:
    result: list[str] = []
    for row in conn.execute(
        "SELECT id,scope_json FROM ingest_run ORDER BY id"
    ):
        try:
            scope = json.loads(str(row["scope_json"]))
        except (TypeError, json.JSONDecodeError) as exc:
            raise Phase4C4BVerificationError(
                f"ingest_run scope_json is invalid: {row['id']}"
            ) from exc
        if (
            isinstance(scope, dict)
            and scope.get("family") == RUN_FAMILY
        ):
            result.append(str(row["id"]))
    return result


_CHARACTER_TABLES = {
    "rows": "character_row_occurrence",
    "raw_slots": "character_raw_slot_occurrence",
    "relations": "character_relation_occurrence",
    "text_resolutions": "character_text_resolution_occurrence",
    "text_candidates": "character_text_candidate_occurrence",
    "voice_containers": "character_voice_container_occurrence",
    "audio_variants": "character_audio_variant_occurrence",
}


# This oracle intentionally repeats the six-ID source coordinates and the
# family-local owner lists instead of importing builder/release derivation
# output. It is a source/SQL witness for the bounded contract, not a second
# projection implementation or a universal Character ontology.
_ORACLE_AVATAR_IDS = (
    "10000003", "10000005", "10000007", "10000062", "10000117", "10000901",
)
_ORACLE_PRIMARY_TABLES = {
    "AvatarExcelConfigData": ("id", "Avatar"),
    "FetterInfoExcelConfigData": ("avatarId", "Info"),
    "FetterStoryExcelConfigData": ("avatarId", "Story"),
    "FettersExcelConfigData": ("avatarId", "Fetter"),
}
_ORACLE_OWNER_FIELDS = {
    "AvatarExcelConfigData": ("descTextMapHash", "nameTextMapHash"),
    "FetterInfoExcelConfigData": (
        "avatarConstellationAfterTextMapHash",
        "avatarConstellationBeforTextMapHash",
        "avatarDetailTextMapHash", "avatarNativeTextMapHash",
        "avatarTitleTextMapHash", "avatarVisionAfterTextMapHash",
        "avatarVisionBeforTextMapHash", "cvChineseTextMapHash",
        "cvEnglishTextMapHash", "cvJapaneseTextMapHash",
        "cvKoreanTextMapHash",
    ),
    "FetterStoryExcelConfigData": (
        "storyContext2TextMapHash", "storyContextTextMapHash",
        "storyTitle2TextMapHash", "storyTitleLockedTextMapHash",
        "storyTitleTextMapHash",
    ),
    "FettersExcelConfigData": (
        "voiceFileTextTextMapHash", "voiceTitleLockedTextMapHash",
        "voiceTitleTextMapHash",
    ),
}
_ORACLE_TEXTMAP_PATHS = {
    "CHS": {
        "main": "TextMap/TextMapCHS.json",
        "medium": "TextMap/TextMap_MediumCHS.json",
    },
    "EN": {
        "main": "TextMap/TextMapEN.json",
        "medium": "TextMap/TextMap_MediumEN.json",
    },
    "JP": {
        "main": "TextMap/TextMapJP.json",
        "medium": "TextMap/TextMap_MediumJP.json",
    },
}
_ORACLE_ITEM_PATHS = (
    "BinOutput/Voice/Items/1248787199.json",
    "BinOutput/Voice/Items/1560160621.json",
    "BinOutput/Voice/Items/2939652103.json",
    "BinOutput/Voice/Items/750982387.json",
)
_ORACLE_PRIMARY_COUNTS = {
    "10000003": {"Avatar": 1, "Info": 1, "Story": 8, "Fetter": 72},
    "10000005": {"Avatar": 1, "Info": 1, "Story": 8, "Fetter": 254},
    "10000007": {"Avatar": 1, "Info": 1, "Story": 8, "Fetter": 254},
    "10000062": {"Avatar": 1, "Info": 1, "Story": 6, "Fetter": 57},
    "10000117": {"Avatar": 1, "Info": 1, "Story": 0, "Fetter": 0},
    "10000901": {"Avatar": 1, "Info": 0, "Story": 0, "Fetter": 0},
}
_ORACLE_OWNER_COUNTS = {
    "10000003": 269, "10000005": 815, "10000007": 815,
    "10000062": 214, "10000117": 13, "10000901": 2,
}
_ORACLE_LOCALE_COUNTS = {
    "10000003": {"unique_medium": 171, "missing": 98},
    "10000005": {"unique_medium": 733, "missing": 82},
    "10000007": {"unique_medium": 733, "missing": 82},
    "10000062": {"unique_medium": 137, "missing": 77},
    "10000117": {"unique_medium": 10, "missing": 3},
    "10000901": {"CHS": {"unique_medium": 2, "missing": 0}, "EN": {"unique_medium": 1, "missing": 1}, "JP": {"unique_medium": 1, "missing": 1}},
}
_ORACLE_VOICE_ATTACHMENTS = {
    "10000003": 72, "10000005": 254, "10000007": 254,
    "10000062": 57, "10000117": 0, "10000901": 0,
}
_ORACLE_LOCALES = ("CHS", "EN", "JP")
_ORACLE_TEXT_STATUSES = (
    "unique_main", "unique_medium", "both_equal", "both_conflict", "missing",
)


def _oracle_json(root: Path, relative_path: str) -> Any:
    try:
        return json.loads((root / relative_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Phase4C4BVerificationError(
            f"independent oracle cannot read {relative_path}: {exc}"
        ) from exc


def _independent_source_sql_invariants(
    conn: sqlite3.Connection,
    source_root: str | Path,
    expected: Phase4C4BReleaseExpected,
) -> dict[str, Any]:
    'Check literal source coordinates and persisted rows independently.'

    if tuple(expected.manifest.boundary["avatar_ids"]) != _ORACLE_AVATAR_IDS:
        raise Phase4C4BVerificationError(
            "independent oracle literal six-ID manifest mismatch"
        )
    root = Path(source_root).resolve()
    selected: dict[str, dict[str, list[tuple[int, dict[str, Any]]]]] = {
        avatar_id: {label: [] for _, label in _ORACLE_PRIMARY_TABLES.values()}
        for avatar_id in _ORACLE_AVATAR_IDS
    }
    table_for_label = {
        label: table_name
        for table_name, (_, label) in _ORACLE_PRIMARY_TABLES.items()
    }
    for table_name, (native_field, label) in _ORACLE_PRIMARY_TABLES.items():
        relative_path = f"ExcelBinOutput/{table_name}.json"
        rows = _oracle_json(root, relative_path)
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise Phase4C4BVerificationError(
                f"independent oracle family file is not an object array: {relative_path}"
            )
        for row_index, row in enumerate(rows):
            value = row.get(native_field)
            avatar_id = str(value) if value is not None else None
            if avatar_id in selected:
                selected[avatar_id][label].append((row_index, row))

    primary_counts = {
        avatar_id: {
            label: len(selected[avatar_id][label])
            for label in ("Avatar", "Info", "Story", "Fetter")
        }
        for avatar_id in _ORACLE_AVATAR_IDS
    }
    if primary_counts != _ORACLE_PRIMARY_COUNTS:
        raise Phase4C4BVerificationError(
            f"independent source primary counts mismatch: {primary_counts}"
        )

    textmaps: dict[str, dict[str, dict[str, Any]]] = {}
    for locale, paths in _ORACLE_TEXTMAP_PATHS.items():
        textmaps[locale] = {}
        for shard_kind, relative_path in paths.items():
            value = _oracle_json(root, relative_path)
            if not isinstance(value, dict):
                raise Phase4C4BVerificationError(
                    f"independent oracle TextMap shard is not an object: {relative_path}"
                )
            textmaps[locale][shard_kind] = value

    owner_counts = {avatar_id: 0 for avatar_id in _ORACLE_AVATAR_IDS}
    locale_counts = {
        avatar_id: {
            locale: {status: 0 for status in _ORACLE_TEXT_STATUSES}
            for locale in _ORACLE_LOCALES
        }
        for avatar_id in _ORACLE_AVATAR_IDS
    }
    for avatar_id in _ORACLE_AVATAR_IDS:
        for label, rows in selected[avatar_id].items():
            table_name = table_for_label[label]
            for _, row in rows:
                for field in _ORACLE_OWNER_FIELDS[table_name]:
                    value = row.get(field)
                    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                        raise Phase4C4BVerificationError(
                            f"independent oracle invalid TextMap owner: {table_name}.{field}"
                        )
                    owner_counts[avatar_id] += 1
                    hash_key = str(value)
                    for locale in _ORACLE_LOCALES:
                        main = textmaps[locale]["main"].get(hash_key)
                        medium = textmaps[locale]["medium"].get(hash_key)
                        if main is None and medium is None:
                            status = "missing"
                        elif main is not None and medium is None:
                            status = "unique_main"
                        elif main is None and medium is not None:
                            status = "unique_medium"
                        elif main == medium:
                            status = "both_equal"
                        else:
                            status = "both_conflict"
                        if (
                            main is not None and not isinstance(main, str)
                        ) or (
                            medium is not None and not isinstance(medium, str)
                        ):
                            raise Phase4C4BVerificationError(
                                f"independent oracle non-text TextMap candidate: {locale}/{hash_key}"
                            )
                        locale_counts[avatar_id][locale][status] += 1
    if owner_counts != _ORACLE_OWNER_COUNTS:
        raise Phase4C4BVerificationError(
            f"independent source owner counts mismatch: {owner_counts}"
        )
    expected_locale = {}
    for avatar_id in _ORACLE_AVATAR_IDS:
        base = _ORACLE_LOCALE_COUNTS[avatar_id]
        if "CHS" in base:
            per_locale = base
        else:
            per_locale = {locale: base for locale in _ORACLE_LOCALES}
        expected_locale[avatar_id] = {
            locale: dict(per_locale[locale])
            | {
                status: 0 for status in _ORACLE_TEXT_STATUSES
                if status not in per_locale[locale]
            }
            for locale in _ORACLE_LOCALES
        }
    if locale_counts != expected_locale:
        raise Phase4C4BVerificationError(
            f"independent source locale counts mismatch: {locale_counts}"
        )

    lut = _oracle_json(root, "BinOutput/Voice/Lut/Lut.json")
    if not isinstance(lut, dict):
        raise Phase4C4BVerificationError("independent oracle Voice LUT is not an object")
    item_files = {
        relative_path: _oracle_json(root, relative_path)
        for relative_path in _ORACLE_ITEM_PATHS
    }
    if not all(isinstance(value, dict) for value in item_files.values()):
        raise Phase4C4BVerificationError(
            "independent oracle Voice Items file is not an object"
        )
    lut_by_slot: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for lut_key, record in lut.items():
        if isinstance(record, dict) and record.get("PKPBCKFKHEE") == "Fetter":
            lut_by_slot.setdefault(str(record.get("MHDDLMLPIBB")), []).append(
                (str(lut_key), record)
            )
    voice_attachments = {avatar_id: 0 for avatar_id in _ORACLE_AVATAR_IDS}
    source_attachment_sets = {avatar_id: set() for avatar_id in _ORACLE_AVATAR_IDS}
    physical_by_avatar = {avatar_id: set() for avatar_id in _ORACLE_AVATAR_IDS}
    physical_containers: dict[tuple[str, str], int] = {}
    for avatar_id in _ORACLE_AVATAR_IDS:
        for row_index, row in selected[avatar_id]["Fetter"]:
            raw_slot = row.get("voiceFile")
            if raw_slot is None:
                continue
            if isinstance(raw_slot, bool) or not isinstance(raw_slot, (int, str)):
                raise Phase4C4BVerificationError(
                    f"independent oracle invalid Voice slot at Fetters/{row_index}"
                )
            slot = str(raw_slot)
            lut_candidates = lut_by_slot.get(slot, [])
            if len(lut_candidates) != 1:
                raise Phase4C4BVerificationError(
                    f"independent oracle Voice LUT join count for slot {slot}: {len(lut_candidates)}"
                )
            _, lut_record = lut_candidates[0]
            item_path = f"BinOutput/Voice/Items/{lut_record.get('DFEELKOFAKE')}.json"
            item_root = item_files.get(item_path)
            if item_root is None:
                raise Phase4C4BVerificationError(
                    f"independent oracle Voice Items path outside declared boundary: {item_path}"
                )
            item_candidates = [
                (str(item_key), item_record)
                for item_key, item_record in item_root.items()
                if isinstance(item_record, dict)
                and item_record.get("PKPBCKFKHEE") == "Fetter"
                and str(item_record.get("MHDDLMLPIBB")) == slot
            ]
            if len(item_candidates) != 1:
                raise Phase4C4BVerificationError(
                    f"independent oracle Voice Items join count for slot {slot}: {len(item_candidates)}"
                )
            item_key, item_record = item_candidates[0]
            if item_record.get("AIFHPAAHJOC") != item_key:
                raise Phase4C4BVerificationError(
                    f"independent oracle Voice Items object-pointer mismatch for slot {slot}"
                )
            variants = item_record.get("BOICINMJKCP")
            if not isinstance(variants, list) or not all(
                isinstance(variant, dict) for variant in variants
            ):
                raise Phase4C4BVerificationError(
                    f"independent oracle Voice child array invalid for slot {slot}"
                )
            physical_key = (item_path, f"/{item_key}")
            voice_attachments[avatar_id] += 1
            source_attachment_sets[avatar_id].add(
                (row_index, f"/{row_index}/voiceFile")
            )
            physical_by_avatar[avatar_id].add(physical_key)
            prior_children = physical_containers.setdefault(physical_key, len(variants))
            if prior_children != len(variants):
                raise Phase4C4BVerificationError(
                    f"independent oracle physical Voice child count changed: {physical_key}"
                )
    if voice_attachments != _ORACLE_VOICE_ATTACHMENTS:
        raise Phase4C4BVerificationError(
            f"independent source Voice attachment counts mismatch: {voice_attachments}"
        )
    source_attachment_count = len({
        (avatar_id, row_index, pointer)
        for avatar_id, values in source_attachment_sets.items()
        for row_index, pointer in values
    })
    physical_count = len(physical_containers)
    child_count = sum(physical_containers.values())
    if (source_attachment_count, physical_count, child_count) != (637, 314, 8800):
        raise Phase4C4BVerificationError(
            "independent source Voice physical counts mismatch: "
            f"attachments={source_attachment_count}, containers={physical_count}, "
            f"children={child_count}"
        )
    traveler_sets = {
        avatar_id: source_attachment_sets[avatar_id]
        for avatar_id in ("10000005", "10000007")
    }
    if any(len(values) != 254 for values in traveler_sets.values()) or (
        traveler_sets["10000005"] & traveler_sets["10000007"]
    ):
        raise Phase4C4BVerificationError(
            "independent oracle Traveler attachment row sets are not separate"
        )
    if any(len(physical_by_avatar[avatar_id]) != 254 for avatar_id in traveler_sets):
        raise Phase4C4BVerificationError(
            "independent oracle Traveler physical container counts mismatch"
        )
    if primary_counts["10000117"]["Story"] != 0 or primary_counts["10000117"]["Fetter"] != 0:
        raise Phase4C4BVerificationError("independent oracle 10000117 boundary gained a family")
    if any(primary_counts["10000901"][label] for label in ("Info", "Story", "Fetter")):
        raise Phase4C4BVerificationError("independent oracle 10000901 boundary gained a family")

    run_id = expected.ingest_run_id
    sql_primary = {
        avatar_id: {label: 0 for label in ("Avatar", "Info", "Story", "Fetter")}
        for avatar_id in _ORACLE_AVATAR_IDS
    }
    for row in conn.execute(
        "SELECT avatar_id,table_name,COUNT(*) FROM character_row_occurrence "
        "WHERE ingest_run_id=? GROUP BY avatar_id,table_name",
        (run_id,),
    ):
        table_label = _ORACLE_PRIMARY_TABLES.get(str(row[1]), (None, None))[1]
        if str(row[0]) not in sql_primary or table_label is None:
            raise Phase4C4BVerificationError("independent SQL primary row out of literal scope")
        sql_primary[str(row[0])][table_label] = int(row[2])
    if sql_primary != _ORACLE_PRIMARY_COUNTS:
        raise Phase4C4BVerificationError(
            f"independent SQL primary counts mismatch: {sql_primary}"
        )

    sql_owner = {avatar_id: 0 for avatar_id in _ORACLE_AVATAR_IDS}
    for row in conn.execute(
        "SELECT r.avatar_id,COUNT(*) FROM character_relation_occurrence rel "
        "JOIN character_row_occurrence r ON r.id=rel.source_row_occurrence_id "
        "AND r.ingest_run_id=rel.ingest_run_id "
        "WHERE rel.ingest_run_id=? AND rel.relation_kind='textmap_owner_candidate' "
        "GROUP BY r.avatar_id",
        (run_id,),
    ):
        if str(row[0]) not in sql_owner:
            raise Phase4C4BVerificationError("independent SQL owner row out of literal scope")
        sql_owner[str(row[0])] = int(row[1])
    if sql_owner != _ORACLE_OWNER_COUNTS:
        raise Phase4C4BVerificationError(
            f"independent SQL owner counts mismatch: {sql_owner}"
        )

    sql_locale = {
        avatar_id: {
            locale: {status: 0 for status in _ORACLE_TEXT_STATUSES}
            for locale in _ORACLE_LOCALES
        }
        for avatar_id in _ORACLE_AVATAR_IDS
    }
    for row in conn.execute(
        "SELECT r.avatar_id,tr.locale,tr.resolution_status,COUNT(*) "
        "FROM character_text_resolution_occurrence tr "
        "JOIN character_row_occurrence r ON r.id=tr.source_row_occurrence_id "
        "AND r.ingest_run_id=tr.ingest_run_id "
        "WHERE tr.ingest_run_id=? GROUP BY r.avatar_id,tr.locale,tr.resolution_status",
        (run_id,),
    ):
        avatar_id, locale, status = str(row[0]), str(row[1]), str(row[2])
        if (
            avatar_id not in sql_locale
            or locale not in _ORACLE_LOCALES
            or status not in _ORACLE_TEXT_STATUSES
        ):
            raise Phase4C4BVerificationError("independent SQL locale row out of literal scope")
        sql_locale[avatar_id][locale][status] = int(row[3])
    if sql_locale != expected_locale:
        raise Phase4C4BVerificationError("independent SQL locale counts mismatch")

    sql_voice = {avatar_id: 0 for avatar_id in _ORACLE_AVATAR_IDS}
    for row in conn.execute(
        "SELECT r.avatar_id,COUNT(*) FROM character_relation_occurrence rel "
        "JOIN character_row_occurrence r ON r.id=rel.source_row_occurrence_id "
        "AND r.ingest_run_id=rel.ingest_run_id "
        "WHERE rel.ingest_run_id=? AND rel.relation_kind='voice_container_attachment' "
        "GROUP BY r.avatar_id",
        (run_id,),
    ):
        if str(row[0]) not in sql_voice:
            raise Phase4C4BVerificationError("independent SQL Voice row out of literal scope")
        sql_voice[str(row[0])] = int(row[1])
    if sql_voice != _ORACLE_VOICE_ATTACHMENTS:
        raise Phase4C4BVerificationError(
            f"independent SQL Voice attachment counts mismatch: {sql_voice}"
        )

    sql_attachment_count = int(conn.execute(
        "SELECT COUNT(*) FROM (SELECT source_row_occurrence_id,source_pointer "
        "FROM character_relation_occurrence WHERE ingest_run_id=? "
        "AND relation_kind='voice_container_attachment' "
        "GROUP BY source_row_occurrence_id,source_pointer)",
        (run_id,),
    ).fetchone()[0])
    sql_physical_count = int(conn.execute(
        "SELECT COUNT(*) FROM (SELECT item_source_file_id,item_source_pointer "
        "FROM character_voice_container_occurrence WHERE ingest_run_id=? "
        "GROUP BY item_source_file_id,item_source_pointer)",
        (run_id,),
    ).fetchone()[0])
    sql_physical_outside = int(conn.execute(
        "SELECT COUNT(*) FROM character_voice_container_occurrence vc "
        "JOIN source_file sf ON sf.id=vc.item_source_file_id "
        "WHERE vc.ingest_run_id=? AND sf.relative_path NOT IN (?,?,?,?)",
        (run_id, *_ORACLE_ITEM_PATHS),
    ).fetchone()[0])
    sql_unattached_containers = int(conn.execute(
        "SELECT COUNT(*) FROM character_voice_container_occurrence vc "
        "WHERE vc.ingest_run_id=? AND NOT EXISTS (SELECT 1 FROM "
        "character_relation_occurrence rel WHERE rel.ingest_run_id=vc.ingest_run_id "
        "AND rel.target_voice_container_id=vc.id "
        "AND rel.relation_kind='voice_container_attachment')",
        (run_id,),
    ).fetchone()[0])
    child_counts = conn.execute(
        "SELECT COUNT(*),COUNT(*) FROM (SELECT voice_container_id,source_pointer "
        "FROM character_audio_variant_occurrence WHERE ingest_run_id=? "
        "GROUP BY voice_container_id,source_pointer)",
        (run_id,),
    ).fetchone()
    child_total = int(conn.execute(
        "SELECT COUNT(*) FROM character_audio_variant_occurrence WHERE ingest_run_id=?",
        (run_id,),
    ).fetchone()[0])
    child_unique = int(child_counts[1])
    child_binding_errors = int(conn.execute(
        "SELECT COUNT(*) FROM character_audio_variant_occurrence av "
        "LEFT JOIN character_voice_container_occurrence vc ON vc.id=av.voice_container_id "
        "AND vc.ingest_run_id=av.ingest_run_id "
        "WHERE av.ingest_run_id=? AND (vc.id IS NULL "
        "OR av.item_source_record_id<>vc.item_source_record_id "
        "OR av.source_pointer NOT LIKE vc.item_source_pointer || '/BOICINMJKCP/%')",
        (run_id,),
    ).fetchone()[0])
    if (
        sql_attachment_count != 637
        or sql_physical_count != 314
        or sql_physical_outside != 0
        or sql_unattached_containers != 0
        or child_total != 8800
        or child_unique != 8800
        or child_binding_errors != 0
    ):
        raise Phase4C4BVerificationError(
            "independent SQL physical Voice invariant mismatch: "
            f"attachments={sql_attachment_count}, containers={sql_physical_count}, "
            f"outside={sql_physical_outside}, unattached={sql_unattached_containers}, "
            f"children={child_total}/{child_unique}, binding_errors={child_binding_errors}"
        )
    traveler_sql = {
        str(row[0]): int(row[1])
        for row in conn.execute(
            "SELECT r.avatar_id,COUNT(*) FROM character_relation_occurrence rel "
            "JOIN character_row_occurrence r ON r.id=rel.source_row_occurrence_id "
            "AND r.ingest_run_id=rel.ingest_run_id "
            "WHERE rel.ingest_run_id=? AND rel.relation_kind='voice_container_attachment' "
            "AND r.avatar_id IN ('10000005','10000007') GROUP BY r.avatar_id",
            (run_id,),
        )
    }
    if traveler_sql != {"10000005": 254, "10000007": 254}:
        raise Phase4C4BVerificationError(
            f"independent SQL Traveler attachment counts mismatch: {traveler_sql}"
        )
    return {
        "status": "pass",
        "oracle": "independent_source_coordinate_and_sql_invariants_v1",
        "literal_avatar_ids": list(_ORACLE_AVATAR_IDS),
        "source": {
            "primary_counts": primary_counts,
            "owner_counts": owner_counts,
            "locale_counts": expected_locale,
            "voice_attachments": voice_attachments,
            "source_row_attachments": source_attachment_count,
            "physical_items_path_pointer_containers": physical_count,
            "audio_children": child_count,
            "traveler_attachment_sets": {
                avatar_id: len(values) for avatar_id, values in traveler_sets.items()
            },
        },
        "sql": {
            "primary_counts": sql_primary,
            "owner_counts": sql_owner,
            "locale_counts": sql_locale,
            "voice_attachments": sql_voice,
            "source_row_attachments": sql_attachment_count,
            "physical_items_path_pointer_containers": sql_physical_count,
            "containers_without_relation": sql_unattached_containers,
            "audio_children": child_total,
            "unique_container_source_pointers": child_unique,
            "child_binding_errors": child_binding_errors,
            "traveler_attachment_counts": traveler_sql,
        },
    }


def _verify_db_contract(
    conn: sqlite3.Connection,
    expected: Phase4C4BReleaseExpected,
    *,
    allowed_other_run_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    versions = [
        int(row[0])
        for row in conn.execute(
            "SELECT version FROM schema_migration ORDER BY version"
        )
    ]
    if versions != list(range(1, 11)):
        raise Phase4C4BVerificationError(
            "Phase 4C4B schema must be exactly known versions "
            f"1..10: {versions}"
        )
    schema_signature = verify_character_phase4c2_schema_signature(
        conn
    )

    source = expected.manifest.source
    catalog = conn.execute(
        "SELECT kind,url,license_note FROM source_catalog WHERE id=?",
        (source["source_id"],),
    ).fetchone()
    if catalog is None or tuple(catalog) != (
        "primary",
        source["upstream"],
        None,
    ):
        raise Phase4C4BVerificationError(
            "Phase 4C4B source_catalog binding mismatch"
        )
    snapshot = conn.execute(
        'SELECT source_id,release_label,build,"commit",tree,observed_at,'
        "status,metadata_json FROM source_snapshot WHERE id=?",
        (expected.snapshot_id,),
    ).fetchone()
    if snapshot is None or tuple(snapshot) != (
        source["source_id"],
        source["release_label"],
        source["build"],
        source["commit"],
        source["tree"],
        source["observed_at"],
        "active",
        canonical_json({"sealed_source_manifest": True}),
    ):
        raise Phase4C4BVerificationError(
            "Phase 4C4B parser-independent source snapshot "
            "binding mismatch"
        )
    parser = conn.execute(
        "SELECT parser_version,mapping_version,fingerprint,"
        "config_checksum,created_at FROM parser_build WHERE id=?",
        (expected.parser_build_id,),
    ).fetchone()
    if parser is None or tuple(parser) != (
        expected.manifest.versions["parser"],
        expected.manifest.versions["projection"],
        "character-phase4c4b-bounded-six-avatar",
        expected.config_checksum,
        source["observed_at"],
    ):
        raise Phase4C4BVerificationError(
            "Phase 4C4B parser_build binding mismatch"
        )
    expected_run_scope = canonical_json({
        "family": RUN_FAMILY,
        "manifest_id": expected.manifest.manifest_id,
        "avatar_ids": expected.manifest.boundary["avatar_ids"],
        "positive_avatar_ids": expected.manifest.boundary["positive_avatar_ids"],
        "boundary_avatar_ids": expected.manifest.boundary["boundary_avatar_ids"],
        "coverage": "canary_only",
        "corpus_complete": False,
        "selected_paths": [
            item["relative_path"]
            for item in expected.derived["files"]
        ],
    })
    expected_run_config = canonical_json({
        "manifest_raw_sha256": expected.manifest.raw_sha256,
        "versions": expected.manifest.versions,
        "source_commit": expected.manifest.source["commit"],
        "source_tree": expected.manifest.source["tree"],
        "avatar_ids": expected.manifest.boundary["avatar_ids"],
        "coverage": "canary_only",
        "corpus_complete": False,
        "logical_identity_created": False,
        "mcp_capability_added": False,
    })
    run = conn.execute(
        "SELECT snapshot_id,parser_build_id,scope_json,config_json,"
        "status,created_at FROM ingest_run WHERE id=?",
        (expected.ingest_run_id,),
    ).fetchone()
    if run is None or tuple(run) != (
        expected.snapshot_id,
        expected.parser_build_id,
        expected_run_scope,
        expected_run_config,
        "complete",
        source["observed_at"],
    ):
        raise Phase4C4BVerificationError(
            "Phase 4C4B ingest_run binding mismatch"
        )
    family_ids = _same_family_run_ids(conn)
    if family_ids != [expected.ingest_run_id]:
        raise Phase4C4BVerificationError(
            "Phase 4C4B same-family ingest run set mismatch: "
            f"{family_ids}"
        )

    file_ids: dict[str, str] = {}
    for item in expected.derived["files"]:
        file_id = digest_id(
            "sf", expected.snapshot_id, item["relative_path"]
        )
        file_ids[item["relative_path"]] = file_id
        row = conn.execute(
            "SELECT snapshot_id,relative_path,upstream_blob_oid,"
            "sha256,size_bytes,format FROM source_file WHERE id=?",
            (file_id,),
        ).fetchone()
        if row is None:
            raise Phase4C4BVerificationError(
                "Phase 4C4B source_file mismatch: "
                f"{item['relative_path']}"
            )
        actual_file = tuple(row)
        if (
            actual_file[0:2]
            != (
                expected.snapshot_id,
                item["relative_path"],
            )
            or actual_file[2] not in (None, item["blob_oid"])
            or actual_file[3:]
            != (
                item["sha256"],
                item["size_bytes"],
                item["format"],
            )
        ):
            raise Phase4C4BVerificationError(
                "Phase 4C4B source_file mismatch: "
                f"{item['relative_path']}"
            )
    for item in expected.derived["source_records"]:
        file_id = file_ids[item["relative_path"]]
        record_id = digest_id(
            "sr", file_id, item["source_pointer"]
        )
        row = conn.execute(
            "SELECT source_file_id,pointer,record_kind,native_id,"
            "raw_sha256,raw_json FROM source_record WHERE id=?",
            (record_id,),
        ).fetchone()
        if row is None or tuple(row) != (
            file_id,
            item["source_pointer"],
            item["record_kind"],
            item["native_id"],
            item["raw_checksum"],
            canonical_json(item["raw_value"]),
        ):
            raise Phase4C4BVerificationError(
                "Phase 4C4B source_record mismatch: "
                f"{item['relative_path']}#{item['source_pointer']}"
            )

    verify_phase4c4b_release_occurrence_rows(
        conn, expected, insert_missing=False
    )
    counts: dict[str, int] = {}
    for key, table in _CHARACTER_TABLES.items():
        scoped = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=?",
                (expected.ingest_run_id,),
            ).fetchone()[0]
        )
        total = int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
        )
        allowed_runs = {expected.ingest_run_id, *allowed_other_run_ids}
        present_runs = {
            str(row[0])
            for row in conn.execute(
                f"SELECT DISTINCT ingest_run_id FROM {table}"
            )
        }
        unexpected_runs = present_runs - allowed_runs
        if unexpected_runs:
            raise Phase4C4BVerificationError(
                f"Phase 4C4B {key} run-scope pollution: "
                f"unexpected_runs={sorted(unexpected_runs)}"
            )
        wanted = int(expected.derived["counts"][key])
        if scoped != wanted or (
            not allowed_other_run_ids and total != wanted
        ):
            raise Phase4C4BVerificationError(
                f"Phase 4C4B {key} expected-set pollution: "
                f"scoped={scoped} total={total} expected={wanted}"
            )
        counts[key] = scoped

    business_tables = (
        "evidence_container",
        "identity_group",
        "passage_occurrence",
        "occurrence_edge",
        "document_evidence",
        "static_evidence_container",
        "static_segment_occurrence",
        "static_relationship_occurrence",
        "static_text_resolution_occurrence",
        "static_text_candidate_occurrence",
    )
    business_leak = 0
    for table in business_tables:
        business_leak += int(
            conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE ingest_run_id=?",
                (expected.ingest_run_id,),
            ).fetchone()[0]
        )
    if business_leak:
        raise Phase4C4BVerificationError(
            "Phase 4C4B run leaked into logical/static business tables"
        )

    observed_status = {
        "relations": dict(Counter(
            str(row[0])
            for row in conn.execute(
                "SELECT resolution_status "
                "FROM character_relation_occurrence "
                "WHERE ingest_run_id=?",
                (expected.ingest_run_id,),
            )
        )),
        "text_resolutions": {
            key: int(
                conn.execute(
                    "SELECT COUNT(*) "
                    "FROM character_text_resolution_occurrence "
                    "WHERE ingest_run_id=? "
                    "AND resolution_status=?",
                    (expected.ingest_run_id, key),
                ).fetchone()[0]
            )
            for key in (
                "unique_main",
                "unique_medium",
                "both_equal",
                "both_conflict",
                "missing",
            )
        },
    }
    if (
        observed_status["relations"]
        != expected.derived["status_counts"]["relations"]
        or observed_status["text_resolutions"]
        != expected.derived["status_counts"]["text_resolutions"]
    ):
        raise Phase4C4BVerificationError(
            "Phase 4C4B persisted status counts mismatch"
        )
    distinct_paths = int(
        conn.execute(
            "SELECT COUNT(DISTINCT audio_path) "
            "FROM character_audio_variant_occurrence "
            "WHERE ingest_run_id=?",
            (expected.ingest_run_id,),
        ).fetchone()[0]
    )
    distinct_selectors = int(
        conn.execute(
            "SELECT COUNT(DISTINCT character_child_selector) "
            "FROM character_audio_variant_occurrence "
            "WHERE ingest_run_id=?",
            (expected.ingest_run_id,),
        ).fetchone()[0]
    )
    if (
        distinct_paths
        != expected.derived["counts"]["distinct_audio_paths"]
        or distinct_selectors
        != expected.derived["counts"][
            "distinct_character_child_selectors"
        ]
    ):
        raise Phase4C4BVerificationError(
            "Phase 4C4B audio/selector distinct counts mismatch"
        )
    foreign_keys = [
        tuple(row)
        for row in conn.execute("PRAGMA foreign_key_check")
    ]
    if foreign_keys:
        raise Phase4C4BVerificationError(
            f"Phase 4C4B foreign key violations: {foreign_keys[:3]}"
        )
    return {
        "schema_version": 10,
        "schema_signature": schema_signature,
        "counts": expected.derived["counts"],
        "status_counts": expected.derived["status_counts"],
        "table_row_counts": expected.derived[
            "table_row_counts"
        ],
        "text_owner_field_counts": expected.derived[
            "text_owner_field_counts"
        ],
        "occurrence_set_digests": expected.derived[
            "occurrence_set_digests"
        ],
        "foreign_key_check": [],
        "business_rows_for_character_run": 0,
        "static_business_rows_for_character_run": 0,
        "write_once_triggers": schema_signature[
            "immutable_triggers"
        ],
    }


def verify_character_phase4c4b_database_contract(
    conn: sqlite3.Connection,
    expected: Phase4C4BReleaseExpected,
    *,
    allowed_other_run_ids: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Verify 4C4B rows, optionally alongside fixed reviewed Character runs."""

    if tuple(allowed_other_run_ids) not in (
        (),
        (C2_CONTRACT.ingest_run_id,),
        (C2_CONTRACT.ingest_run_id, C5B_CONTRACT.ingest_run_id),
    ):
        raise Phase4C4BVerificationError(
            "C4B combined verification permits only the fixed Phase 4C2 and Phase 4C5B runs"
        )
    result = _verify_db_contract(
        conn, expected, allowed_other_run_ids=allowed_other_run_ids
    )
    return {
        "protocol": "wave1-stage-final-state-contract-v1",
        "contract_id": (
            "wave1-stage-final-state-v1:phase4c4b:"
            + expected.manifest.manifest_id
        ),
        "status": "pass",
        "run_id": expected.ingest_run_id,
        "parser_build_id": expected.parser_build_id,
        "manifest_sha256": expected.manifest.raw_sha256,
        "exact_counts": result["counts"],
        "required_checks": [
            "schema", "source_binding", "run_binding", "exact_counts",
            "full_rows", "occurrence_digests", "logical_boundary",
            "foreign_keys",
        ],
        **result,
    }



def verify_character_phase4c4b_evidence_db(
    db_path: str | Path,
    build_report_path: str | Path,
    manifest_path: str | Path,
    source_root: str | Path,
) -> dict[str, Any]:
    target = Path(db_path)
    if not target.is_file():
        raise Phase4C4BVerificationError(
            f"Phase 4C4B database does not exist: {target}"
        )

    # A sidecar can contain a newer SQLite state than the main file. Reject it
    # before even fingerprinting/opening the logical database.
    _assert_no_sqlite_sidecars(target, stage="before fingerprint")
    before = _fingerprint(target)
    _assert_no_sqlite_sidecars(target, stage="after fingerprint")
    conn: sqlite3.Connection | None = None
    journal_mode: str | None = None
    try:
        # This release oracle imports no builder preflight, derivation, or row
        # verification function. The source/SQL oracle below is separate from
        # both this release derivation and the persisted-state verifier.
        _assert_no_sqlite_sidecars(target, stage="before source verification")
        expected = build_phase4c4b_release_expected(
            source_root, manifest_path
        )
        _assert_no_sqlite_sidecars(target, stage="before build-report validation")
        build = _load_build_report(build_report_path)
        _validate_report(build, target, before, expected)
        _assert_no_sqlite_sidecars(target, stage="before readonly open")
        uri = target.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        _assert_no_sqlite_sidecars(target, stage="after readonly open")
        conn.execute("PRAGMA query_only=ON")
        if int(conn.execute("PRAGMA query_only").fetchone()[0]) != 1:
            raise Phase4C4BVerificationError(
                "SQLite query_only could not be enabled"
            )
        journal_mode = str(
            conn.execute("PRAGMA journal_mode").fetchone()[0]
        ).casefold()
        if journal_mode != "delete":
            raise Phase4C4BVerificationError(
                "Phase 4C4B verifier requires DELETE journal mode: "
                f"{journal_mode!r}"
            )
        _assert_no_sqlite_sidecars(target, stage="before independent oracle")
        independent = _independent_source_sql_invariants(
            conn, source_root, expected
        )
        _assert_no_sqlite_sidecars(target, stage="before database contract")
        contract = _verify_db_contract(conn, expected)
        _assert_no_sqlite_sidecars(target, stage="before readonly close")
        conn.close()
        conn = None
        _assert_no_sqlite_sidecars(target, stage="after readonly close")
        return {
            "status": "pass",
            "db_path": str(target.resolve()),
            "database_fingerprint": before,
            "manifest": {
                "manifest_id": expected.manifest.manifest_id,
                "raw_sha256": expected.manifest.raw_sha256,
            },
            "source": {
                "commit": expected.manifest.source["commit"],
                "tree": expected.manifest.source["tree"],
                "selected_files": len(expected.derived["files"]),
                "phase4c1_artifacts_verified": 5,
                "canary_unattached_intersection": 0,
                "preflight_verified": True,
            },
            "readonly": {
                "sqlite_uri_mode": "ro",
                "query_only": True,
                "journal_mode": journal_mode,
                "sidecars_absent": True,
                "fingerprint_unchanged": True,
            },
            "independent_invariants": independent,
            **contract,
        }
    except Phase4C4BVerificationError:
        raise
    except (
        Phase4C4BReleaseContractError,
        CharacterPhase4C2SchemaError,
        sqlite3.Error,
        OSError,
        ValueError,
        TypeError,
        KeyError,
    ) as exc:
        raise Phase4C4BVerificationError(str(exc)) from exc
    finally:
        if conn is not None:
            conn.close()
            _assert_no_sqlite_sidecars(target, stage="after readonly close")
        _assert_no_sqlite_sidecars(target, stage="before final fingerprint")
        after = _fingerprint(target)
        _assert_no_sqlite_sidecars(target, stage="after final fingerprint")
        if after != before:
            raise Phase4C4BVerificationError(
                "verifier mutated database fingerprint: "
                f"before={before} after={after}"
            )


__all__ = [
    "Phase4C4BVerificationError",
    "verify_character_phase4c4b_database_contract",
    "verify_character_phase4c4b_evidence_db",
]
