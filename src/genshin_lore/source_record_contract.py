"""Builder-independent immutable source-record row contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class SourceRecordContract:
    id: str
    source_file_id: str
    pointer: str
    record_kind: str
    native_id: str | None
    raw_sha256: str
    raw_json: str | None

    @classmethod
    def from_row(
        cls,
        row: Mapping[str, Any],
        *,
        record_id_key: str = "id",
        native_id_key: str = "native_id",
    ) -> "SourceRecordContract":
        native_id = row[native_id_key]
        raw_json = row["raw_json"]
        return cls(
            id=str(row[record_id_key]),
            source_file_id=str(row["source_file_id"]),
            pointer=str(row["pointer"]),
            record_kind=str(row["record_kind"]),
            native_id=None if native_id is None else str(native_id),
            raw_sha256=str(row["raw_sha256"]),
            raw_json=None if raw_json is None else str(raw_json),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


__all__ = ["SourceRecordContract"]
