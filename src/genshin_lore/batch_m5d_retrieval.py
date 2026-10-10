"""Family-specific routed retrieval for the non-product M5D candidate.

Qualification is performed by :mod:`batch_m5d_runtime` exactly once.  This
module only performs scoped reads against the resulting immutable snapshot.
The query loops are mechanical; table links, sort fields, and search tables
are literal family declarations and never form a cross-family model.
"""
from __future__ import annotations

import base64
import binascii
from functools import cmp_to_key
import json
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ._readonly_database import ReadonlyDatabase
from .batch_m5d_runtime import (
    M5DChildTableDeclaration,
    M5DFamilyDeclaration,
    M5DQualification,
    M5D_SEARCH_ORDER,
    M5D_SEARCH_TABLES,
    family_capability,
)
from .family_evidence import FamilyEvidenceResult
from .runtime_stability import stable_routed_call
from .util import canonical_json


class BatchM5DRetrievalError(RuntimeError):
    """A qualified M5D provider cannot execute its family-local read."""


# Every entry is explicit.  Optional zero-row child shapes are absent, exactly
# as they are absent from the M5C shard contract.
def _children(prefix: str, *, text: bool, relation: bool, asset: bool, opaque: bool) -> tuple[M5DChildTableDeclaration, ...]:
    result: list[M5DChildTableDeclaration] = [
        M5DChildTableDeclaration(f"{prefix}_raw_field_occurrence", "row_occurrence_id"),
    ]
    if relation:
        relation_table = f"{prefix}_relationship_occurrence"
        result.extend((
            M5DChildTableDeclaration(relation_table, "row_occurrence_id"),
            M5DChildTableDeclaration(f"{prefix}_relationship_candidate_occurrence", "relationship_occurrence_id", relation_table, "occurrence_id"),
        ))
    if text:
        owner_table = f"{prefix}_text_owner_occurrence"
        resolution_table = f"{prefix}_text_resolution_occurrence"
        result.extend((
            M5DChildTableDeclaration(owner_table, "row_occurrence_id"),
            M5DChildTableDeclaration(resolution_table, "owner_occurrence_id", owner_table, "occurrence_id"),
            M5DChildTableDeclaration(f"{prefix}_text_candidate_occurrence", "resolution_occurrence_id", resolution_table, "occurrence_id"),
        ))
    if opaque:
        result.append(M5DChildTableDeclaration(f"{prefix}_opaque_occurrence", "row_occurrence_id"))
    if asset:
        result.append(M5DChildTableDeclaration(f"{prefix}_asset_occurrence", "row_occurrence_id"))
    return tuple(result)


M5D_QUERY_CHILDREN: dict[str, tuple[M5DChildTableDeclaration, ...]] = {
    "avatar_codex_archive": (
        # Historical Avatar raw fields use `row_occurrence_id`; relation/opaque rows
        # retain `source_row_occurrence_id`.  Keep these family-specific links
        # literal rather than routing through a universal parent adapter.
        M5DChildTableDeclaration("avatar_codex_raw_field_occurrence", "row_occurrence_id"),
        M5DChildTableDeclaration("avatar_codex_relationship_occurrence", "source_row_occurrence_id"),
        M5DChildTableDeclaration("avatar_codex_relationship_candidate_occurrence", "relationship_occurrence_id", "avatar_codex_relationship_occurrence", "id", "source_row_occurrence_id"),
        M5DChildTableDeclaration("avatar_codex_opaque_occurrence", "source_row_occurrence_id"),
    ),
    "cook_recipe_archive": _children("cook_recipe_archive", text=True, relation=True, asset=True, opaque=True),
    "cook_bonus_archive": _children("cook_bonus_archive", text=False, relation=True, asset=False, opaque=True),
    "homeworld_furniture_archive": _children("homeworld_furniture_archive", text=True, relation=True, asset=True, opaque=True),
    "furniture_make_archive": _children("furniture_make_archive", text=False, relation=True, asset=False, opaque=True),
    "furniture_suite_archive": _children("furniture_suite_archive", text=True, relation=True, asset=True, opaque=True),
    "homeworld_furniture_type_archive": _children("homeworld_furniture_type_archive", text=True, relation=False, asset=True, opaque=True),
    "homeworld_bgm_archive": _children("homeworld_bgm_archive", text=True, relation=False, asset=True, opaque=True),
    "profile_picture_archive": _children("profile_picture_archive", text=True, relation=True, asset=True, opaque=True),
    "profile_frame_archive": _children("profile_frame_archive", text=True, relation=False, asset=True, opaque=True),
    "gadget_title_archive": _children("gadget_title_archive", text=True, relation=True, asset=False, opaque=False),
}


