"""Mechanical protocol for family-specific batch projection shards.

This module intentionally knows nothing about native identifiers, relations,
text owners, or filters.  A family supplies table DDL, columns, rows, and a
physical-identity sort key; the shared layer only validates and serialises
those declarations.  In particular, there is no common entity table here.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence


class ShardProtocolError(ValueError):
    """A family shard declaration is not mechanically safe to materialise."""


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _identifier(value: str, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ShardProtocolError(f"invalid SQLite {label}: {value!r}")
    return value


def _json_value(value: Any) -> Any:
    """Return a stable, lossless representation suitable for row digests."""

    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    raise ShardProtocolError(f"row value is not SQLite/JSON serialisable: {type(value)!r}")


def canonical_row(row: Sequence[Any]) -> str:
    return json.dumps(
        [_json_value(value) for value in row],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def rowset_digest(rows: Iterable[Sequence[Any]]) -> str:
    digest = hashlib.sha256()
    for row in rows:
        digest.update(canonical_row(row).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


SortKey = Callable[[Sequence[Any]], tuple[Any, ...]]


@dataclass(frozen=True)
class TableSpec:
    """Family-owned table shape and natural physical ordering.

    ``sort_key`` is deliberately supplied by the family adapter.  The shared
    protocol never guesses which column is an identity or relation field.
    """

    name: str
    create_sql: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...] = ()
    sort_key: SortKey | None = None
    order_columns: tuple[str, ...] = ()
    index_sql: tuple[str, ...] = ()
    foreign_key_check: bool = False

    def __post_init__(self) -> None:
        _identifier(self.name, "table name")
        if not self.create_sql.lstrip().upper().startswith("CREATE TABLE"):
            raise ShardProtocolError(f"table {self.name} must provide CREATE TABLE DDL")
        if not self.columns:
            raise ShardProtocolError(f"table {self.name} must declare columns")
        for column in self.columns:
            _identifier(column, f"column in {self.name}")
        for column in self.order_columns:
            if column not in self.columns:
                raise ShardProtocolError(
                    f"table {self.name} order column is not declared: {column}"
                )
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ShardProtocolError(
                    f"table {self.name} row has {len(row)} values, expected {len(self.columns)}"
                )
        for statement in self.index_sql:
            if not statement.lstrip().upper().startswith("CREATE INDEX"):
                raise ShardProtocolError(f"table {self.name} has non-index SQL")

    def ordered_rows(self) -> tuple[tuple[Any, ...], ...]:
        key = self.sort_key or (lambda row: tuple(_json_value(value) for value in row))
        # A family sort key must be total.  The canonical row is a final tie
        # breaker and keeps equal physical keys as separate occurrences.
        return tuple(sorted(self.rows, key=lambda row: (key(row), canonical_row(row))))


@dataclass(frozen=True)
class FamilyShardSpec:
    family: str
    family_contract_version: str
    ingest_run_id: str
    snapshot_id: str
    parser_build_id: str
    coverage_level: str
    corpus_complete: bool
    product_accepted: bool
    read_set: tuple[str, ...]
    write_set: tuple[str, ...]
    tables: tuple[TableSpec, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    shard_schema_version: int = 1

    def __post_init__(self) -> None:
        for label, value in (
            ("family", self.family),
            ("family_contract_version", self.family_contract_version),
            ("ingest_run_id", self.ingest_run_id),
            ("snapshot_id", self.snapshot_id),
            ("parser_build_id", self.parser_build_id),
            ("coverage_level", self.coverage_level),
        ):
            if not isinstance(value, str) or not value:
                raise ShardProtocolError(f"{label} must be a non-empty string")
        if self.corpus_complete and self.coverage_level == "R1_structured_family":
            # A complete family boundary may still be non-product; this guard
            # only prevents the protocol from silently accepting an impossible
            # empty declaration.
            if not self.tables:
                raise ShardProtocolError("complete structured shard has no family tables")
        if self.product_accepted:
            raise ShardProtocolError("M2 shards cannot be product accepted")
        if self.shard_schema_version < 1:
            raise ShardProtocolError("shard schema version must be positive")
        names = [table.name for table in self.tables]
        if len(names) != len(set(names)):
            raise ShardProtocolError("family shard table names must be unique")
        if tuple(self.write_set) != tuple(names):
            raise ShardProtocolError(
                "write_set must exactly declare family-owned shard tables"
            )

    @property
    def table_names(self) -> tuple[str, ...]:
        return tuple(table.name for table in self.tables)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "family_contract_version": self.family_contract_version,
            "ingest_run_id": self.ingest_run_id,
            "snapshot_id": self.snapshot_id,
            "parser_build_id": self.parser_build_id,
            "coverage_level": self.coverage_level,
            "corpus_complete": self.corpus_complete,
            "product_accepted": self.product_accepted,
            "read_set": list(self.read_set),
            "write_set": list(self.write_set),
            "tables": list(self.table_names),
            "shard_schema_version": self.shard_schema_version,
            "metadata": dict(self.metadata),
        }


def schema_digest(create_sql: str) -> str:
    return hashlib.sha256(" ".join(create_sql.split()).encode("utf-8")).hexdigest()


def spec_digest(spec: FamilyShardSpec) -> str:
    payload = {
        "identity": {
            "family": spec.family,
            "contract": spec.family_contract_version,
            "run": spec.ingest_run_id,
            "snapshot": spec.snapshot_id,
            "parser": spec.parser_build_id,
        },
        "coverage_level": spec.coverage_level,
        "corpus_complete": spec.corpus_complete,
        "product_accepted": spec.product_accepted,
        "read_set": list(spec.read_set),
        "write_set": list(spec.write_set),
        "tables": [
            {
                "name": table.name,
                "schema_sha256": schema_digest(table.create_sql),
                "columns": list(table.columns),
                "rows": [canonical_row(row) for row in table.ordered_rows()],
            }
            for table in sorted(spec.tables, key=lambda item: item.name)
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


__all__ = [
    "FamilyShardSpec",
    "ShardProtocolError",
    "SortKey",
    "TableSpec",
    "canonical_row",
    "rowset_digest",
    "schema_digest",
    "spec_digest",
]
