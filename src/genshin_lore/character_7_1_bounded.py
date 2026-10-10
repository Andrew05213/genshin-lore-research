"""Read-only, snapshot-bound Character Lore/Fetter Voice discovery adapter.

This is not a 7.1 Research corpus. All returned rows are rechecked against
the accepted physical foundation and exact 7.1 source before citation.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

from .loading_tips_source import array_records, pairs, sha


SNAPSHOT = "anime-game-data:7.1:9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
FOUNDATION_RUN = "character71:f13a6261e9ed01bf6a765e6d8b22167bfd71c8b1"
FOUNDATION_SHA = "20e065491d3696555fbeaae104c95284a9f7e9ae439861613a984829b6a1b8ef"


class Character71Bounded:
    def __init__(self, manifest_path: str | Path, source_root: str | Path):
        self.manifest = json.loads(Path(manifest_path).read_text())
        self.source = Path(source_root)
        if self.manifest.get("contract") != "character-7.1-bounded-successor-v1" or self.manifest.get("snapshot_id") != SNAPSHOT or self.manifest.get("foundation_run_id") != FOUNDATION_RUN or self.manifest.get("product_accepted") is not False:
            raise ValueError("bounded Character runtime contract")
        self.files: dict[str, Path] = {}
        self.db: dict[str, sqlite3.Connection] = {}
        self._source_cache: dict[str, list] = {}
        self._object_cache: dict[str, dict] = {}
        try:
            for kind in ("foundation", "text", "lore", "voice"):
                spec = self.manifest["artifacts"][kind]
                path = Path(spec["machine_local_path"])
                if path.stat().st_size != spec["size_bytes"] or sha(path) != spec["sha256"] or any(Path(str(path) + ext).exists() for ext in ("-wal", "-shm", "-journal")):
                    raise ValueError("bounded Character artifact drift: " + kind)
                self.files[kind] = path
                self.db[kind] = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
                self.db[kind].execute("PRAGMA query_only=ON")
            if self.manifest["artifacts"]["foundation"]["sha256"] != FOUNDATION_SHA:
                raise ValueError("old/unaccepted foundation is not eligible")
            for kind in ("text", "lore", "voice"):
                release = json.loads(self.db[kind].execute("SELECT manifest_json FROM release WHERE id=1").fetchone()[0])
                spec = self.manifest["artifacts"][kind]
                if release["contract"] != f"character-7.1-{kind}-successor-v1" or release["foundation_run_id"] != FOUNDATION_RUN or release["snapshot_id"] != SNAPSHOT or (release["parser_build_id"], release["ingest_run_id"]) != (spec["parser_build_id"], spec["ingest_run_id"]):
                    raise ValueError("bounded Character release binding: " + kind)
                if kind != "text" and release["text_projection_sha256"] != self.manifest["artifacts"]["text"]["sha256"]:
                    raise ValueError("bounded Character TextMap projection binding")
            foundation_release = json.loads(self.db["foundation"].execute("SELECT manifest_json FROM foundation_release WHERE id=1").fetchone()[0])
            if foundation_release["ingest_run_id"] != FOUNDATION_RUN or foundation_release["snapshot_id"] != SNAPSHOT or foundation_release["parser_build_id"] != self.manifest["artifacts"]["foundation"]["parser_build_id"]:
                raise ValueError("bounded Character foundation release")
            scope_files = foundation_release["source_file_count"]
            if scope_files != 18:
                raise ValueError("bounded Character source denominator")
            source_identity = subprocess.check_output(["git", "-C", str(self.source), "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines()
            if source_identity != ["9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99", "aa578c08c402af39b41f1ffc95124d10f5410272"] or subprocess.check_output(["git", "-C", str(self.source), "status", "--porcelain=v1"], text=True).strip():
                raise ValueError("bounded Character source checkout drift")
            for path, size, expected_sha in self.db["foundation"].execute("SELECT path,size_bytes,sha256 FROM foundation_source_file"):
                if (self.source / path).stat().st_size != size or sha(self.source / path) != expected_sha:
                    raise ValueError("bounded Character source bytes drift: " + path)
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        for conn in self.db.values():
            conn.close()
        self.db.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _source_pairs(self, path: str) -> list:
        if path not in self._source_cache:
            self._source_cache[path] = pairs(self.source / path)
        return self._source_cache[path]

    def _source_object(self, path: str) -> dict:
        if path not in self._object_cache:
            value = json.loads((self.source / path).read_text())
            if type(value) is not dict:
                raise ValueError("Voice source object root drift")
            self._object_cache[path] = value
        return self._object_cache[path]

    @staticmethod
    def _canonical(value: object) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)

    def _text(self, owner_id: str, locale: str = "CHS") -> dict:
        row = self.db["text"].execute("SELECT row_id,owner_pointer,field_name,textmap_hash,status,candidate_count FROM resolution WHERE owner_id=? AND locale=?", (owner_id, locale)).fetchone()
        if row is None:
            raise ValueError("TextMap owner missing from projection")
        parent = self.db["foundation"].execute("SELECT row_id,pointer,field_name,textmap_hash FROM foundation_text_owner WHERE id=?", (owner_id,)).fetchone()
        if parent != row[:4]:
            raise ValueError("TextMap owner provenance drift")
        candidates = []
        for tier, path, ordinal, key, raw in self.db["text"].execute("SELECT tier,source_path,physical_ordinal,source_key,raw_text FROM candidate WHERE owner_id=? AND locale=? ORDER BY source_path,physical_ordinal", (owner_id, locale)):
            source_key, source_value = self._source_pairs(path)[ordinal]
            if (source_key, source_value) != (key, raw) or key != row[3]:
                raise ValueError("TextMap physical candidate drift")
            candidates.append({"tier": tier, "source_path": path, "physical_ordinal": ordinal,
                               "source_key": key, "pointer": "/" + str(key), "text": raw})
        if len(candidates) != row[5] or row[4] != ("missing" if not candidates else "resolved_unique" if len(candidates) == 1 else "resolved_candidate_set"):
            raise ValueError("TextMap candidate set drift")
        return {"owner_id": owner_id, "row_id": row[0], "owner_pointer": row[1], "field_name": row[2],
                "textmap_hash": row[3], "locale": locale, "resolution_status": row[4], "candidates": candidates}

    def _row(self, row_id: str) -> dict:
        row = self.db["foundation"].execute("SELECT snapshot_id,parser_build_id,ingest_run_id,source_path,ordinal,pointer,raw_json,raw_sha256 FROM foundation_primary_row WHERE id=?", (row_id,)).fetchone()
        if row is None or row[0] != SNAPSHOT or row[2] != FOUNDATION_RUN:
            raise ValueError("Character physical evidence not found in exact run")
        source_path, ordinal = row[3], row[4]
        found = next(((source_row, raw) for pos, source_row, raw in array_records(self.source / source_path) if pos == ordinal), None)
        if found is None or found[1] != row[6]:
            raise ValueError("Character raw physical source drift")
        return {"evidence_ref": row_id, "snapshot_id": row[0], "parser_build_id": row[1], "ingest_run_id": row[2], "source_path": source_path, "physical_ordinal": ordinal, "pointer": row[5], "raw_sha256": row[7]}

    def _owner_candidates(self, kind: str, row_id: str) -> list[str]:
        if kind == "avatar":
            return [row_id]
        table = "voice_avatar_candidate" if kind == "voice" else "lore_avatar_candidate"
        db = self.db["voice" if kind == "voice" else "lore"]
        return [row[0] for row in db.execute(f"SELECT avatar_row_id FROM {table} WHERE row_id=? ORDER BY avatar_row_id", (row_id,))]

    def _field(self, kind: str, row_id: str, field: str) -> dict | None:
        table = "voice_text_ref" if kind == "voice" else "lore_text_ref"
        db = self.db["voice" if kind == "voice" else "lore"]
        row = db.execute(f"SELECT owner_id FROM {table} WHERE row_id=? AND field_name=?", (row_id, field)).fetchone()
        return self._text(row[0]) if row else None

    @staticmethod
    def _single_label(resolution: dict | None) -> tuple[str | None, str]:
        if resolution is None:
            return None, "unresolved"
        if resolution["resolution_status"] == "resolved_candidate_set":
            return None, "ambiguous"
        if resolution["resolution_status"] == "missing":
            return None, "unresolved"
        return resolution["candidates"][0]["text"], "resolved"

    def _context(self, kind: str, row_id: str) -> dict:
        owners = self._owner_candidates(kind, row_id)
        if len(owners) != 1:
            return {"display_title": None, "display_subtitle": None, "source_kind": kind, "resolution_status": "ambiguous" if owners else "unresolved", "title_basis": None, "citation_label": None, "owner_candidates": owners}
        name, name_status = self._single_label(self._field("avatar", owners[0], "nameTextMapHash"))
        title_field = "storyTitleTextMapHash" if kind == "story" else "voiceTitleTextMapHash" if kind == "voice" else None
        title, title_status = self._single_label(self._field(kind, row_id, title_field)) if title_field else (None, "resolved")
        status = "ambiguous" if "ambiguous" in (name_status, title_status) else "unresolved" if "unresolved" in (name_status, title_status) else "resolved"
        quoted_title = title if title and title.startswith("「") and title.endswith("」") else f"「{title}」" if title else None
        label = f"「{name}」—{quoted_title}" if status == "resolved" and quoted_title else f"「{name}」—角色资料" if status == "resolved" else None
        return {"display_title": name if name_status == "resolved" else None, "display_subtitle": title if title_status == "resolved" else None, "source_kind": kind, "resolution_status": status, "title_basis": "source-authored-TextMap-and-exact-physical-owner" if status == "resolved" else None, "citation_label": label, "owner_candidates": owners}

    def read(self, kind: str, row_id: str, *, locale: str = "CHS") -> dict:
        if kind not in ("avatar", "fetter_info", "story", "voice") or locale not in ("CHS", "EN", "JP"):
            raise ValueError("unsupported bounded Character scope")
        db_kind = "voice" if kind == "voice" else "lore"
        table = "voice_record" if kind == "voice" else "lore_record"
        row = self.db[db_kind].execute(f"SELECT * FROM {table} WHERE row_id=?", (row_id,)).fetchone()
        if row is None or (kind != "voice" and row[1] != kind):
            raise ValueError("Character record outside bounded scope")
        provenance = self._row(row_id)
        ref_table = "voice_text_ref" if kind == "voice" else "lore_text_ref"
        fields = [self._text(owner_id, locale) for (owner_id,) in self.db[db_kind].execute(f"SELECT owner_id FROM {ref_table} WHERE row_id=? ORDER BY owner_pointer", (row_id,))]
        if any(field["row_id"] != row_id for field in fields):
            raise ValueError("cross-container Character text projection")
        raw_ref_table = "voice_raw_field_ref" if kind == "voice" else "lore_raw_field_ref"
        raw_observations = []
        for field_id, pointer, name, role, source_ordinal, child_ordinal in self.db[db_kind].execute(f"SELECT field_id,pointer,field_name,role,source_field_ordinal,child_ordinal FROM {raw_ref_table} WHERE row_id=? ORDER BY source_field_ordinal,child_ordinal", (row_id,)):
            foundation_field = self.db["foundation"].execute("SELECT row_id,pointer,field_name,role,source_field_ordinal,child_ordinal,raw_json FROM foundation_field WHERE id=?", (field_id,)).fetchone()
            if foundation_field is None or foundation_field[:6] != (row_id, pointer, name, role, source_ordinal, child_ordinal):
                raise ValueError("Character raw condition provenance drift")
            raw_observations.append({"field_id": field_id, "pointer": pointer, "field_name": name, "role": role, "source_field_ordinal": source_ordinal, "child_ordinal": child_ordinal, "raw_json": foundation_field[6]})
        result = {"kind": kind, "coverage": "Character/Fetter Voice only" if kind == "voice" else "Character metadata and Fetter Story only", "partial": True, "product_accepted": False, "provenance": provenance, "text_fields": fields, "raw_condition_observations": raw_observations, "source_context": self._context(kind, row_id), "discovery_only": True}
        result["citation_label"] = result["source_context"]["citation_label"]
        return result

    def inspect(self, kind: str, row_id: str) -> dict:
        result = self.read(kind, row_id)
        foundation = self.db["foundation"]
        result["raw_json"] = foundation.execute("SELECT raw_json FROM foundation_primary_row WHERE id=?", (row_id,)).fetchone()[0]
        if kind == "voice":
            candidates = []
            for (lut_id,) in self.db["voice"].execute("SELECT lut_id FROM voice_lut_candidate WHERE row_id=? ORDER BY lut_id", (row_id,)):
                lut = self.db["voice"].execute("SELECT source_path,object_key,object_ordinal,pointer,voice_slot,item_id FROM lut_ref WHERE lut_id=?", (lut_id,)).fetchone()
                item = self.db["voice"].execute("SELECT source_path,object_key,object_ordinal,pointer,variants_status FROM item_ref WHERE item_id=?", (lut[5],)).fetchone()
                audio = self.db["voice"].execute("SELECT audio_id,physical_ordinal,pointer,audio_path,selector,order_value FROM audio_ref WHERE item_id=? ORDER BY physical_ordinal", (lut[5],)).fetchall()
                # The physical candidate must still exist in the accepted foundation.
                if foundation.execute("SELECT count(*) FROM foundation_voice_lut WHERE id=?", (lut_id,)).fetchone()[0] != 1:
                    raise ValueError("Voice LUT foundation drift")
                lut_raw = foundation.execute("SELECT raw_json FROM foundation_voice_lut WHERE id=?", (lut_id,)).fetchone()[0]
                item_raw = foundation.execute("SELECT raw_json FROM foundation_voice_item WHERE id=?", (lut[5],)).fetchone()[0]
                if self._canonical(self._source_object(lut[0])[lut[1]]) != lut_raw or self._canonical(self._source_object(item[0])[item[1]]) != item_raw:
                    raise ValueError("Voice LUT/Items exact source drift")
                for audio_id, ordinal, _, _, _, _ in audio:
                    raw = foundation.execute("SELECT raw_json FROM foundation_audio_variant WHERE id=?", (audio_id,)).fetchone()[0]
                    if self._canonical(self._source_object(item[0])[item[1]]["GLDBLKKLJHP"][ordinal]) != raw:
                        raise ValueError("Voice audio variant exact source drift")
                candidates.append({"lut_id": lut_id, "lut_source": lut, "item_source": item, "audio_variants": audio})
            result["voice_candidates"] = candidates
        return result

    def search(self, query: str, *, kind: str | None = None, limit: int = 20, offset: int = 0) -> dict:
        if not query or not isinstance(query, str) or kind not in (None, "avatar", "fetter_info", "story", "voice") or not 1 <= limit <= 50 or offset < 0:
            raise ValueError("bounded Character query scope")
        found = set()
        # The text artifact is a separate read-only DB; no ATTACH or mutation is
        # needed. Owner IDs are merely physical lookup keys, never entity IDs.
        owner_ids = [row[0] for row in self.db["text"].execute("SELECT DISTINCT owner_id FROM candidate WHERE locale='CHS' AND instr(raw_text,?)>0 ORDER BY owner_id", (query,))]
        for owner_id in owner_ids:
            if kind != "voice":
                for row_id, record_kind in self.db["lore"].execute("SELECT r.row_id,r.kind FROM lore_text_ref t JOIN lore_record r ON r.row_id=t.row_id WHERE t.owner_id=?", (owner_id,)):
                    if kind is None or record_kind == kind:
                        found.add((record_kind, row_id))
            if kind in (None, "voice"):
                for (row_id,) in self.db["voice"].execute("SELECT row_id FROM voice_text_ref WHERE owner_id=?", (owner_id,)):
                    found.add(("voice", row_id))
        ordered = sorted(found)
        page = ordered[offset:offset + limit]
        results = [self.read(record_kind, row_id) for record_kind, row_id in page]
        return {"results": results, "total_physical_records": len(ordered), "next_offset": offset + limit if offset + limit < len(ordered) else None, "coverage": "bounded Character 7.1 only", "partial": True, "product_accepted": False}

    def follow(self, avatar_row_id: str, *, limit: int = 50, offset: int = 0) -> dict:
        self.read("avatar", avatar_row_id)
        rows = [("story", row_id) for (row_id,) in self.db["lore"].execute("SELECT row_id FROM lore_avatar_candidate WHERE avatar_row_id=? AND row_id IN(SELECT row_id FROM lore_record WHERE kind='story')", (avatar_row_id,))]
        rows += [("voice", row_id) for (row_id,) in self.db["voice"].execute("SELECT row_id FROM voice_avatar_candidate WHERE avatar_row_id=?", (avatar_row_id,))]
        rows.sort()
        return {"candidate_navigation_only": True, "logical_relation_closure": False, "items": [{"kind": kind, "evidence_ref": row_id} for kind, row_id in rows[offset:offset + limit]], "next_offset": offset + limit if offset + limit < len(rows) else None, "total_physical_candidates": len(rows), "partial": True}