def _q(identifier: str) -> str:
    if not isinstance(identifier, str) or not identifier or not identifier.replace("_", "").isalnum() or identifier[0].isdigit():
        raise BatchM5DRetrievalError(f"unsafe identifier: {identifier!r}")
    return '"' + identifier.replace('"', '""') + '"'


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _limit(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        raise ValueError("limit must be between 1 and 100")
    return value


def _json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BatchM5DRetrievalError("stored evidence JSON is invalid") from exc


def _row_dict(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): row[key] for key in row.keys()}


def _encode_cursor(scope: Mapping[str, Any], key: list[Any]) -> str:
    raw = canonical_json({"version": 1, "scope": dict(scope), "key": key}).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str | None, scope: Mapping[str, Any]) -> list[Any] | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError("cursor must be a non-empty string")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, TypeError, UnicodeError, binascii.Error, json.JSONDecodeError) as exc:
        raise ValueError("cursor is invalid") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1 or payload.get("scope") != dict(scope):
        raise ValueError("cursor scope does not match request")
    key = payload.get("key")
    if not isinstance(key, list) or not key:
        raise ValueError("cursor position is invalid")
    return key


def _sort_atom(value: Any) -> tuple[int, Any]:
    """Return a typed sort atom for diagnostics/cursor evidence.

    Ordering itself is implemented by ``_compare_atom`` below.  Keeping the
    value typed here is important: converting ``10`` to ``"10"`` would make
    a numeric family order lexicographic (and put 10 before 2).
    """
    if value is None:
        return (0, "")
    if isinstance(value, bool):
        return (1, int(value))
    if isinstance(value, (int, float)):
        return (2, value)
    if isinstance(value, str):
        return (3, value)
    return (4, canonical_json(value))


def _compare_atom(left: Any, right: Any) -> int:
    """Compare evidence values without erasing their JSON/SQLite types."""
    left_atom, right_atom = _sort_atom(left), _sort_atom(right)
    if left_atom[0] != right_atom[0]:
        return -1 if left_atom[0] < right_atom[0] else 1
    lvalue, rvalue = left_atom[1], right_atom[1]
    if isinstance(lvalue, (int, float)) and not isinstance(lvalue, bool) \
            and isinstance(rvalue, (int, float)) and not isinstance(rvalue, bool):
        if lvalue < rvalue:
            return -1
        if lvalue > rvalue:
            return 1
        return 0
    if lvalue < rvalue:
        return -1
    if lvalue > rvalue:
        return 1
    return 0


def _compare_ordered_keys(left: list[Any], right: list[Any], tokens: Sequence[str]) -> int:
    for index, (lvalue, rvalue) in enumerate(zip(left, right)):
        comparison = _compare_atom(lvalue, rvalue)
        if comparison:
            token = tokens[index] if index < len(tokens) else ""
            if str(token).rsplit(" ", 1)[-1].lower() == "desc":
                comparison = -comparison
            return comparison
    if len(left) != len(right):
        return -1 if len(left) < len(right) else 1
    return 0


def _filter_value(definition: Mapping[str, Any], value: Any, label: str) -> None:
    if value is None:
        raise ValueError(f"filters.{label} must not be null")
    kind = definition.get("value_type")
    if kind == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
        raise ValueError(f"filters.{label} must be an integer")
    if kind == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise ValueError(f"filters.{label} must be a number")
    if kind == "boolean" and not isinstance(value, bool):
        raise ValueError(f"filters.{label} must be a boolean")
    if kind == "string" and (not isinstance(value, str) or not value):
        raise ValueError(f"filters.{label} must be a non-empty string")
    if kind == "enum":
        allowed = definition.get("allowed_values", ())
        if value not in allowed:
            raise ValueError(f"filters.{label} must be one of {','.join(str(item) for item in allowed)}")


@dataclass(frozen=True)
class _SearchHit:
    row: Mapping[str, Any]
    owner: Mapping[str, Any]
    resolution: Mapping[str, Any]
    candidate: Mapping[str, Any]


