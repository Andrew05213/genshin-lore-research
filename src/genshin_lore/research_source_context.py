"""Read-through, source-authored display context for Research evidence.

This is derived presentation metadata. It does not assign logical identity or
change the evidence reference used by a citation.
"""
from __future__ import annotations

import json
import re


HEADING = re.compile(r"^【([^【】]{1,160})】$")
TITLE_FIELDS = {
    "document_archive": ("titleTextMapHash", "source-authored-document-title", "document"),
}


def title_state(candidates: list[dict], *, source_kind: str, path: str, subtitle: str | None = None) -> dict:
    """Preserve physical candidates; identical strings do not merge them."""
    candidates = sorted(candidates, key=lambda x: (x["source_row_id"], x["title_text_id"]))
    if not candidates:
        status = 'unresolved'
    elif len(candidates) != 1:
        status = 'ambiguous'
    else:
        status = 'candidate' if candidates[0].get('relation_unproven') else 'resolved'
    title = candidates[0]["title"] if len(candidates) == 1 else None
    basis = candidates[0]["basis"] if len(candidates) == 1 else None
    if title:
        label = f"《{title}》" if source_kind in ("book", "document") else f"「{title}」"
        if status == 'candidate':
            label = '候选' + label + '（书名与文件的关系未证实）'
        if subtitle:
            label += f"—「{subtitle}」"
    elif subtitle:
        reason = "正式标题未解析" if status == "unresolved" else "正式标题存在多个来源候选"
        label = f"「{subtitle}」（{reason}；{path}）"
    else:
        label = f"正式标题未解析（{path}）" if status == "unresolved" else f"标题存在多个来源候选（{path}）"
    return {
        "display_title": title,
        "display_subtitle": subtitle,
        "source_kind": source_kind,
        "volume_title": title if source_kind == "book" else None,
        "chapter_title": subtitle if source_kind == "book" else None,
        "parent_title": None,
        "speaker_display_name": None,
        "source_title_basis": basis,
        "title_basis": basis,
        "title_resolution_status": status,
        "title_candidates": candidates,
        "citation_label": label,
        "internal_source_path": path,
        "context_is_evidence_truth": False,
    }


class ResearchSourceContext:
    def __init__(self, research_db, document_db=None):
        self.research_db = research_db
        self.document_db = document_db
        self._readable_cache: dict[tuple[str, str], list[dict]] = {}

    def _document_readables(self, query: str | None = None):
        """Source paths carried by sealed candidates, without resolving ownership."""
        if self.document_db is None:
            return
        sql = """
            SELECT d.id AS source_row_id, d.source_path AS document_path,
                   d.pointer AS document_pointer, rel.id AS relation_id,
                   rel.kind AS relation_kind, rel.status AS relation_status, rel.search_complete,
                   cand.target_seal_json, s.id AS slot_id, s.status AS slot_status,
                   t.id AS title_text_id, t.raw_text AS title,
                   t.source_path AS title_source_path, t.pointer AS title_pointer
              FROM document_candidate cand
              JOIN document_relation rel ON rel.id=cand.relation_id
              JOIN document_row d ON d.id=rel.row_id
              JOIN document_slot s ON s.row_id=d.id
              JOIN document_text t ON t.slot_id=s.id
             WHERE rel.kind IN ('localization_readable','document_localization')
               AND s.field='titleTextMapHash' AND s.locale='CHS'
        """
        params = ()
        if query is not None:
            sql += ' AND instr(t.raw_text,?)>0'
            params = (query,)
        for row in self.document_db.execute(sql, params):
            if row['slot_status'] != 'resolved':
                continue
            seal = json.loads(row['target_seal_json'])
            if row['relation_kind'] == 'localization_readable':
                if row['relation_status'] == 'resolved' and row['search_complete']:
                    yield row, seal, False, None
            elif row['relation_status'] == 'mapping_unresolved':
                for target in seal.get('candidate_readable_targets', []):
                    if target['locale'] == 'CHS':
                        yield row, target['target'], True, target

    def readable_paths_for_title(self, query: str) -> list[str]:
        return sorted({seal['path'] for _, seal, _, _ in self._document_readables(query)})

    def _readable_document_titles(self, path: str, file_sha256: str) -> list[dict]:
        if self.document_db is None:
            return []
        key = path, file_sha256
        if key in self._readable_cache:
            return self._readable_cache[key]
        by_physical_title: dict[tuple[str, str], dict] = {}
        for row, seal, unproven, locator in self._document_readables():
            if seal.get("path") != path or seal.get("sha256") != file_sha256:
                continue
            title = row["title"]
            if not isinstance(title, str) or not title.strip():
                continue
            physical_key = row["source_row_id"], row["title_text_id"]
            if physical_key in by_physical_title:
                if unproven:
                    by_physical_title[physical_key].setdefault('navigation_candidates', []).append(locator)
                    continue
            by_physical_title[physical_key] = {
                "title": title,
                "basis": "source-authored-document-title",
                "source_row_id": row["source_row_id"],
                "source_row_path": row["document_path"],
                "source_row_pointer": row["document_pointer"],
                "title_slot_id": row["slot_id"],
                "title_text_id": row["title_text_id"],
                "title_source_path": row["title_source_path"],
                "title_source_pointer": row["title_pointer"],
                "readable_path": path,
                "readable_file_sha256": file_sha256,
            }
            if unproven:
                by_physical_title[physical_key].update(relation_unproven=True,
                    basis='source-authored title via unresolved physical Localization candidate; ownership not proven',
                    navigation_candidates=[locator])
        result = list(by_physical_title.values())
        self._readable_cache[key] = result
        return result

    def _heading(self, row) -> str | None:
        if row is None or row["ordinal"] is None:
            return None
        # Only a physical predecessor in this container can supply a heading.
        found = self.research_db.execute(
            "SELECT raw_text FROM research_text WHERE container_id=? AND ordinal<=? "
            "AND raw_text GLOB '【*】' ORDER BY ordinal DESC,id DESC LIMIT 64",
            (row["container_id"], row["ordinal"]),
        )
        for candidate in found:
            match = HEADING.fullmatch(candidate["raw_text"].strip())
            if match:
                return match.group(1)
        return None

    def _local_title(self, container) -> list[dict]:
        family = container["evidence_family"]
        if family not in TITLE_FIELDS:
            return []
        field, basis, _ = TITLE_FIELDS[family]
        rows = self.research_db.execute(
            "SELECT id,raw_text,text_source_path,text_source_pointer,source_table,pk_value "
            "FROM research_text WHERE container_id=? AND locale='CHS' AND owner_field=? ORDER BY id",
            (container["id"], field),
        )
        return [
            {
                "title": item["raw_text"], "basis": basis,
                "source_row_id": f"{item['source_table']}:{item['pk_value']}",
                "title_text_id": str(item["id"]),
                "title_source_path": item["text_source_path"],
                "title_source_pointer": item["text_source_pointer"],
            }
            for item in rows if item["raw_text"].strip()
        ]

    def for_evidence(self, container, row=None) -> dict:
        family = container["evidence_family"]
        path = container["source_path"]
        if family == "readable_archive":
            candidates = self._readable_document_titles(path, container["source_file_sha256"])
            return title_state(candidates, source_kind="book" if "/Book" in path else "readable", path=path, subtitle=self._heading(row))
        source_kind = TITLE_FIELDS[family][2] if family in TITLE_FIELDS else "unknown"
        return title_state(self._local_title(container), source_kind=source_kind, path=path)
