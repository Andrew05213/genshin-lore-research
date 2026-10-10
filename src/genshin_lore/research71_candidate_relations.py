"""Declared 7.1 physical relation scopes; no logical target selection."""
from __future__ import annotations

import json

from .research71_candidate_adapters import canonical, source_key, primary_columns

# table, physical parent FK, candidate table, observation FK
SCOPES = {
    "character_foundation": [("foundation_relation","source_row_id","foundation_candidate","relation_id")],
    "quest_review_binding": [("bound_locator","source_review_row_id","bound_candidate","locator_id")],
    "native_message": [("native71_relation","row_id","native71_candidate","relation_id")],
    "event_native_text_17": [("event71_relation","row_id","event71_candidate","relation_id")],
    "native_successor_physical": [("relation_locator","row_id","relation_candidate","locator_id")],
    "native_high_value_physical": [("relation_locator","row_id","relation_candidate","locator_id")],
    "codexquest_archive": [("codex_navigation","root_id","codex_candidate","navigation_id")],
    "narrative_npc": [("locator","row_ordinal","physical_candidate",None)],
    "narrative_dialog": [("locator","row_ordinal","physical_candidate",None)],
    "narrative_reminder": [("locator","row_ordinal","physical_candidate",None)],
    "talk_physical": [("talk_physical_reference","file_id","talk_physical_candidate","reference_id")],
}


def project(src,out,key,component,mapping,rs,ks):
    adapter = component["adapter"]
    for table,parent,ct,fk in SCOPES.get(adapter,()):
        rb,kb = rs,ks
        for value in src.execute(f'SELECT * FROM "{table}" ORDER BY '+','.join('"'+c+'"' for c in primary_columns(src,table))):
            r = dict(value);cid = mapping.get(str(r[parent]))
            if cid is None:
                raise ValueError("relation parent outside declared namespace: "+adapter)
            pkcol,pk = source_key(src,table,r)
            if fk is not None:
                rows = list(src.execute(f'SELECT * FROM "{ct}" WHERE "{fk}"=?', (r.get("id"),)))
            else:
                rows = list(src.execute(f'SELECT * FROM "{ct}" WHERE row_ordinal=? AND field=? AND item_ordinal=?', (r['row_ordinal'],r['field'],r['item_ordinal'])))
            count = r.get("candidate_count", r.get("known_candidate_count",len(rows)))
            if count != len(rows):
                raise ValueError("complete physical candidate set cardinality drift: "+adapter)
            rs += 1
            state = r.get("status",r.get("state","mapping_unresolved"))
            kind = r.get("relation_kind",r.get("kind",r.get("role",r.get("field","physical_reference"))))
            ptr = r.get("source_pointer",r.get("pointer",r.get("field", "/")))
            # Full original row is also an independently auditable source seal.
            out.execute("INSERT INTO research_relation VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(rs,cid,key,adapter+":"+table,table,pkcol,pk,str(kind),str(ptr),canonical(r),str(state),int(r.get("search_complete",0)),count,r.get("candidate_namespace",r.get("target_boundary","declared_physical_candidates"))))
            for value in rows:
                x = dict(value);cp,cv = source_key(src,ct,x)
                family = x.get("target_family")
                path = x.get("target_source_path",x.get("candidate_source_path"))
                pointer = x.get("target_source_pointer",x.get("target_pointer",x.get("candidate_pointer")))
                occurrence = x.get("target_occurrence_id",x.get("target_row_id",x.get("target_primary_id")))
                contract = x.get("target_contract");parser = x.get("target_parser");run = x.get("target_run",x.get("target_run_id"))
                if adapter.startswith("narrative_"):
                    family = {"npc":"npc_archive","dialog":"dialog_archive","reminder":"reminder_archive"}.get(r.get("target_family"))
                if adapter in {"character_foundation","event_native_text_17"}:
                    family,contract,parser,run = (component[k] for k in ("family","contract","parser","run"))
                if adapter == "codexquest_archive":
                    family = "quest_codex_archive"
                ks += 1
                out.execute("INSERT INTO research_candidate VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",(ks,rs,ct,cp,cv,family,contract,parser,run,None if occurrence is None else str(occurrence),path,pointer,canonical(x)))
        nd = src.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        nc = src.execute(f'SELECT COUNT(*) FROM "{ct}"').fetchone()[0]
        if (rs-rb,ks-kb) != (nd,nc):
            raise ValueError("relation/candidate full rowset omitted or duplicated")
        for table_,kind,n in ((table,"relation",nd),(ct,"candidate",nc)):
            out.execute("INSERT INTO adapter_coverage VALUES(?,?,?,?,?,?,?,?)",(key,adapter+":"+table_,kind,table_,n,n,"complete","all original physical rows and states; target may remain unresolved"))
    return rs,ks
