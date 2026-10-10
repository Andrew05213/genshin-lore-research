"""Batch M0 performance and physical-fidelity baseline.

This module is diagnostic only.  It creates no database, migration, evidence
projection, provider, or release artifact.  Its file inventory is complete for
five pinned source roots, while record coverage remains explicitly separate.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.util
import json
import math
import multiprocessing
import os
import platform
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .build_metrics import (
    file_fingerprint,
    finish_measurement,
    metric_spread_ratio,
    sqlite_statement_counter,
    start_measurement,
    unavailable,
)


REPORT_VERSION = 1
REPORT_NAME = "batch-m0-summary.json"
MARKDOWN_NAME = "batch-m0-summary.md"
SOURCE_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
SOURCE_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
SOURCE_SNAPSHOT_ID = f"anime-game-data:7.0:{SOURCE_COMMIT}"
SOURCE_ROOTS = ("BinOutput", "ExcelBinOutput", "Readable", "Subtitle", "TextMap")
EXPECTED_ROOT_FILE_COUNTS = {
    "BinOutput": 117359,
    "ExcelBinOutput": 2198,
    "Readable": 28650,
    "Subtitle": 4644,
    "TextMap": 34,
}
EXPECTED_ROOT_BYTES = {
    "BinOutput": 7_473_383_452,
    "ExcelBinOutput": 333_656_162,
    "Readable": 58_087_745,
    "Subtitle": 6_169_792,
    "TextMap": 1_282_899_534,
}
EXPECTED_FILE_COUNT = 152_885
EXPECTED_FILE_BYTES = 9_154_196_685
EXPECTED_GIT_INVENTORY_SHA256 = "711cbb0f980686e192c3f904f3f14b106911f51b9852fa67cd04f9b9c24066b2"
EXPECTED_PHYSICAL_MANIFEST_SHA256 = "5d5d5ebde19a9ae9af31ef7b4cda4584634d20b7892f220d992aadadbb84cf79"
EXPECTED_TOP_LEVEL_KEYS = frozenset({
    "accepted_product_unchanged",
    "cache_control",
    "decision_inputs_for_m1",
    "environment",
    "identity_policy",
    "limitations",
    "performance",
    "portable_evidence_sha256",
    "report_kind",
    "report_version",
    "source_denominator",
    "source_pin",
    "status",
    "stop_condition_details",
    "stop_conditions",
})
TEXTMAP_PATHS = (
    "TextMap/TextMapCHS.json",
    "TextMap/TextMapEN.json",
    "TextMap/TextMapJP.json",
    "TextMap/TextMap_MediumCHS.json",
    "TextMap/TextMap_MediumEN.json",
    "TextMap/TextMap_MediumJP.json",
)
ACCEPTED_SCHEMA13_SIZE = 1_723_596_800
ACCEPTED_SCHEMA13_SHA256 = "7ff8163b6007a41b0827af05fbcf996353617c52f354a131431da912712e58be"
ACCEPTED_WORLD_RECEIPT_SHA256 = "d5baa9986491d84090bfdab4df42500dc016d59c24ec8736187422269e9729a0"
ACCEPTED_QUEST_RECEIPT_SHA256 = "3924e72b454ac9c068d25815e2edf3b917b11680ad83d9f230433c8d82d9240b"
REPEAT_RUNS = 3
REPEATABILITY_LIMIT = 0.15
WORKER_RSS_LIMIT_BYTES = 8 * 1024**3
WORKER_BEST_MARGIN = 0.10
UNICODE_ESCAPE_RE = re.compile(rb"\\u[0-9a-fA-F]{4}")


class BatchM0Error(RuntimeError):
    pass


@dataclass(frozen=True)
class LexicalNumber:
    kind: str
    raw: str


@dataclass(frozen=True)
class PairsObject:
    pairs: tuple[tuple[str, Any], ...]


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def canonical_compact(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _git(source_root: Path, *arguments: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", "-C", str(source_root), *arguments],
        check=False,
        capture_output=True,
        text=not binary,
        encoding=None if binary else "utf-8",
    )
    if result.returncode:
        stderr = result.stderr.decode("utf-8", "replace") if binary else result.stderr
        raise BatchM0Error(f"source git command failed: {' '.join(arguments)}: {stderr.strip()}")
    return result.stdout


def verify_source_pin(source_root: Path) -> None:
    if str(_git(source_root, "status", "--porcelain=v1", "--untracked-files=all")).strip():
        raise BatchM0Error("pinned source checkout is not clean")
    if str(_git(source_root, "rev-parse", "HEAD")).strip() != SOURCE_COMMIT:
        raise BatchM0Error("pinned source commit mismatch")
    if str(_git(source_root, "rev-parse", "HEAD^{tree}")).strip() != SOURCE_TREE:
        raise BatchM0Error("pinned source tree mismatch")


def _tree_entries(source_root: Path) -> list[dict[str, Any]]:
    raw = _git(source_root, "ls-tree", "-r", "-l", "-z", "HEAD", "--", *SOURCE_ROOTS, binary=True)
    assert isinstance(raw, bytes)
    entries: list[dict[str, Any]] = []
    for encoded in raw.split(b"\0"):
        if not encoded:
            continue
        metadata, separator, raw_path = encoded.partition(b"\t")
        if not separator:
            raise BatchM0Error("unexpected git ls-tree record without a path separator")
        fields = metadata.decode("ascii").split()
        if len(fields) != 4 or fields[1] != "blob":
            raise BatchM0Error("source denominator contains an unsupported non-blob entry")
        mode, _kind, oid, raw_size = fields
        path = raw_path.decode("utf-8", "surrogateescape")
        entries.append({"mode": mode, "blob_oid": oid, "size_bytes": int(raw_size), "relative_path": path})
    entries.sort(key=lambda item: item["relative_path"].encode("utf-8", "surrogateescape"))
    counts = Counter(item["relative_path"].split("/", 1)[0] for item in entries)
    if dict(sorted(counts.items())) != EXPECTED_ROOT_FILE_COUNTS:
        raise BatchM0Error(f"five-root file denominator changed: {dict(sorted(counts.items()))}")
    return entries


def git_inventory_digest(source_root: Path) -> dict[str, Any]:
    entries = _tree_entries(source_root)
    digest = hashlib.sha256()
    for item in entries:
        digest.update(canonical_compact(item) + b"\n")
    return {
        "digest_algorithm": "sha256(canonical_git_blob_inventory_rows_v1)",
        "file_count": len(entries),
        "root_file_counts": dict(EXPECTED_ROOT_FILE_COUNTS),
        "sha256": digest.hexdigest(),
        "total_bytes": sum(item["size_bytes"] for item in entries),
    }


def _reject_constant(raw: str) -> Any:
    raise ValueError(f"non-JSON numeric constant: {raw}")


def parse_json_physical(raw: bytes) -> Any:
    return json.loads(
        raw,
        object_pairs_hook=lambda pairs: PairsObject(tuple(pairs)),
        parse_int=lambda value: LexicalNumber("integer", value),
        parse_float=lambda value: LexicalNumber("number", value),
        parse_constant=_reject_constant,
    )


def _escape_coordinate_key(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1").replace(":", "~2")


def coordinate_text(segments: Sequence[tuple[str, int, str | None]]) -> str:
    if not segments:
        return "/"
    tokens = []
    for kind, ordinal, key in segments:
        if kind == "array":
            tokens.append(f"#{ordinal}")
        else:
            tokens.append(f"@{ordinal}:{_escape_coordinate_key(key or '')}")
    return "/" + "/".join(tokens)


def resolve_coordinate(value: Any, segments: Sequence[tuple[str, int, str | None]]) -> Any:
    current = value
    for kind, ordinal, key in segments:
        if kind == "array":
            if not isinstance(current, list):
                raise BatchM0Error("array coordinate applied to a non-array")
            current = current[ordinal]
        else:
            if not isinstance(current, PairsObject):
                raise BatchM0Error("pair coordinate applied to a non-object")
            observed_key, current = current.pairs[ordinal]
            if observed_key != key:
                raise BatchM0Error("pair coordinate key check failed")
    return current


def _physical_value(value: Any) -> Any:
    if isinstance(value, LexicalNumber):
        return {"$number_kind": value.kind, "$raw": value.raw}
    if isinstance(value, PairsObject):
        return {"$pairs": [[key, _physical_value(item)] for key, item in value.pairs]}
    if isinstance(value, list):
        return [_physical_value(item) for item in value]
    return value


def physical_value_sha256(value: Any) -> str:
    return sha256_bytes(canonical_compact(_physical_value(value)))


def _bounded_append(target: list[dict[str, Any]], item: dict[str, Any], *, limit: int = 16) -> None:
    if len(target) < limit:
        target.append(item)


def analyze_json_value(value: Any, relative_path: str, samples: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    stack: list[tuple[Any, tuple[tuple[str, int, str | None], ...], int]] = [(value, (), 0)]
    tutorial = "Tutorial" in relative_path
    while stack:
        current, coordinate, depth = stack.pop()
        counts["maximum_depth"] = max(counts["maximum_depth"], depth)
        if isinstance(current, PairsObject):
            counts["object_occurrences"] += 1
            counts["object_pair_occurrences"] += len(current.pairs)
            keys = [key for key, _item in current.pairs]
            duplicates = {key: amount for key, amount in Counter(keys).items() if amount > 1}
            if duplicates:
                counts["duplicate_key_groups"] += len(duplicates)
                counts["duplicate_key_excess"] += sum(amount - 1 for amount in duplicates.values())
                for key, amount in sorted(duplicates.items()):
                    ordinals = [index for index, (candidate, _item) in enumerate(current.pairs) if candidate == key]
                    _bounded_append(samples["duplicate_keys"], {
                        "key": key,
                        "object_coordinate": coordinate_text(coordinate),
                        "pair_ordinals": ordinals,
                        "relative_path": relative_path,
                        "occurrences": amount,
                    })
            if keys != sorted(keys):
                counts["objects_with_nonlexical_member_order"] += 1
            for ordinal in range(len(current.pairs) - 1, -1, -1):
                key, child = current.pairs[ordinal]
                stack.append((child, (*coordinate, ("pair", ordinal, key)), depth + 1))
        elif isinstance(current, list):
            counts["array_occurrences"] += 1
            counts["array_element_occurrences"] += len(current)
            if tutorial and depth > 0 and current:
                child_coordinate = (*coordinate, ("array", 0, None))
                child = resolve_coordinate(value, child_coordinate)
                _bounded_append(samples["tutorial_nested_arrays"], {
                    "coordinate": coordinate_text(child_coordinate),
                    "relative_path": relative_path,
                    "value_sha256": physical_value_sha256(child),
                }, limit=12)
                counts["tutorial_nested_array_roundtrips"] += 1
            for ordinal in range(len(current) - 1, -1, -1):
                stack.append((current[ordinal], (*coordinate, ("array", ordinal, None)), depth + 1))
        elif isinstance(current, LexicalNumber):
            counts[f"number_kind:{current.kind}"] += 1
            lowered = current.raw.lower()
            risk = None
            if "e" in lowered:
                risk = "exponent"
            elif "." in current.raw:
                risk = "decimal"
            elif lowered == "-0":
                risk = "negative_zero"
            if risk:
                counts[f"number_lexical_risk:{risk}"] += 1
                _bounded_append(samples["number_lexical_risks"], {
                    "coordinate": coordinate_text(coordinate),
                    "kind": current.kind,
                    "raw": current.raw,
                    "relative_path": relative_path,
                    "risk": risk,
                })
    return dict(counts)


def _record_layout(root: str, value: Any) -> tuple[str, int, str]:
    if isinstance(value, list):
        return "top_level_array", len(value), "physical_top_level_occurrences_addressable"
    if isinstance(value, PairsObject):
        if root == "TextMap":
            return "top_level_object_pairs", len(value.pairs), "physical_top_level_occurrences_addressable"
        return "top_level_object_pairs", len(value.pairs), "physical_members_addressable_record_semantics_unsupported"
    return "top_level_scalar", 0, "unsupported_record_layout"


def _coordinate_anchors(value: Any, relative_path: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        length = len(value)
        kind = "array"
        key_at = lambda _index: None
    elif isinstance(value, PairsObject):
        length = len(value.pairs)
        kind = "pair"
        key_at = lambda index: value.pairs[index][0]
    else:
        return []
    if not length:
        return []
    result = []
    for ordinal in sorted({0, length // 2, length - 1}):
        segment = (kind, ordinal, key_at(ordinal))
        selected = resolve_coordinate(value, (segment,))
        result.append({
            "coordinate": coordinate_text((segment,)),
            "relative_path": relative_path,
            "value_sha256": physical_value_sha256(selected),
        })
    return result


def scan_source_denominator(source_root: Path) -> dict[str, Any]:
    entries = _tree_entries(source_root)
    start = start_measurement()
    root_counts: dict[str, Counter[str]] = defaultdict(Counter)
    global_counts: Counter[str] = Counter()
    fidelity_counts: Counter[str] = Counter()
    samples: dict[str, list[dict[str, Any]]] = defaultdict(list)
    file_digest = hashlib.sha256()
    git_digest = hashlib.sha256()
    phase_ns: Counter[str] = Counter()
    for item in entries:
        relative = item["relative_path"]
        root = relative.split("/", 1)[0]
        path = source_root / relative
        stat = path.stat()
        if not path.is_file() or path.is_symlink() or stat.st_size != item["size_bytes"]:
            raise BatchM0Error(f"source file shape or size changed: {relative}")
        git_digest.update(canonical_compact(item) + b"\n")
        extension = path.suffix.lower() or "<none>"
        read_started = time.perf_counter_ns()
        raw = path.read_bytes()
        phase_ns["file_read"] += time.perf_counter_ns() - read_started
        hash_started = time.perf_counter_ns()
        working_sha = sha256_bytes(raw)
        phase_ns["sha256"] += time.perf_counter_ns() - hash_started
        root_counts[root]["files"] += 1
        root_counts[root]["bytes"] += len(raw)
        root_counts[root][f"extension:{extension}"] += 1
        global_counts["files"] += 1
        global_counts["bytes"] += len(raw)
        unicode_escapes = len(UNICODE_ESCAPE_RE.findall(raw))
        fidelity_counts["unicode_escape_occurrences"] += unicode_escapes
        if unicode_escapes:
            fidelity_counts["files_with_unicode_escapes"] += 1
            _bounded_append(samples["unicode_escapes"], {
                "occurrences": unicode_escapes,
                "relative_path": relative,
            })
        parse_status = "not_parsed_non_json"
        record_status = "unsupported_record_layout"
        layout = "non_json"
        occurrence_count = 0
        if extension == ".json":
            decode_started = time.perf_counter_ns()
            try:
                value = parse_json_physical(raw)
            except (UnicodeError, ValueError, RecursionError) as exc:
                phase_ns["json_decode"] += time.perf_counter_ns() - decode_started
                parse_status = "unparsed_container"
                record_status = "unparsed_container"
                layout = "empty_json_container" if not raw else "json_parse_failed"
                _bounded_append(samples["unparsed_json"], {
                    "error_type": type(exc).__name__,
                    "reason": "empty_tracked_file" if not raw else str(exc),
                    "relative_path": relative,
                })
            else:
                phase_ns["json_decode"] += time.perf_counter_ns() - decode_started
                analysis_started = time.perf_counter_ns()
                observed = analyze_json_value(value, relative, samples)
                phase_ns["json_analysis"] += time.perf_counter_ns() - analysis_started
                fidelity_counts["maximum_observed_depth"] = max(
                    fidelity_counts["maximum_observed_depth"],
                    observed.pop("maximum_depth", 0),
                )
                fidelity_counts.update(observed)
                parse_status = "parsed_json"
                layout, occurrence_count, record_status = _record_layout(root, value)
                if len(samples["coordinate_roundtrips"]) < 24:
                    for anchor in _coordinate_anchors(value, relative):
                        _bounded_append(samples["coordinate_roundtrips"], anchor, limit=24)
                del value
        if len(raw) >= 16 * 1024**2:
            fidelity_counts["oversize_files_ge_16_mib"] += 1
            _bounded_append(samples["oversize_files"], {"relative_path": relative, "size_bytes": len(raw)})
        root_counts[root][f"parse_status:{parse_status}"] += 1
        root_counts[root][f"record_status:{record_status}"] += 1
        root_counts[root][f"layout:{layout}"] += 1
        root_counts[root]["physical_top_level_occurrences"] += occurrence_count
        global_counts[f"parse_status:{parse_status}"] += 1
        global_counts[f"record_status:{record_status}"] += 1
        global_counts[f"layout:{layout}"] += 1
        global_counts["physical_top_level_occurrences"] += occurrence_count
        file_digest.update(canonical_compact({
            "blob_oid": item["blob_oid"],
            "extension": extension,
            "layout": layout,
            "mode": item["mode"],
            "parse_status": parse_status,
            "record_status": record_status,
            "relative_path": relative,
            "sha256": working_sha,
            "size_bytes": item["size_bytes"],
            "top_level_occurrences": occurrence_count,
        }) + b"\n")
    metrics = finish_measurement(start)
    measured_counts = {root: root_counts[root]["files"] for root in SOURCE_ROOTS}
    if measured_counts != EXPECTED_ROOT_FILE_COUNTS:
        raise BatchM0Error(f"scanned file counts changed: {measured_counts}")
    status_counts = {key.split(":", 1)[1]: amount for key, amount in global_counts.items() if key.startswith("record_status:")}
    addressable = sum(amount for key, amount in global_counts.items() if key.startswith("record_status:physical_"))
    # Zero observations are contract facts, not missing metrics.  Keep these
    # keys explicit so tracked acceptance can distinguish zero from omission.
    fidelity_counts.setdefault("duplicate_key_groups", 0)
    fidelity_counts.setdefault("duplicate_key_excess", 0)
    result = {
        "file_level": {
            "coverage_complete": global_counts["files"] == len(entries),
            "file_count": global_counts["files"],
            "physical_file_manifest_sha256": file_digest.hexdigest(),
            "total_bytes": global_counts["bytes"],
            "git_inventory_sha256": git_digest.hexdigest(),
        },
        "record_level": {
            "coverage_complete": False,
            "coverage_note": "addressable top-level occurrences are physical coordinates, not family record semantics",
            "files_with_addressable_physical_occurrences": addressable,
            "physical_top_level_occurrences": global_counts["physical_top_level_occurrences"],
            "status_counts": dict(sorted(status_counts.items())),
        },
        "root_summaries": {},
        "physical_fidelity": {
            "coordinate_contract": "pair_ordinal_path_v1",
            "byte_spans_implemented": False,
            "counts": dict(sorted(fidelity_counts.items())),
            "samples": {key: value for key, value in sorted(samples.items())},
        },
        "measurement": metrics | {
            "phase_wall_seconds": {key: round(value / 1_000_000_000, 6) for key, value in sorted(phase_ns.items())},
            "sqlite_statement_count": unavailable("source denominator scan opens no SQLite connection"),
            "index_build_time": unavailable("M0 creates no database or index"),
        },
    }
    for root in SOURCE_ROOTS:
        counter = root_counts[root]
        result["root_summaries"][root] = {
            "bytes": counter["bytes"],
            "extension_counts": dict(sorted((key.split(":", 1)[1], value) for key, value in counter.items() if key.startswith("extension:"))),
            "file_count": counter["files"],
            "layout_counts": dict(sorted((key.split(":", 1)[1], value) for key, value in counter.items() if key.startswith("layout:"))),
            "parse_status_counts": dict(sorted((key.split(":", 1)[1], value) for key, value in counter.items() if key.startswith("parse_status:"))),
            "physical_top_level_occurrences": counter["physical_top_level_occurrences"],
            "record_status_counts": dict(sorted((key.split(":", 1)[1], value) for key, value in counter.items() if key.startswith("record_status:"))),
        }
    return result


WORKLOADS = {
    "avatar_g2_source_audit": {
        "script": "scripts/generate_phase4g1_avatar_codex_audit.py",
        "function": "build_reports",
        "args": "source_repo",
        "unit_count": 120,
        "unit": "primary_physical_rows",
    },
    "world_archive_source_audit": {
        "script": "scripts/generate_phase4e1_world_archive_audit.py",
        "function": "build_reports",
        "args": "source_repo",
        "unit_count": 852,
        "unit": "primary_physical_rows",
    },
    "quest_codex_source_audit": {
        "script": "scripts/generate_phase4f1_quest_codex_audit.py",
        "function": "generate_reports",
        "args": "source_repo",
        "unit_count": 633,
        "unit": "primary_plus_referenced_support_rows",
    },
    "character_source_audit": {
        "script": "scripts/generate_phase4c1_character_audit.py",
        "function": "build_reports",
        "args": "source",
        "unit_count": 10277,
        "unit": "four_primary_table_rows",
    },
}


def _load_script(repo_root: Path, relative: str) -> Any:
    path = repo_root / relative
    name = "_batch_m0_" + path.stem + "_" + hashlib.sha1(relative.encode("utf-8")).hexdigest()[:8]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise BatchM0Error(f"cannot load workload module: {relative}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _profile_child(connection: Any, workload: str, source_root: str, repo_root: str) -> None:
    try:
        specification = WORKLOADS[workload]
        start = start_measurement()
        module = _load_script(Path(repo_root), specification["script"])
        function = getattr(module, specification["function"])
        if specification["args"] == "source_repo":
            reports = function(Path(source_root), Path(repo_root))
        else:
            reports = function(Path(source_root))
        metrics = finish_measurement(start)
        metrics.update({
            "generated_report_bytes": sum(len(raw) for raw in reports.values()),
            "generated_report_count": len(reports),
            "rows_per_second": round(specification["unit_count"] / metrics["wall_seconds"], 3),
            "row_unit": specification["unit"],
            "sqlite_statement_count": unavailable("source-audit regeneration opens no instrumented SQLite connection"),
            "workload_semantics": "in-memory regeneration of tracked source-audit reports; not a database or full product build",
        })
        connection.send({"status": "pass", "metrics": metrics})
    except BaseException as exc:  # child must return a bounded diagnostic
        connection.send({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        connection.close()


def _run_profile_trial(workload: str, source_root: Path, repo_root: Path) -> dict[str, Any]:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_profile_child, args=(child, workload, str(source_root), str(repo_root)))
    process.start()
    child.close()
    result = parent.recv()
    process.join()
    if process.exitcode != 0 or result.get("status") != "pass":
        raise BatchM0Error(f"profile workload failed: {workload}: {result.get('error', process.exitcode)}")
    return result["metrics"]


def profile_family_audits(source_root: Path, repo_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for workload in WORKLOADS:
        _run_profile_trial(workload, source_root, repo_root)  # explicit warm-up; not reported as a cold run
        trials = [_run_profile_trial(workload, source_root, repo_root) for _ in range(REPEAT_RUNS)]
        walls = [trial["wall_seconds"] for trial in trials]
        spread = metric_spread_ratio(walls)
        result[workload] = {
            "cache_state": "uncontrolled_os_cache_after_explicit_workload_warmup",
            "repeatability_limit": REPEATABILITY_LIMIT,
            "spread_ratio": round(spread, 6),
            "status": "pass" if spread <= REPEATABILITY_LIMIT else "stop_condition",
            "trials": trials,
            "wall_median_seconds": round(sorted(walls)[1], 6),
        }
    return result


def _textmap_parse_trial(source_root: Path, repeats: int) -> dict[str, Any]:
    start = start_measurement()
    occurrences = 0
    for _pass in range(repeats):
        for relative in TEXTMAP_PATHS:
            value = parse_json_physical((source_root / relative).read_bytes())
            if not isinstance(value, PairsObject):
                raise BatchM0Error(f"TextMap is not an object-pair container: {relative}")
            occurrences += len(value.pairs)
            del value
    metrics = finish_measurement(start)
    metrics.update({
        "passes": repeats,
        "physical_pair_occurrences": occurrences,
        "sqlite_statement_count": unavailable("TextMap parse benchmark opens no SQLite connection"),
        "workload_semantics": "six pinned CHS/EN/JP main+Medium shards parsed with duplicate-preserving pairs",
    })
    return metrics


def _textmap_profile_child(connection: Any, source_root: str, repeats: int) -> None:
    try:
        connection.send({"status": "pass", "metrics": _textmap_parse_trial(Path(source_root), repeats)})
    except BaseException as exc:
        connection.send({"status": "error", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        connection.close()


def _run_textmap_trial(source_root: Path, repeats: int) -> dict[str, Any]:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_textmap_profile_child, args=(child, str(source_root), repeats))
    process.start()
    child.close()
    result = parent.recv()
    process.join()
    if process.exitcode != 0 or result.get("status") != "pass":
        raise BatchM0Error(f"TextMap profile failed: {result.get('error', process.exitcode)}")
    return result["metrics"]


def profile_textmap_reuse(source_root: Path) -> dict[str, Any]:
    _run_textmap_trial(source_root, 1)
    result: dict[str, Any] = {}
    for name, repeats in (("single_pass", 1), ("four_repeated_passes", 4)):
        trials = [_run_textmap_trial(source_root, repeats) for _ in range(REPEAT_RUNS)]
        walls = [trial["wall_seconds"] for trial in trials]
        spread = metric_spread_ratio(walls)
        result[name] = {
            "cache_state": "uncontrolled_os_cache_after_explicit_six-shard_warmup",
            "repeatability_limit": REPEATABILITY_LIMIT,
            "spread_ratio": round(spread, 6),
            "status": "pass" if spread <= REPEATABILITY_LIMIT else "stop_condition",
            "trials": trials,
            "wall_median_seconds": round(sorted(walls)[1], 6),
        }
    single = result["single_pass"]["wall_median_seconds"]
    repeated = result["four_repeated_passes"]["wall_median_seconds"]
    result["observed_repeat_to_single_ratio"] = round(repeated / single, 3) if single else unavailable("single pass measured zero seconds")
    return result


def _hash_parse_task(arguments: tuple[str, str]) -> dict[str, Any]:
    source_root, relative = arguments
    start = start_measurement()
    raw = (Path(source_root) / relative).read_bytes()
    digest = sha256_bytes(raw)
    value = parse_json_physical(raw)
    if not isinstance(value, PairsObject):
        raise BatchM0Error(f"TextMap worker input is not an object: {relative}")
    metrics = finish_measurement(start)
    return {
        "metrics": metrics,
        "pair_count": len(value.pairs),
        "pid": os.getpid(),
        "relative_path": relative,
        "sha256": digest,
    }


def _current_rss_bytes() -> int | None:
    try:
        for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def profile_worker_scaling(source_root: Path) -> dict[str, Any]:
    # Warm every selected file once so 1/2/4 are all explicitly page-cache
    # uncontrolled/warm rather than labelling any run "cold".
    for relative in TEXTMAP_PATHS:
        with (source_root / relative).open("rb") as handle:
            while handle.read(1024 * 1024):
                pass
    measurements: dict[str, Any] = {}
    for workers in (1, 2, 4):
        started = time.perf_counter()
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as executor:
            rows = list(executor.map(_hash_parse_task, [(str(source_root), path) for path in TEXTMAP_PATHS]))
        wall = time.perf_counter() - started
        per_process: dict[int, int] = {}
        for row in rows:
            pid = row["pid"]
            per_process[pid] = max(per_process.get(pid, 0), row["metrics"]["peak_rss_bytes"])
        parent_rss = _current_rss_bytes()
        rss_upper = sum(per_process.values()) + (parent_rss or 0)
        measurements[str(workers)] = {
            "cache_state": "uncontrolled_os_cache_after_explicit_file_warmup",
            "input_files": list(TEXTMAP_PATHS),
            "pair_occurrences": sum(row["pair_count"] for row in rows),
            "parent_current_rss_bytes": parent_rss if parent_rss is not None else unavailable("/proc/self/status unavailable"),
            "peak_rss_upper_bound_bytes": rss_upper,
            "peak_rss_upper_bound_semantics": "sum of each worker high-water mark plus parent current RSS; conservative, not simultaneous sampled RSS",
            "process_count_observed": len(per_process),
            "wall_seconds": round(wall, 6),
            "workers": workers,
        }
    best = min(item["wall_seconds"] for item in measurements.values())
    eligible = [
        int(workers)
        for workers, item in measurements.items()
        if item["wall_seconds"] <= best * (1 + WORKER_BEST_MARGIN)
        and item["peak_rss_upper_bound_bytes"] <= WORKER_RSS_LIMIT_BYTES
    ]
    selected = min(eligible) if eligible else None
    return {
        "best_wall_seconds": best,
        "execution_order": [1, 2, 4],
        "measurement_count_per_worker": 1,
        "measurements": measurements,
        "m1_freeze_gate": "rerun each worker count at least three times with perturbed execution order before freezing the TextMap compiler default",
        "provisional": True,
        "rss_limit_bytes": WORKER_RSS_LIMIT_BYTES,
        "selection_contract": "smallest worker count within best+10% and conservative peak RSS <=8 GiB",
        "selected_worker_count": selected if selected is not None else unavailable("no worker count met both gates"),
        "status": "pass" if selected is not None else "stop_condition",
    }


def _sidecars(path: Path) -> dict[str, bool]:
    return {suffix: Path(str(path) + suffix).exists() for suffix in ("-journal", "-shm", "-wal")}


def accepted_bundle_fingerprint(database: Path, world_receipt: Path, quest_receipt: Path) -> dict[str, Any]:
    database_fp = file_fingerprint(database)
    database_fp["sidecars"] = _sidecars(database)
    return {
        "database": database_fp,
        "quest_receipt": file_fingerprint(quest_receipt),
        "world_receipt": file_fingerprint(world_receipt),
    }


def validate_accepted_bundle(fingerprint: Mapping[str, Any]) -> None:
    database = fingerprint["database"]
    if database["size_bytes"] != ACCEPTED_SCHEMA13_SIZE or database["sha256"] != ACCEPTED_SCHEMA13_SHA256:
        raise BatchM0Error("accepted Schema 13 artifact seal mismatch")
    if any(database["sidecars"].values()):
        raise BatchM0Error("accepted Schema 13 artifact has a SQLite sidecar")
    if fingerprint["world_receipt"]["sha256"] != ACCEPTED_WORLD_RECEIPT_SHA256:
        raise BatchM0Error("accepted World receipt seal mismatch")
    if fingerprint["quest_receipt"]["sha256"] != ACCEPTED_QUEST_RECEIPT_SHA256:
        raise BatchM0Error("accepted QuestCodex receipt seal mismatch")


def accepted_database_canary(database: Path) -> dict[str, Any]:
    uri = f"file:{database.as_posix()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        with sqlite_statement_counter(connection) as statement_count:
            migration_versions = [row[0] for row in connection.execute("SELECT version FROM schema_migration ORDER BY version")]
            values = {
                "ingest_runs": connection.execute("SELECT COUNT(*) FROM ingest_run").fetchone()[0],
                "quest_codex_rows": connection.execute("SELECT COUNT(*) FROM quest_codex_row_occurrence").fetchone()[0],
                "world_archive_rows": connection.execute("SELECT COUNT(*) FROM world_archive_row_occurrence").fetchone()[0],
            }
            count = statement_count()
    finally:
        connection.close()
    if migration_versions != list(range(1, 14)):
        raise BatchM0Error("accepted Schema 13 migration ledger changed")
    return {
        "migration_versions": migration_versions,
        "query_values": values,
        "sqlite_statement_count": count,
        "uri_mode": "mode=ro&immutable=1; PRAGMA query_only=ON",
    }


def environment_fingerprint(path: Path) -> dict[str, Any]:
    memory_bytes: int | dict[str, str] = unavailable("/proc/meminfo unavailable")
    try:
        first = Path("/proc/meminfo").read_text(encoding="ascii").splitlines()[0]
        if first.startswith("MemTotal:"):
            memory_bytes = int(first.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    filesystem: dict[str, Any]
    result = subprocess.run(["findmnt", "-n", "-o", "FSTYPE,SOURCE,TARGET", "-T", str(path)], capture_output=True, text=True)
    if result.returncode == 0:
        fields = result.stdout.strip().split(maxsplit=2)
        filesystem = {"filesystem_type": fields[0], "source": fields[1] if len(fields) > 1 else None, "target": fields[2] if len(fields) > 2 else None}
    else:
        filesystem = unavailable("findmnt did not return filesystem metadata")
    statvfs = os.statvfs(path)
    return {
        "cpu_count_logical": os.cpu_count(),
        "filesystem": filesystem | {
            "fragment_size_bytes": statvfs.f_frsize,
            "free_bytes_at_start": statvfs.f_bavail * statvfs.f_frsize,
        },
        "kernel": platform.release(),
        "machine": platform.machine(),
        "memory_total_bytes": memory_bytes,
        "os": platform.system(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
    }


def _unchanged(before: Mapping[str, Any], after: Mapping[str, Any]) -> bool:
    return before == after


def _decision_inputs(scan: Mapping[str, Any]) -> dict[str, Any]:
    counts = scan["physical_fidelity"]["counts"]
    duplicate_excess = counts.get("duplicate_key_excess", 0)
    numeric_risks = sum(value for key, value in counts.items() if key.startswith("number_lexical_risk:"))
    unicode_escapes = counts.get("unicode_escape_occurrences", 0)
    return {
        "existing_source_file_sufficient": True,
        "existing_source_record_sufficient": duplicate_excess == 0,
        "existing_source_record_assessment": (
            "existing pointer/raw_json columns can represent observed records when M1 freezes pair-ordinal_path_v1 and stores lexical raw JSON; no duplicate keys were observed"
            if duplicate_excess == 0
            else "insufficient without an additive occurrence ordinal because duplicate object keys collide under key-only pointers"
        ),
        "observed_duplicate_key_excess": duplicate_excess,
        "observed_number_lexical_risk_occurrences": numeric_risks,
        "observed_unicode_escape_occurrences": unicode_escapes,
        "member_order_is_physical_evidence": counts.get("objects_with_nonlexical_member_order", 0) > 0,
        "minimum_0015_categories_if_m1_proceeds": [
            "batch wave/shard/import metadata",
            "snapshot-level TextMap physical occurrence coordinates with pair ordinal and raw key/text spelling",
            "table/chunk digest ledger",
        ],
        "explicitly_not_justified": [
            "universal entity/attribute/relation tables",
            "generic family identity inference",
            "byte-span columns (M0 did not implement byte spans)",
        ],
    }


def run_baseline(
    *,
    source_root: Path,
    repo_root: Path,
    database: Path,
    world_receipt: Path,
    quest_receipt: Path,
) -> dict[str, Any]:
    source_root = source_root.resolve()
    repo_root = repo_root.resolve()
    verify_source_pin(source_root)
    environment = environment_fingerprint(source_root)
    before = accepted_bundle_fingerprint(database, world_receipt, quest_receipt)
    validate_accepted_bundle(before)
    query_before = accepted_database_canary(database)
    # Keep the coordinator small until isolated workload processes finish.  A
    # prior ordering scanned the full tree first and made inherited ru_maxrss
    # high-water marks useless for workload-specific observations.
    workloads = profile_family_audits(source_root, repo_root)
    workers = profile_worker_scaling(source_root)
    textmap = profile_textmap_reuse(source_root)
    scan = scan_source_denominator(source_root)
    query_after = accepted_database_canary(database)
    after = accepted_bundle_fingerprint(database, world_receipt, quest_receipt)
    validate_accepted_bundle(after)
    bundle_unchanged = _unchanged(before, after) and query_before == query_after
    repeatability_failures = [name for name, item in workloads.items() if item["status"] != "pass"]
    repeatability_failures.extend(f"textmap:{name}" for name, item in textmap.items() if isinstance(item, dict) and item.get("status") == "stop_condition")
    stop_conditions = {
        "accepted_bundle_or_query_changed": not bundle_unchanged,
        "denominator_not_frozen": not scan["file_level"]["coverage_complete"],
        "physical_coordinates_unstable": bool(scan["physical_fidelity"]["samples"].get("coordinate_failures")),
        "profile_repeatability_over_15_percent": bool(repeatability_failures),
        "worker_selection_failed": workers["status"] != "pass",
    }
    status = "accepted" if not any(stop_conditions.values()) else "blocked"
    report = {
        "accepted_product_unchanged": {
            "after": after,
            "before": before,
            "query_after": query_after,
            "query_before": query_before,
            "status": "pass" if bundle_unchanged else "stop_condition",
        },
        "cache_control": {
            "classification": "uncontrolled_os_cache",
            "cold_cache_claimed": False,
            "note": "No root cache drop was attempted; repeated workloads use explicit user-space warm-up then report OS cache as uncontrolled.",
        },
        "decision_inputs_for_m1": _decision_inputs(scan),
        "environment": environment,
        "identity_policy": "raw_evidence_identity_is_not_logical_identity",
        "limitations": [
            "M0 profiles in-memory source-audit regeneration, not full product builds.",
            "Linux /proc storage read_bytes may be zero for page-cache hits; rchar records broader syscall characters.",
            "Record coverage reports addressable physical top-level occurrences and does not assert family record semantics.",
            "Pair-ordinal coordinates are proven at parsed-value level; byte spans are not implemented.",
            "SQLite statement/index metrics are unavailable for source-only workloads because M0 opens no build connection.",
        ],
        "performance": {
            "family_source_audit_profiles": workloads,
            "textmap_reuse_profile": textmap,
            "worker_scaling": workers,
        },
        "report_kind": "batch_m0_performance_physical_fidelity_baseline",
        "report_version": REPORT_VERSION,
        "source_denominator": scan,
        "source_pin": {
            "commit": SOURCE_COMMIT,
            "roots": list(SOURCE_ROOTS),
            "snapshot_id": SOURCE_SNAPSHOT_ID,
            "tree": SOURCE_TREE,
        },
        "status": status,
        "stop_condition_details": {
            "repeatability_failures": repeatability_failures,
        },
        "stop_conditions": stop_conditions,
    }
    report["portable_evidence_sha256"] = portable_evidence_digest(report)
    return report


def portable_evidence_digest(report: Mapping[str, Any]) -> str:
    selected = {
        "decision_inputs_for_m1": report["decision_inputs_for_m1"],
        "identity_policy": report["identity_policy"],
        "report_kind": report["report_kind"],
        "report_version": report["report_version"],
        "source_denominator": {
            "file_level": report["source_denominator"]["file_level"],
            "record_level": report["source_denominator"]["record_level"],
            "root_summaries": report["source_denominator"]["root_summaries"],
            "physical_fidelity": report["source_denominator"]["physical_fidelity"],
        },
        "source_pin": report["source_pin"],
        "status": report["status"],
        "stop_conditions": report["stop_conditions"],
    }
    return sha256_bytes(canonical_compact(selected))


def render_markdown(report: Mapping[str, Any]) -> str:
    denominator = report["source_denominator"]
    fidelity = denominator["physical_fidelity"]["counts"]
    profiles = report["performance"]["family_source_audit_profiles"]
    lines = [
        "# Batch M0 performance and physical-fidelity baseline",
        "",
        f"Status: **{report['status']}**. This is a diagnostic baseline, not a product release.",
        "",
        "## Frozen denominator",
        "",
        f"- Five-root files: {denominator['file_level']['file_count']:,} ({denominator['file_level']['total_bytes']:,} bytes); file coverage complete.",
        f"- Addressable top-level physical occurrences: {denominator['record_level']['physical_top_level_occurrences']:,}; record/family semantics are not claimed complete.",
        f"- Duplicate-key excess: {fidelity.get('duplicate_key_excess', 0):,}; numeric lexical-risk occurrences: {report['decision_inputs_for_m1']['observed_number_lexical_risk_occurrences']:,}; Unicode escapes: {fidelity.get('unicode_escape_occurrences', 0):,}.",
        f"- Physical file manifest SHA-256: `{denominator['file_level']['physical_file_manifest_sha256']}`.",
        "",
        "## Representative source-audit regeneration",
        "",
        "| Workload | Median wall | Spread | Gate |",
        "| --- | ---: | ---: | --- |",
    ]
    for name, item in sorted(profiles.items()):
        lines.append(f"| `{name}` | {item['wall_median_seconds']:.3f}s | {item['spread_ratio']:.1%} | {item['status']} |")
    worker = report["performance"]["worker_scaling"]
    lines.extend([
        "",
        "## M1 input",
        "",
        f"- Provisional internal hash/parse worker result: `{worker['selected_worker_count']}`.",
        "- The 1/2/4-worker comparison used one measurement per count in fixed `1 → 2 → 4` order with uncontrolled OS cache.",
        "- Before M1 freezes a TextMap compiler default, it must perturb worker-count order and run at least three trials per count.",
        f"- Existing `source_record` sufficient for observed addressable JSON occurrences: `{str(report['decision_inputs_for_m1']['existing_source_record_sufficient']).lower()}`.",
        "- TextMap must remain physical occurrences keyed by snapshot/file/parser contract/pair ordinal; equal hash or text does not merge evidence.",
        "- Byte spans were not implemented and are not justified for Schema 15 by M0.",
        "",
        "## Boundaries",
        "",
        "M0 changed no database, migration, provider, MCP tool, or accepted-product claim. Source-only workload timings are not full-build timings. OS cache was not dropped and no cold-cache claim is made.",
        "",
    ])
    return "\n".join(lines)


def verify_report(report_path: Path, markdown_path: Path, source_root: Path) -> dict[str, Any]:
    raw = report_path.read_bytes()
    try:
        report = json.loads(raw, object_pairs_hook=_strict_object)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise BatchM0Error(f"invalid tracked M0 report: {exc}") from exc
    if not isinstance(report, dict) or raw != canonical_bytes(report):
        raise BatchM0Error("tracked M0 JSON is not byte-exact canonical JSON")
    if report.get("report_kind") != "batch_m0_performance_physical_fidelity_baseline" or report.get("report_version") != REPORT_VERSION:
        raise BatchM0Error("tracked M0 report contract changed")
    expected_digest = portable_evidence_digest(report)
    if report.get("portable_evidence_sha256") != expected_digest:
        raise BatchM0Error("tracked M0 portable evidence digest mismatch")
    if markdown_path.read_text(encoding="utf-8") != render_markdown(report):
        raise BatchM0Error("tracked M0 markdown is not byte-exact for the JSON report")
    verify_source_pin(source_root)
    git_inventory = git_inventory_digest(source_root)
    if git_inventory["sha256"] != report["source_denominator"]["file_level"]["git_inventory_sha256"]:
        raise BatchM0Error("current pinned Git inventory differs from the tracked M0 denominator")
    return {
        "file_count": git_inventory["file_count"],
        "portable_evidence_sha256": expected_digest,
        "status": report["status"],
    }


def validate_tracked_acceptance_contract(
    report: Mapping[str, Any],
    raw: bytes,
    *,
    expected_report_sha256: str,
) -> None:
    """Validate accepted M0 facts against a checker-owned byte seal.

    The portable digest inside the report is reproducibility evidence, not an
    acceptance root: it can be recomputed after a report edit.  The caller
    therefore owns the expected whole-report SHA-256 independently.
    """

    if sha256_bytes(raw) != expected_report_sha256:
        raise BatchM0Error("tracked M0 report byte seal mismatch")
    if set(report) != EXPECTED_TOP_LEVEL_KEYS:
        raise BatchM0Error("tracked M0 top-level key contract changed")
    if report.get("status") != "accepted":
        raise BatchM0Error("tracked M0 status is not accepted")
    expected_stops = {
        "accepted_bundle_or_query_changed": False,
        "denominator_not_frozen": False,
        "physical_coordinates_unstable": False,
        "profile_repeatability_over_15_percent": False,
        "worker_selection_failed": False,
    }
    if report.get("stop_conditions") != expected_stops:
        raise BatchM0Error("tracked M0 stop-condition contract changed")
    expected_pin = {
        "commit": SOURCE_COMMIT,
        "roots": list(SOURCE_ROOTS),
        "snapshot_id": SOURCE_SNAPSHOT_ID,
        "tree": SOURCE_TREE,
    }
    if report.get("source_pin") != expected_pin:
        raise BatchM0Error("tracked M0 source pin contract changed")

    denominator = report.get("source_denominator")
    if not isinstance(denominator, dict) or set(denominator) != {
        "file_level",
        "measurement",
        "physical_fidelity",
        "record_level",
        "root_summaries",
    }:
        raise BatchM0Error("tracked M0 denominator key contract changed")
    expected_file_level = {
        "coverage_complete": True,
        "file_count": EXPECTED_FILE_COUNT,
        "git_inventory_sha256": EXPECTED_GIT_INVENTORY_SHA256,
        "physical_file_manifest_sha256": EXPECTED_PHYSICAL_MANIFEST_SHA256,
        "total_bytes": EXPECTED_FILE_BYTES,
    }
    if denominator.get("file_level") != expected_file_level:
        raise BatchM0Error("tracked M0 file denominator contract changed")
    record_level = denominator.get("record_level")
    if (
        not isinstance(record_level, dict)
        or record_level.get("coverage_complete") is not False
        or record_level.get("physical_top_level_occurrences") != 14_893_173
    ):
        raise BatchM0Error("tracked M0 record-coverage contract changed")
    roots = denominator.get("root_summaries")
    if not isinstance(roots, dict) or set(roots) != set(SOURCE_ROOTS):
        raise BatchM0Error("tracked M0 root summary contract changed")
    for root in SOURCE_ROOTS:
        if (
            roots[root].get("file_count") != EXPECTED_ROOT_FILE_COUNTS[root]
            or roots[root].get("bytes") != EXPECTED_ROOT_BYTES[root]
        ):
            raise BatchM0Error(f"tracked M0 root count/bytes changed: {root}")

    fidelity = denominator.get("physical_fidelity")
    counts = fidelity.get("counts") if isinstance(fidelity, dict) else None
    if (
        not isinstance(fidelity, dict)
        or fidelity.get("coordinate_contract") != "pair_ordinal_path_v1"
        or fidelity.get("byte_spans_implemented") is not False
    ):
        raise BatchM0Error("tracked M0 physical-coordinate contract changed")
    expected_fidelity = {
        "duplicate_key_excess": 0,
        "duplicate_key_groups": 0,
        "maximum_observed_depth": 27,
        "number_kind:number": 10_826_438,
        "number_lexical_risk:decimal": 10_515_608,
        "number_lexical_risk:exponent": 310_830,
        "objects_with_nonlexical_member_order": 13_496_530,
        "tutorial_nested_array_roundtrips": 6_334,
        "unicode_escape_occurrences": 76,
    }
    if not isinstance(counts, dict) or any(counts.get(key) != value for key, value in expected_fidelity.items()):
        raise BatchM0Error("tracked M0 critical physical-fidelity facts changed")
    decisions = report.get("decision_inputs_for_m1")
    expected_decisions = {
        "existing_source_file_sufficient": True,
        "existing_source_record_sufficient": True,
        "member_order_is_physical_evidence": True,
        "observed_duplicate_key_excess": 0,
        "observed_number_lexical_risk_occurrences": 10_826_438,
        "observed_unicode_escape_occurrences": 76,
    }
    if not isinstance(decisions, dict) or any(decisions.get(key) != value for key, value in expected_decisions.items()):
        raise BatchM0Error("tracked M0 decision inputs changed")
    accepted = report.get("accepted_product_unchanged")
    if (
        not isinstance(accepted, dict)
        or accepted.get("status") != "pass"
        or accepted.get("before") != accepted.get("after")
        or accepted.get("query_before") != accepted.get("query_after")
    ):
        raise BatchM0Error("tracked M0 accepted-product preservation changed")
    _validate_metrics_shape(report)


def _validate_metrics_shape(report: Mapping[str, Any]) -> None:
    denominator_measurement = report["source_denominator"].get("measurement")
    if not isinstance(denominator_measurement, dict):
        raise BatchM0Error("tracked M0 denominator measurement is missing")
    required_measurements = {
        "cpu_system_seconds",
        "cpu_user_seconds",
        "index_build_time",
        "linux_proc_io",
        "peak_rss_bytes",
        "phase_wall_seconds",
        "sqlite_statement_count",
        "wall_seconds",
    }
    phases = denominator_measurement.get("phase_wall_seconds")
    if (
        not required_measurements.issubset(denominator_measurement)
        or not _positive_number(denominator_measurement.get("wall_seconds"))
        or not _positive_number(denominator_measurement.get("peak_rss_bytes"))
        or not isinstance(phases, dict)
        or set(phases) != {"file_read", "json_analysis", "json_decode", "sha256"}
        or any(not _positive_number(value) for value in phases.values())
    ):
        raise BatchM0Error("tracked M0 denominator metric shape changed")
    performance = report.get("performance")
    if not isinstance(performance, dict) or set(performance) != {
        "family_source_audit_profiles",
        "textmap_reuse_profile",
        "worker_scaling",
    }:
        raise BatchM0Error("tracked M0 performance key contract changed")
    workloads = performance["family_source_audit_profiles"]
    if not isinstance(workloads, dict) or set(workloads) != set(WORKLOADS):
        raise BatchM0Error("tracked M0 workload set changed")
    for name, profile in workloads.items():
        _validate_three_trial_profile(profile, f"workload {name}")
    textmap = performance["textmap_reuse_profile"]
    if not isinstance(textmap, dict) or set(textmap) != {
        "four_repeated_passes",
        "observed_repeat_to_single_ratio",
        "single_pass",
    }:
        raise BatchM0Error("tracked M0 TextMap profile key contract changed")
    _validate_three_trial_profile(textmap["single_pass"], "TextMap single pass")
    _validate_three_trial_profile(textmap["four_repeated_passes"], "TextMap four pass")
    scaling = performance["worker_scaling"]
    if (
        not isinstance(scaling, dict)
        or scaling.get("status") != "pass"
        or scaling.get("selected_worker_count") != 4
        or scaling.get("execution_order") != [1, 2, 4]
        or scaling.get("measurement_count_per_worker") != 1
        or scaling.get("provisional") is not True
        or set(scaling.get("measurements", {})) != {"1", "2", "4"}
    ):
        raise BatchM0Error("tracked M0 provisional worker-scaling contract changed")
    for key, metric in scaling["measurements"].items():
        if (
            not isinstance(metric, dict)
            or metric.get("workers") != int(key)
            or metric.get("cache_state") != "uncontrolled_os_cache_after_explicit_file_warmup"
            or metric.get("pair_occurrences") != 2_474_525
            or not _positive_number(metric.get("wall_seconds"))
            or not _positive_number(metric.get("peak_rss_upper_bound_bytes"))
        ):
            raise BatchM0Error(f"tracked M0 worker metric shape changed: {key}")


def _validate_three_trial_profile(profile: Any, label: str) -> None:
    if (
        not isinstance(profile, dict)
        or profile.get("status") != "pass"
        or profile.get("repeatability_limit") != REPEATABILITY_LIMIT
        or not isinstance(profile.get("trials"), list)
        or len(profile["trials"]) != REPEAT_RUNS
        or not _positive_number(profile.get("wall_median_seconds"))
        or not isinstance(profile.get("spread_ratio"), (int, float))
        or isinstance(profile.get("spread_ratio"), bool)
        or not math.isfinite(profile["spread_ratio"])
        or profile["spread_ratio"] < 0
        or profile["spread_ratio"] > REPEATABILITY_LIMIT
    ):
        raise BatchM0Error(f"tracked M0 profile shape changed: {label}")
    required = {"cpu_system_seconds", "cpu_user_seconds", "linux_proc_io", "peak_rss_bytes", "wall_seconds"}
    for trial in profile["trials"]:
        if (
            not isinstance(trial, dict)
            or not required.issubset(trial)
            or not _positive_number(trial.get("wall_seconds"))
            or not _positive_number(trial.get("peak_rss_bytes"))
        ):
            raise BatchM0Error(f"tracked M0 trial metric shape changed: {label}")


def _positive_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result
