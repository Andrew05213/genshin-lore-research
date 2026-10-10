"""Explicit physical storage adapters for the independent 7.1 candidate.

This is a versioned adapter registry over the existing Research schema. It
does not infer owner roles, identities, or relations from column spelling.
Composite physical keys remain bound to the sealed source artifact.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from functools import lru_cache

from .research_index import _container, _text, _prov

VERSION = "research71-explicit-physical-adapters-v1"

# parent, text projection SQL, declared owner/state source
SPECS = {
    "quest_narrative_archive": ("excel_row", "SELECT t.*,s.row_id parent_id,s.owner_field owner,s.locale inherited_locale FROM text_candidate t JOIN text_slot s ON s.id=t.slot_id ORDER BY t.id"),
    "quest_review_archive": ("evidence_row", "SELECT t.*,s.row_id parent_id,s.owner_field owner,s.locale inherited_locale FROM text_candidate t JOIN text_slot s ON s.id=t.slot_id ORDER BY t.id"),
    "codexquest_archive": ("codex_root", "SELECT t.*,s.root_id parent_id,s.source_label owner,s.locale inherited_locale FROM codex_text_candidate t JOIN codex_text_slot s ON s.id=t.slot_id ORDER BY t.id"),
    "native_message": ("native71_row", "SELECT t.*,s.row_id parent_id,s.owner_field owner,s.locale inherited_locale FROM native71_text t JOIN native71_slot s ON s.id=t.slot_id ORDER BY t.id"),
    "event_native_text_17": ("event71_row", "SELECT t.*,s.row_id parent_id,s.owner_field owner,s.locale inherited_locale FROM event71_text t JOIN event71_slot s ON s.id=t.slot_id ORDER BY t.id"),
    "native_successor_physical": ("row_occurrence", "SELECT t.*,s.row_id parent_id,s.owner_field owner,s.locale inherited_locale FROM text_candidate t JOIN text_slot s ON s.id=t.slot_id ORDER BY t.id"),
    "native_high_value_physical": ("row_occurrence", "SELECT t.*,s.row_id parent_id,s.owner_field owner,s.locale inherited_locale FROM text_candidate t JOIN text_slot s ON s.id=t.slot_id ORDER BY t.id"),
    "event_physical_13": ("physical_row", "SELECT t.*,o.row_id parent_id,o.field_pointer owner,t.locale inherited_locale FROM textmap_candidate t JOIN numeric_observation o ON o.id=t.observation_id ORDER BY t.id"),
    "event_physical_16": ("selected_occurrence", None),
    "event_physical_18": ("selected_occurrence", None),
    "quest_talk_physical": ("physical_container", "SELECT t.*,n.container_id parent_id,n.pointer owner,t.locale inherited_locale,n.ordinal node_ordinal FROM text_slot t JOIN talk_node n ON n.id=t.node_id WHERE t.raw_text IS NOT NULL ORDER BY t.id"),
    "narrative_npc": ("physical_row", "SELECT t.*,CAST(t.row_ordinal AS TEXT) parent_id,t.field owner,t.locale inherited_locale FROM text_candidate t ORDER BY t.row_ordinal,t.field,t.locale,t.source_path,t.pair_ordinal"),
    "narrative_dialog": ("physical_row", "SELECT t.*,CAST(t.row_ordinal AS TEXT) parent_id,t.field owner,t.locale inherited_locale FROM text_candidate t ORDER BY t.row_ordinal,t.field,t.locale,t.source_path,t.pair_ordinal"),
    "narrative_reminder": ("physical_row", "SELECT t.*,CAST(t.row_ordinal AS TEXT) parent_id,t.field owner,t.locale inherited_locale FROM text_candidate t ORDER BY t.row_ordinal,t.field,t.locale,t.source_path,t.pair_ordinal"),
}
TEXT_TABLE = {
    "codexquest_archive": "codex_text_candidate", "native_message": "native71_text",
    "event_native_text_17": "event71_text", "event_physical_13": "textmap_candidate",
    "quest_talk_physical": "text_slot",
}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@lru_cache(maxsize=512)
def primary_columns(db, table):
    return [r[1] for r in sorted(db.execute(f'PRAGMA table_info("{table}")'), key=lambda r: r[5]) if r[5]]


def source_key(db, table, row):
    columns = primary_columns(db, table)
    if not columns:
        raise ValueError("explicit physical primary key required: " + table)
    if len(columns) == 1:
        return columns[0], str(row[columns[0]])
    return canonical(columns), canonical([row[k] for k in columns])


def fetch(db, table, column, value):
    if not table.replace("_", "").isalnum():
        raise ValueError("unsafe physical table")
    if column.startswith("["):
        names = json.loads(column);values = json.loads(value)
        if names != primary_columns(db, table) or len(names) != len(values):
            raise ValueError("composite physical key drift")
        return db.execute(f'SELECT * FROM "{table}" WHERE ' + " AND ".join(f'"{k}"=?' for k in names), values).fetchone()
    if column not in primary_columns(db, table):
        raise ValueError("physical primary key drift")
    # Historical core adapters name the occurrence key without the run part
    # of a composite PK. This is usable only when exactly one sealed source
    # row matches; never pick a sibling run or the first physical candidate.
    rows = db.execute(f'SELECT * FROM "{table}" WHERE "{column}"=? LIMIT 2', (value,)).fetchall()
    if len(rows) > 1:
        raise ValueError("ambiguous historical physical key; exact run required")
    return rows[0] if rows else None


def adapt(src, out, key, component, r0, cs, ts):
    name = component["adapter"]
    parent, sql = SPECS[name]
    identity = tuple(component[k] for k in ("family", "contract", "parser", "run"))
    mapping = {};parent_keys = primary_columns(src, parent)
    for row in src.execute(f'SELECT * FROM "{parent}" ORDER BY ' + ",".join('"'+k+'"' for k in parent_keys)):
        row = dict(row);pkcol, pk = source_key(src, parent, row)
        native_pk = str(row.get("id", row.get("ordinal")))
        # Physical NPC evidence_ref is already a sealed source identity.
        occurrence = row.get("evidence_ref", native_pk)
        path = row["source_path"];ptr = row.get("pointer", row.get("source_pointer", "/"))
        sha, blob = _prov(src, r0, path)
        cs += 1
        _container(out, cs, key, name + ":" + parent, identity, occurrence,
                   row.get("kind", row.get("boundary", parent)), parent, pk, path, ptr, sha, blob,
                   row.get("ordinal", row.get("source_ordinal")), "physical_source_ordinal", pkcol)
        mapping[native_pk] = cs
    out.execute("INSERT INTO adapter_coverage VALUES(?,?,?,?,?,?,?,?)", (key,name+":"+parent,"container",parent,len(mapping),len(mapping),"complete","complete declared physical parent namespace"))
    if sql is None:
        return cs, ts, mapping
    table = TEXT_TABLE.get(name, "text_candidate")
    before = ts
    for row in src.execute(sql):
        row = dict(row)
        pkcol, pk = source_key(src, table, row)
        parent_id = str(row["parent_id"]);cid = mapping[parent_id]
        cr = out.execute("SELECT * FROM research_container WHERE id=?", (cid,)).fetchone()
        path = row["source_path"] if "source_path" in row else None
        ptr = row.get("pointer", row.get("source_pointer"));pair = row.get("pair_ordinal")
        if name == "quest_talk_physical":
            locators = json.loads(row["locators_json"])
            # This qualified projection stores one selected physical resolution;
            # any unexpected multi-locator shape must not silently select first.
            if len(locators) != 1 or locators[0]["raw_text"] != row["raw_text"]:
                raise ValueError("Quest/Talk non-unique physical locator requires explicit adaptation")
            path, ptr, pair = (locators[0][k] for k in ("path", "pointer", "pair_ordinal"))
        elif name.startswith("narrative_"):
            ptr = "/" + str(row["hash"])
        sha, blob = _prov(src, r0, path)
        child = row.get("id") or "physical-key:" + hashlib.sha256(pk.encode()).hexdigest()
        ts += 1
        _text(out,ts,cid,key,name+":"+table,tuple(cr[3:7]),cr[7],"physical_text_candidate",child,
              table,pk,row["inherited_locale"],row["owner"],row.get("node_ordinal", pair),
              row.get("owner", ptr),path,ptr,sha,blob,pair,row["raw_text"],pkcol)
    denominator = src.execute(f'SELECT COUNT(*) FROM "{table}"' + (" WHERE raw_text IS NOT NULL" if name == "quest_talk_physical" else "")).fetchone()[0]
    if ts - before != denominator:
        raise ValueError("text adapter omitted physical candidates: " + name)
    out.execute("INSERT INTO adapter_coverage VALUES(?,?,?,?,?,?,?,?)", (key,name+":"+table,"text",table,denominator,ts-before,"complete","all physical text candidates; role proof is reread, not inferred"))
    return cs, ts, mapping


def text_proof(src, adapter, row):
    """Reread slot/owner statuses; unknown stays unknown. No safety promotion."""
    name = adapter.split(":", 1)[0]
    got = fetch(src, row["source_table"], row["pk_column"], row["pk_value"])
    if got is None:
        raise ValueError("physical text disappeared")
    got = dict(got);proof = dict(got)
    if "slot_id" in got:
        slot = "codex_text_slot" if name == "codexquest_archive" else "native71_slot" if name == "native_message" else "event71_slot" if name == "event_native_text_17" else "text_slot"
        proof = {**dict(src.execute(f'SELECT * FROM "{slot}" WHERE id=?', (got["slot_id"],)).fetchone()), **proof}
    elif name.startswith("narrative_"):
        state = src.execute("SELECT * FROM text_resolution WHERE row_ordinal=? AND field=? AND locale=?", (got["row_ordinal"],got["field"],got["locale"])).fetchone()
        owner = src.execute("SELECT * FROM text_owner WHERE row_ordinal=? AND field=?", (got["row_ordinal"],got["field"])).fetchone()
        proof = {**dict(state), **dict(owner), **proof}
    if name == "event_physical_13":
        proof.update(dict(src.execute("SELECT * FROM numeric_observation WHERE id=?", (got["observation_id"],)).fetchone()))
    return {"resolution_status": proof.get("status", proof.get("state", "unknown")),
            "owner_role_status": proof.get("owner_role_status", proof.get("semantic_owner_status", proof.get("semantic_role", "unknown"))),
            "semantic_owner_mapping": "unresolved" if name in {"narrative_reminder", "event_physical_13"} else "unknown",
            "field_continuity_status": proof.get("field_continuity_status", "unknown"),
            "candidate_count": proof.get("candidate_count"), "selected": proof.get("selected"),
            "selected_candidate_count": proof.get("selected_candidate_count"),
            "locale": proof.get("locale", row["locale"]), "relation_status": "physical_candidate",
            "logical_identity_proven": False}


def character(src, out, key, component, r0, cs, ts):
    """Keep foundation and text-run identity distinct; no copied foundation."""
    if component["adapter"] == "character_foundation":
        count = 0;mapping = {}
        for r in src.execute("SELECT * FROM foundation_primary_row ORDER BY id"):
            sha, blob = _prov(src, r0, r["source_path"])
            identity = (component["family"], component["contract"], r["parser_build_id"], r["ingest_run_id"])
            cs += 1;count += 1
            _container(out,cs,key,"character_foundation:foundation_primary_row",identity,r["id"],"character_physical_row","foundation_primary_row",r["id"],r["source_path"],r["pointer"],sha,blob,r["ordinal"],"physical_source_ordinal")
            mapping[r['id']]=cs
        out.execute("INSERT INTO adapter_coverage VALUES(?,?,?,?,?,?,?,?)",(key,"character_foundation:foundation_primary_row","container","foundation_primary_row",count,count,"complete","foundation parser/run retained; derived Lore/Voice overlay bindings separate"))
        return cs,ts,mapping
    mapping = {r["occurrence_id"]: r for r in out.execute("SELECT * FROM research_container WHERE adapter_id='character_foundation:foundation_primary_row'")}
    n = 0
    for r in src.execute("SELECT c.*,z.row_id,z.field_name,z.owner_pointer FROM candidate c JOIN resolution z ON z.owner_id=c.owner_id AND z.locale=c.locale ORDER BY c.owner_id,c.locale,c.source_path,c.physical_ordinal"):
        pkcol,pk = source_key(src,"candidate",r);parent = mapping[r["row_id"]]
        sha,blob = _prov(src,r0,r["source_path"]);ptr = "/"+r["source_key"]
        child = "character-text-key:"+hashlib.sha256(pk.encode()).hexdigest()
        identity = tuple(component[k] for k in ("family","contract","parser","run"))
        ts += 1;n += 1
        _text(out,ts,parent["id"],key,"character_text:candidate",identity,r["row_id"],"physical_text_candidate",child,"candidate",pk,r["locale"],r["field_name"],r["physical_ordinal"],r["owner_pointer"],r["source_path"],ptr,sha,blob,r["physical_ordinal"],r["raw_text"],pkcol)
    denominator = src.execute("SELECT COUNT(*) FROM candidate").fetchone()[0]
    if n != denominator:
        raise ValueError("Character text candidate denominator mismatch")
    out.execute("INSERT INTO adapter_coverage VALUES(?,?,?,?,?,?,?,?)",(key,"character_text:candidate","text","candidate",n,n,"complete","complete candidates; no logical Character ownership"))
    return cs,ts,{}
