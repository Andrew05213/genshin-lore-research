"""Explicit 7.1 Document/Localization physical field decoder candidate.

The 7.0 builder's field mapping is unchanged. This module only reads
source-authored 7.1 coordinates; it does not resolve cross-version identity.
"""
from __future__ import annotations

CHS_READABLE_FIELDS = ("MBOPLLJEHCJ", "FCNGPFLCLFA")
READABLE_FIELDS = (
    ("MBOPLLJEHCJ", "CHS"),
    ("FCNGPFLCLFA", "CHS"),
    ("enPath", "EN"),
    ("jpPath", "JP"),
)


def document_row(row: dict, ordinal: int) -> dict:
    if not isinstance(row, dict) or type(row.get("id")) is not int or type(row.get("titleTextMapHash")) is not int:
        raise ValueError("7.1 Document row id/titleTextMapHash shape")
    for field in ("questIDList", "contentLocalizedId", "questContentLocalizedId"):
        if field in row and (not isinstance(row[field], list) or any(type(value) is not int for value in row[field])):
            raise ValueError(f"7.1 Document {field} physical locator shape")
    return {"pointer": f"/{ordinal}", "native_id": row["id"],
            "title_text_hash": row["titleTextMapHash"]}


def localization_readable_locators(row: dict, ordinal: int) -> list[dict]:
    if not isinstance(row, dict) or type(row.get("id")) is not int:
        raise ValueError("7.1 Localization row id shape")
    if any(field not in row for field in CHS_READABLE_FIELDS):
        raise ValueError("7.1 CHS authored locator field missing")
    result = []
    for field, locale in READABLE_FIELDS:
        if field not in row:
            continue
        value = row[field]
        if not isinstance(value, str):
            raise ValueError(f"7.1 Localization {field} must be a raw string")
        prefix = f"ART/UI/Readable/{locale}/"
        suffix = value[len(prefix):] if value.startswith(prefix) else None
        valid = suffix is not None and bool(suffix) and all(
            part not in ("", ".", "..") for part in suffix.split("/")
        )
        result.append({
            "pointer": f"/{ordinal}/{field}",
            "native_id": row["id"],
            "field": field,
            "locale": locale,
            "raw_locator": value,
            "target_path": "Readable/" + value[len("ART/UI/Readable/"):] + ".txt" if valid else None,
            "status": "mapped_source_path" if valid else "mapping_unresolved",
        })
    return result
