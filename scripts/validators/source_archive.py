#!/usr/bin/env python3
"""Independent source-to-row verifier for the snapshot-local 7.1 R0 artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import time
from collections import Counter
from pathlib import Path

COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
SNAPSHOT = "anime-game-data:7.1:" + COMMIT
PARSER = "pb-batch-m3-r0-physical-7.1:23e78b08f997ab610cd55a11f91f2f4c252260af"
RUN = "ir-batch-m3-r0-physical-7.1:9fc2c32cc6bb81dd6091e504d43e40bdcf930ea2"
ROOTS = ("BinOutput", "ExcelBinOutput", "Readable", "Subtitle", "TextMap")


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def skip_ws(text: str, position: int) -> int:
    while position < len(text) and text[position] in " \t\r\n":
        position += 1
    return position


def escape_key(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1").replace(":", "~2")


def lexical_occurrences(raw: bytes):
    text = raw.decode("utf-8-sig")
    decoder = json.JSONDecoder()
    position = skip_ws(text, 0)
    if position >= len(text):
        raise ValueError("empty JSON")
    opener = text[position]
    position += 1
    ordinal = 0
    if opener == "[":
        kind = "array"
    elif opener == "{":
        kind = "object"
    else:
        decoder.raw_decode(text, position - 1)
        return "scalar", []
    rows = []
    while True:
        position = skip_ws(text, position)
        closer = "]" if kind == "array" else "}"
        if position < len(text) and text[position] == closer:
            if skip_ws(text, position + 1) != len(text):
                raise ValueError("trailing JSON bytes")
            return kind, rows
        if kind == "array":
            start = position
            _, end = decoder.raw_decode(text, position)
            pointer = f"/#{ordinal}"
            token = text[start:end]
        else:
            start = position
            key, key_end = decoder.raw_decode(text, position)
            if not isinstance(key, str):
                raise ValueError("object key")
            position = skip_ws(text, key_end)
            if position >= len(text) or text[position] != ":":
                raise ValueError("object colon")
            position = skip_ws(text, position + 1)
            _, end = decoder.raw_decode(text, position)
            pointer = f"/@{ordinal}:{escape_key(key)}"
            token = text[start:end]
        rows.append((ordinal, pointer, hashlib.sha256(token.encode()).hexdigest()))
        ordinal += 1
        position = skip_ws(text, end)
        if position >= len(text):
            raise ValueError("unterminated JSON")
        if text[position] == ",":
            position += 1
        elif text[position] != closer:
            raise ValueError("JSON delimiter")


def scan(path: Path, relative: str):
    raw = path.read_bytes()
    source_sha = hashlib.sha256(raw).hexdigest()
    root = relative.split("/", 1)[0]
    if path.suffix.lower() != ".json":
        return (relative, root, source_sha, len(raw), "not_parsed_non_json", "non_json", "unsupported_record_layout", 0), []
    try:
        kind, rows = lexical_occurrences(raw)
    except (UnicodeError, ValueError, json.JSONDecodeError, RecursionError):
        layout = "empty_json_container" if not raw else "json_parse_failed"
        return (relative, root, source_sha, len(raw), "unparsed_container", layout, "unparsed_container", 0), []
    if kind == "array":
        layout, status = "top_level_array", "physical_top_level_occurrences_addressable"
    elif kind == "object":
        layout = "top_level_object_pairs"
        status = "physical_top_level_occurrences_addressable" if root == "TextMap" else "physical_members_addressable_record_semantics_unsupported"
    else:
        layout, status, rows = "top_level_scalar", "unsupported_record_layout", []
    return (relative, root, source_sha, len(raw), "parsed_json", layout, status, len(rows)), rows


def source_paths(source: Path) -> list[str]:
    """Enumerate the fixed source boundary independently of artifact row claims."""
    paths = []

    def unreadable(error):
        raise SystemExit("source inventory cannot traverse: " + str(error))

    for name in ROOTS:
        root = source / name
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise SystemExit("fixed source root unavailable: " + name)
        for current, directories, files in os.walk(root, followlinks=False, onerror=unreadable):
            for directory in directories:
                path = Path(current) / directory
                info = path.lstat()
                if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    raise SystemExit("source inventory non-directory: " + str(path))
            for file in files:
                path = Path(current) / file
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise SystemExit("source inventory non-regular file: " + str(path))
                paths.append(path.relative_to(source).as_posix())
    return sorted(paths, key=lambda value: value.encode("utf-8", "surrogateescape"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--artifact", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    started = time.monotonic()
    pins = subprocess.check_output(["git", "-C", str(args.source), "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines()
    clean = not subprocess.check_output(["git", "-C", str(args.source), "status", "--porcelain"])
    if pins != [COMMIT, TREE] or not clean:
        raise SystemExit("unqualified 7.1 source checkout")
    before = sha_file(args.artifact)
    sidecars = [str(args.artifact) + suffix for suffix in ("-wal", "-shm", "-journal") if Path(str(args.artifact) + suffix).exists()]
    if sidecars:
        raise SystemExit("SQLite sidecars present")
    connection = sqlite3.connect(args.artifact.absolute().as_uri() + "?mode=ro&immutable=1", uri=True)
    metadata = {key: json.loads(value) for key, value in connection.execute("SELECT key,value FROM metadata")}
    # Storage v2 inherits exact binding strings from the referenced file and
    # decodes SHA-256 bytes. Independently check all backing rows, including
    # any orphan that a JOIN view would otherwise hide from the source oracle.
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    schema = metadata.get('schema_version')
    if (version,schema) not in ((1,'batch-m3-r0-physical-occurrence-v1'),
                               (2,'batch-m3-r0-physical-occurrence-storage-v2')):
        raise SystemExit('R0 storage version mismatch')
    if version == 2:
        tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != {'source_file','physical_occurrence_data','metadata'}:
            raise SystemExit('R0 storage table closure mismatch')
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise SystemExit('R0 backing occurrence orphan')
        n=connection.execute('SELECT COUNT(*) FROM physical_occurrence_data').fetchone()[0]
        if connection.execute("SELECT 1 FROM physical_occurrence_data WHERE typeof(value_sha256_bytes)!='blob' OR length(value_sha256_bytes)!=32 LIMIT 1").fetchone():
            raise SystemExit('R0 backing occurrence hash shape mismatch')
    if (metadata["snapshot_id"], metadata["parser_build_id"], metadata["ingest_run_id"]) != (SNAPSHOT, PARSER, RUN):
        raise SystemExit("R0 identity mismatch")
    file_rows = connection.execute("SELECT source_file_id,relative_path,root,source_sha256,size_bytes,parse_status,layout,record_status,occurrence_count FROM source_file ORDER BY source_file_id").fetchall()
    if metadata.get("source_roots") != list(ROOTS) or [row[1] for row in file_rows] != source_paths(args.source):
        raise SystemExit("source file set mismatch")
    occurrences = connection.execute("SELECT occurrence_id,source_file_id,ordinal,pointer,value_sha256,snapshot_id,parser_build_id,ingest_run_id FROM physical_occurrence ORDER BY occurrence_id")
    next_occurrence = occurrences.fetchone()
    files = records = bytes_total = 0
    states = Counter()
    for row in file_rows:
        files += 1
        file_id, relative = int(row[0]), str(row[1])
        expected_file, expected_occurrences = scan(args.source / relative, relative)
        actual_file = (relative, str(row[2]), str(row[3]), int(row[4]), str(row[5]), str(row[6]), str(row[7]), int(row[8]))
        if file_id != files or actual_file != expected_file:
            raise SystemExit("source file closure mismatch: " + relative)
        bytes_total += actual_file[3]
        states[actual_file[6]] += 1
        for expected in expected_occurrences:
            if next_occurrence is None:
                raise SystemExit("occurrence shortage")
            occurrence_id, occurrence_file, ordinal, pointer, value_sha, snapshot, parser, run = next_occurrence
            records += 1
            if (int(occurrence_id), int(occurrence_file), int(ordinal), str(pointer), str(value_sha), snapshot, parser, run) != (records, file_id, *expected, SNAPSHOT, PARSER, RUN):
                raise SystemExit("occurrence closure mismatch: " + relative)
            next_occurrence = occurrences.fetchone()
    if next_occurrence is not None:
        raise SystemExit("extra occurrence")
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or connection.execute("PRAGMA foreign_key_check").fetchall():
        raise SystemExit("SQLite integrity")
    connection.close()
    if version == 2 and records != n:
        raise SystemExit('R0 backing occurrence closure mismatch')
    after = sha_file(args.artifact)
    if before != after or any(Path(str(args.artifact) + suffix).exists()
                              for suffix in ("-wal", "-shm", "-journal")):
        raise SystemExit("artifact changed or SQLite sidecars appeared during verification")
    result = {
        "contract": "7.1-r0-independent-verifier-v1", "status": "PASS",
        "artifact": {"size_bytes": args.artifact.stat().st_size, "sha256": after},
        "identity": {"snapshot_id": SNAPSHOT, "parser_build_id": PARSER, "ingest_run_id": RUN},
        "file_count": files, "file_bytes": bytes_total, "occurrence_count": records,
        "record_status_counts": dict(sorted(states.items())), "source_coordinate_equality": True,
        "artifact_unchanged_during_verify": before == after, "sidecars_absent": True,
        "source_checkout_clean": clean, "elapsed_seconds": time.monotonic() - started,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