class BatchM5DFamilyRepository:
    """One explicit M5D family Repository over one qualified candidate."""

    def __init__(self, db: ReadonlyDatabase, declaration: M5DFamilyDeclaration, qualification: M5DQualification) -> None:
        if declaration.family not in M5D_SEARCH_TABLES:
            raise BatchM5DRetrievalError(f"unknown M5D family: {declaration.family}")
        self.db = db
        self.declaration = declaration
        self.qualification = qualification
        self.family = declaration.family
        self.family_contract_version = declaration.family_contract_version
        self._table = declaration.table_name
        self._primary_id = declaration.primary_id_column
        self._stability = qualification.stability
        if qualification.status != "verified":
            raise BatchM5DRetrievalError("M5D qualification is not verified")

    def capability(self) -> dict[str, Any]:
        return family_capability(self.declaration, self.qualification)

    def _result(self, status: str, run: str, data: dict[str, Any]) -> FamilyEvidenceResult:
        return FamilyEvidenceResult(
            status, self.family, run, self.family_contract_version,
            self.declaration.coverage_level, False, data,
        )

    def _scope(self, run: Any, contract: Any, operation: str) -> FamilyEvidenceResult | None:
        requested = _text(run, "ingest_run_id")
        if contract != self.family_contract_version:
            return self._result("not_found", requested, {
                "status": "NotFound", "reason": "family_contract_version_mismatch",
                "lookup": {"operation": operation, "requested": contract, "supported": self.family_contract_version},
            })
        if requested != self.declaration.ingest_run_id:
            return self._result("not_found", requested, {
                "status": "NotFound", "reason": "ingest_run_ineligible",
                "lookup": {"operation": operation, "ingest_run_id": requested},
            })
        return None

    def _filter_definitions(self, operation: str) -> dict[str, Mapping[str, Any]]:
        return {
            str(item["name"]): item for item in self.declaration.filters
            if operation in item.get("supported_operations", ())
        }

    def _filters(self, filters: dict[str, Any] | None, operation: str) -> dict[str, Any]:
        if filters is None:
            return {}
        if not isinstance(filters, dict):
            raise ValueError("filters must be an object")
        definitions = self._filter_definitions(operation)
        all_names = {str(item["name"]) for item in self.declaration.filters}
        unknown = sorted(set(filters) - all_names)
        if unknown:
            raise ValueError(f"unsupported filters: {', '.join(unknown)}")
        not_applicable = sorted(set(filters) - set(definitions))
        if not_applicable:
            raise ValueError(f"filter not applicable to {operation}: {', '.join(not_applicable)}")
        selected: dict[str, Any] = {}
        for name, value in filters.items():
            _filter_value(definitions[name], value, name)
            selected[name] = value
        return selected

    def _raw_record(self, row: Mapping[str, Any]) -> dict[str, Any]:
        if self.family == "avatar_codex_archive":
            metadata = _json(row["metadata_json"], {})
            raw = metadata.get("raw_record_json") if isinstance(metadata, dict) else None
        else:
            # sqlite3.Row supports indexing but not Mapping.get.
            raw = row["raw_record_json"]
        parsed = _json(raw, {})
        if not isinstance(parsed, dict):
            raise BatchM5DRetrievalError(f"{self.family} primary raw record is not an object")
        return parsed

    def _matches(self, row: Mapping[str, Any], selected: Mapping[str, Any]) -> bool:
        raw = self._raw_record(row)
        definitions = {str(item["name"]): item for item in self.declaration.filters}
        for name, value in selected.items():
            if name == "locale":
                continue
            definition = definitions[name]
            source = str(definition.get("source_field", name))
            if source.endswith("[]"):
                source = source[:-2]
                observed = raw.get(source)
                if not isinstance(observed, list) or not any(member == value for member in observed):
                    return False
            elif raw.get(source) != value:
                return False
        return True

    def _primary_rows(self) -> list[Mapping[str, Any]]:
        rows = self.db.conn.execute(
            f"SELECT * FROM {_q(self._table)} WHERE ingest_run_id=?",
            (self.declaration.ingest_run_id,),
        ).fetchall()
        return rows

    def _sort_parts(self, row: Mapping[str, Any], include_id: bool = True) -> list[Any]:
        raw = self._raw_record(row)
        result: list[Any] = []
        for token in self.declaration.sort:
            name = str(token).rsplit(" ", 1)[0]
            if name == "source_row_ordinal":
                name = "row_index" if self.family == "avatar_codex_archive" else "source_ordinal"
                result.append(row[name])
            elif name == "source_record_pointer":
                name = "source_pointer" if self.family == "avatar_codex_archive" else "source_record_pointer"
                result.append(row[name])
            elif name in row.keys() and name not in {"id", "occurrence_id"}:
                result.append(row[name])
            else:
                result.append(raw.get(name))
        if include_id and self._primary_id not in ["id", "occurrence_id"]:
            result.append(row[self._primary_id])
        elif include_id and (not result or result[-1] != row[self._primary_id]):
            result.append(row[self._primary_id])
        return result

    def _ordered(self, rows: list[Mapping[str, Any]]) -> list[tuple[Mapping[str, Any], list[Any]]]:
        values = [(row, self._sort_parts(row)) for row in rows]
        values.sort(key=cmp_to_key(
            lambda left, right: _compare_ordered_keys(
                left[1], right[1], self.declaration.sort
            )
        ))
        return values

    def _page(self, values: list[tuple[Any, list[Any]]], cursor: str | None, scope: Mapping[str, Any], limit: int) -> tuple[list[Any], str | None]:
        selected = _decode_cursor(cursor, scope)
        start = 0
        if selected is not None:
            for index, (_, key) in enumerate(values):
                if key == selected:
                    start = index + 1
                    break
            else:
                raise ValueError("cursor position is invalid")
        page = values[start:start + limit]
        next_cursor = None
        if start + limit < len(values) and page:
            next_cursor = _encode_cursor(scope, page[-1][1])
        return [item[0] for item in page], next_cursor

    def _primary_item(self, row: Mapping[str, Any]) -> dict[str, Any]:
        physical_id = row[self._primary_id]
        return {
            "occurrence_id": str(physical_id),
            "physical_row_id": str(physical_id),
            "family": self.family,
            "family_contract_version": self.family_contract_version,
            "ingest_run_id": row["ingest_run_id"],
            "row": _row_dict(row),
        }

    def _linked_rows(self, declaration: M5DChildTableDeclaration, parent_id: Any) -> list[Mapping[str, Any]]:
        """Read one explicitly declared edge while retaining its parent scope."""
        return self.db.conn.execute(
            f"SELECT * FROM {_q(declaration.table)} "
            f"WHERE {_q(declaration.link_column)}=? AND ingest_run_id=? "
            "ORDER BY rowid",
            (parent_id, self.declaration.ingest_run_id),
        ).fetchall()

    def _linked_children(self, row: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
        """Return only descendants of this physical row, one edge at a time.

        Text is intentionally traversed as row -> owner -> resolution ->
        candidate. A broad query over all candidates would let a candidate
        from a sibling owner appear in this physical container.
        """
        parent_id = row[self._primary_id]
        declarations = M5D_QUERY_CHILDREN[self.family]
        output: dict[str, list[dict[str, Any]]] = {}

        for declaration in declarations:
            if declaration.parent_table is not None:
                continue
            direct = self._linked_rows(declaration, parent_id)
            output[declaration.table] = [_row_dict(item) for item in direct]

            if declaration.table.endswith("_relationship_occurrence"):
                candidate_declaration = next(
                    (item for item in declarations
                     if item.parent_table == declaration.table), None
                )
                if candidate_declaration is not None:
                    candidates: list[Mapping[str, Any]] = []
                    for relationship in direct:
                        candidates.extend(self._linked_rows(
                            candidate_declaration,
                            relationship[candidate_declaration.parent_id_column],
                        ))
                    output[candidate_declaration.table] = [
                        _row_dict(item) for item in candidates
                    ]

            if declaration.table.endswith("_text_owner_occurrence"):
                resolution_declaration = next(
                    (item for item in declarations
                     if item.parent_table == declaration.table), None
                )
                candidate_declaration = next(
                    (item for item in declarations
                     if resolution_declaration is not None
                     and item.parent_table == resolution_declaration.table), None
                )
                resolutions: list[Mapping[str, Any]] = []
                candidates: list[Mapping[str, Any]] = []
                if resolution_declaration is not None:
                    for owner in direct:
                        owner_resolutions = self._linked_rows(
                            resolution_declaration,
                            owner[resolution_declaration.parent_id_column],
                        )
                        resolutions.extend(owner_resolutions)
                        if candidate_declaration is not None:
                            for resolution in owner_resolutions:
                                candidates.extend(self._linked_rows(
                                    candidate_declaration,
                                    resolution[candidate_declaration.parent_id_column],
                                ))
                    output[resolution_declaration.table] = [
                        _row_dict(item) for item in resolutions
                    ]
                if candidate_declaration is not None:
                    output[candidate_declaration.table] = [
                        _row_dict(item) for item in candidates
                    ]

        # Include legal zero-row optional children explicitly. Callers must
        # assert scope using the selected row IDs, never ``all(empty)``.
        for declaration in declarations:
            output.setdefault(declaration.table, [])
        return output

    @stable_routed_call("list_family_evidence")
    def list_evidence(self, *, ingest_run_id: str, family_contract_version: str, filters: dict[str, Any] | None = None, cursor: str | None = None, limit: int = 50) -> FamilyEvidenceResult:
        error = self._scope(ingest_run_id, family_contract_version, "list_family_evidence")
        if error is not None:
            return error
        selected, bounded = self._filters(filters, "list_family_evidence"), _limit(limit)
        ordered = [(row, key) for row, key in self._ordered([row for row in self._primary_rows() if self._matches(row, selected)])]
        scope = {"family": self.family, "ingest_run_id": ingest_run_id, "family_contract_version": family_contract_version, "operation": "list_family_evidence", "filters": selected, "query": None}
        page, next_cursor = self._page([(self._primary_item(row), key) for row, key in ordered], cursor, scope, bounded)
        return self._result("ok", ingest_run_id, {"operation": "list", "filters": selected, "items": page, "pagination": {"kind": "keyset", "limit": bounded, "next_cursor": next_cursor}})

    @stable_routed_call("get_family_evidence")
    def get_evidence(self, *, ingest_run_id: str, family_contract_version: str, occurrence_id: str) -> FamilyEvidenceResult:
        error = self._scope(ingest_run_id, family_contract_version, "get_family_evidence")
        if error is not None:
            return error
        requested = _text(occurrence_id, "occurrence_id")
        row = self.db.conn.execute(
            f"SELECT * FROM {_q(self._table)} WHERE {_q(self._primary_id)}=? AND ingest_run_id=?",
            (requested, self.declaration.ingest_run_id),
        ).fetchone()
        if row is None:
            return self._result("not_found", ingest_run_id, {"status": "NotFound", "reason": "occurrence_not_found", "lookup": {"operation": "get_family_evidence", "occurrence_id": requested}})
        primary = self._primary_item(row)
        return self._result("ok", ingest_run_id, {
            "operation": "get", "container_scope": f"single_physical_{self.family}_row_occurrence",
            "occurrence": {"row": primary["row"], "physical_row_id": primary["physical_row_id"], "children": self._linked_children(row)},
        })

    def _search_hits(self, query: str, selected: Mapping[str, Any]) -> list[tuple[_SearchHit, list[Any]]]:
        tables = M5D_SEARCH_TABLES[self.family]
        if tables is None:
            return []
        owner_table, resolution_table, candidate_table = tables
        hits: list[tuple[_SearchHit, list[Any]]] = []
        candidates = self.db.conn.execute(f"SELECT * FROM {_q(candidate_table)} WHERE ingest_run_id=?", (self.declaration.ingest_run_id,)).fetchall()
        for candidate in candidates:
            raw_text = candidate["raw_text"]
            if not isinstance(raw_text, str) or query not in raw_text:
                continue
            resolution = self.db.conn.execute(f"SELECT * FROM {_q(resolution_table)} WHERE occurrence_id=? AND ingest_run_id=?", (candidate["resolution_occurrence_id"], self.declaration.ingest_run_id)).fetchone()
            if resolution is None:
                continue
            owner = self.db.conn.execute(f"SELECT * FROM {_q(owner_table)} WHERE occurrence_id=? AND ingest_run_id=?", (resolution["owner_occurrence_id"], self.declaration.ingest_run_id)).fetchone()
            if owner is None:
                continue
            row = self.db.conn.execute(f"SELECT * FROM {_q(self._table)} WHERE occurrence_id=? AND ingest_run_id=?", (owner["row_occurrence_id"], self.declaration.ingest_run_id)).fetchone()
            if row is None or not self._matches(row, selected):
                continue
            if "locale" in selected and candidate["locale"] != selected["locale"]:
                continue
            hit = _SearchHit(row=row, owner=owner, resolution=resolution, candidate=candidate)
            key = [
                row["source_ordinal"], owner["source_pointer"], candidate["locale"], candidate["relative_path"],
                candidate["pair_ordinal"], candidate["pointer"], candidate["raw_key_sha256"], candidate["raw_text_sha256"],
                candidate["raw_record_sha256"], candidate["occurrence_id"],
            ]
            hits.append((hit, key))
        hits.sort(key=cmp_to_key(
            lambda left, right: _compare_ordered_keys(
                left[1], right[1], M5D_SEARCH_ORDER
            )
        ))
        return hits

    @stable_routed_call("search_family_text")
    def search_text(self, *, ingest_run_id: str, family_contract_version: str, query: str, filters: dict[str, Any] | None = None, cursor: str | None = None, limit: int = 50) -> FamilyEvidenceResult:
        error = self._scope(ingest_run_id, family_contract_version, "search_family_text")
        if error is not None:
            return error
        if "search_family_text" not in self.declaration.operations:
            return self._result("not_found", ingest_run_id, {"status": "NotFound", "reason": "operation_not_supported", "lookup": {"operation": "search_family_text", "family": self.family}})
        literal = _text(query, "query")
        if not literal.strip():
            raise ValueError("query must contain a non-whitespace character")
        selected, bounded = self._filters(filters, "search_family_text"), _limit(limit)
        hits = self._search_hits(literal, selected)
        scope = {"family": self.family, "ingest_run_id": ingest_run_id, "family_contract_version": family_contract_version, "operation": "search_family_text", "filters": selected, "query": literal}
        page, next_cursor = self._page([(hit, key) for hit, key in hits], cursor, scope, bounded)
        items = []
        for hit in page:
            items.append({
                "occurrence_id": str(hit.candidate["occurrence_id"]),
                "family": self.family,
                "family_contract_version": self.family_contract_version,
                "ingest_run_id": ingest_run_id,
                "row": _row_dict(hit.row),
                "text_owner": _row_dict(hit.owner),
                "text_resolution": _row_dict(hit.resolution),
                "text_candidate": _row_dict(hit.candidate),
                "raw_text": hit.candidate["raw_text"],
            })
        return self._result("ok", ingest_run_id, {
            "operation": "search_text", "query": {"literal": literal, "semantics": "case_sensitive_literal_substring_on_text_candidate_raw_text"},
            "filters": selected, "items": items,
            "pagination": {"kind": "keyset", "limit": bounded, "next_cursor": next_cursor},
        })


def build_batch_m5d_providers(
    db: ReadonlyDatabase,
    report_path: str | None = None,
    manifest_path: str | None = None,
    verification_path: str | None = None,
    candidate_path: str | None = None,
    spec_dir: str | None = None,
    qualification: M5DQualification | None = None,
) -> tuple[BatchM5DFamilyRepository, ...]:
    """Qualify once (unless a verified snapshot is supplied), then construct 11 providers."""
    if qualification is None:
        from .batch_m5d_runtime import (
            M5D_INDEPENDENT_REPORT_PATH, M5D_REPORT_PATH, M5D_RUNTIME_MANIFEST_PATH, M5D_SPEC_DIR,
            qualify_batch_m5d,
        )
        qualification = qualify_batch_m5d(
            db, report_path or M5D_REPORT_PATH, manifest_path or M5D_RUNTIME_MANIFEST_PATH,
            verification_path or M5D_INDEPENDENT_REPORT_PATH, candidate_path, spec_dir or M5D_SPEC_DIR,
        )
    from .batch_m5d_runtime import load_family_declarations
    declarations = load_family_declarations(spec_dir or Path(qualification.report_path).parents[1] / "batch-m5c/repository-specs") if spec_dir else load_family_declarations()
    return tuple(BatchM5DFamilyRepository(db, declaration, qualification) for declaration in declarations)


__all__ = ["BatchM5DFamilyRepository", "BatchM5DRetrievalError", "M5D_QUERY_CHILDREN", "build_batch_m5d_providers"]
