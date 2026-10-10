"""Frozen, family-specific source contract for the Phase 4F2 bounded canary."""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from .util import canonical_json, digest_id, normalize_text, sha256_bytes, sha256_text

FAMILY = "quest_codex_archive"
FAMILY_CONTRACT_VERSION = "quest-codex-archive-evidence-v1"
RUN_FAMILY = "quest_codex_archive_phase4f2_canary"
EXPECTED_COMMIT = "26df1dfbdf05a82bbb1d97506859f3e1c40718d8"
EXPECTED_TREE = "d891ef1d95fdd6c27a1da8a32ea7a61b483647a6"
EXPECTED_SNAPSHOT_ID = f"anime-game-data:7.0:{EXPECTED_COMMIT}"
EXPECTED_BUILD = "CNRELWin7.0.0_R47482070_S47579390_D47579390"
EXPECTED_OBSERVED_AT = "2026-08-19T00:00:00+08:00"
MANIFEST_ID = "anime-game-data-7.0-phase4f2-quest-codex-canary"
EXPECTED_MANIFEST_SHA256 = "cdac939a274169d7be7f3d899c9f3df865265b9af676782e554dc0b6b21687e2"
VERSIONS = {
    "family_contract": FAMILY_CONTRACT_VERSION,
    "parser": "quest-codex-phase4f2-canary-parser-v1",
    "projection": "quest-codex-phase4f2-family-projection-v1",
    "relationship_rule": "phase4f1-exact-source-coordinate-candidates-v1",
    "text_rule": "explicit-chapter-owner-locale-main-medium-v1",
    "asset_rule": "raw-reference-observation-v1",
    "identity_rule": "snapshot-run-source-coordinate-occurrence-v1",
}
PRIMARY_PATH = "ExcelBinOutput/QuestCodexExcelConfigData.json"
SUPPORT_PATH = "ExcelBinOutput/ChapterExcelConfigData.json"
QUEST_LOOKUP_PATH = "ExcelBinOutput/QuestExcelConfigData.json"
SELECTED_IDS = (10010001, 10130006, 10680001, 10810001, 11020001)
AUDIT_ARTIFACTS = {
    "phase4f1-audit-manifest.json": "268bc02941ab260422651a014735ca79d9f99fc5585bd5384b3b58d2991dff11",
    "phase4f1-source-inventory.json": "5c4b9c2095c1f541a6b7aa906a5c71450b3acc9d9a8de613afb883f2bdd724b1",
    "phase4f1-row-identity-audit.json": "c066ed9e7d9378be243a5dc7189bd92f79f14875368b6a1a21f3aaa04da9f133",
    "phase4f1-relationship-candidate-audit.json": "b6a807c9c2fbbf8ca7bc034f745c779a68ef7d5df572ad4286372e52ffe2a631",
    "phase4f1-text-evidence-audit.json": "d0a46a38bb3a3659493889a1304ccc12946dcff3bd285ac5d34e0d923414727c",
    "phase4f1-asset-opaque-audit.json": "c8ee6959bef371c3c5af736acdcd1128fbc8109ce23b10280bff0e4c704129f6",
    "phase4f1-adjacent-exclusions.json": "7797c9d06b8027f905debc28f59170bfda6b37d1607a0826b1347d356ce7b21e",
    "phase4f1-canary-selection.json": "eb5dd6a8000f737196fb208412e6d203f15873893eba170868cb20f2f64c11f2",
    "phase4f1-reconciliation.json": "5186885638a0ba1fe6ae30fbdac300e9b8379cccc99945a9544313d6f1fb1768",
}

class QuestCodexPhase4F2ContractError(RuntimeError):
    pass

def _fail(message: str) -> None:
    raise QuestCodexPhase4F2ContractError(message)

def _strict(raw: bytes, label: str) -> Any:
    def reject(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                _fail(f"duplicate JSON key in {label}: {key}")
            value[key] = item
        return value
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=reject)
    except (UnicodeError, json.JSONDecodeError) as exc:
        _fail(f"cannot parse {label}: {exc}")

def verify_source_pin(root: Path) -> None:
    root = root.resolve()
    try:
        top = subprocess.run(["git","--no-optional-locks","-C",str(root),"rev-parse","--show-toplevel"],check=True,capture_output=True,text=True,timeout=60).stdout.strip()
        status = subprocess.run(["git","--no-optional-locks","-C",str(root),"status","--porcelain=v1","--untracked-files=all"],check=True,capture_output=True,text=True,timeout=60).stdout.strip()
        commit = subprocess.run(["git","--no-optional-locks","-C",str(root),"rev-parse","HEAD"],check=True,capture_output=True,text=True,timeout=60).stdout.strip()
        tree = subprocess.run(["git","--no-optional-locks","-C",str(root),"rev-parse","HEAD^{tree}"],check=True,capture_output=True,text=True,timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        _fail(f"cannot inspect pinned QuestCodex source: {exc}")
    if Path(top).resolve()!=root or status or commit!=EXPECTED_COMMIT or tree!=EXPECTED_TREE:
        _fail("QuestCodex source checkout pin/cleanliness mismatch")

def _reports(repo_root: Path) -> dict[str, Any]:
    base=repo_root.resolve()/"reports/phase4f1-quest-codex"
    result={}
    for name,digest in AUDIT_ARTIFACTS.items():
        raw=(base/name).read_bytes()
        if sha256_bytes(raw)!=digest:
            _fail(f"Phase 4F1 artifact digest mismatch: {name}")
        value=_strict(raw,name)
        if not isinstance(value,dict) or value.get("audit_version")!="4F1.1":
            _fail(f"Phase 4F1 artifact contract mismatch: {name}")
        result[name]=value
    summary=result["phase4f1-reconciliation.json"]["summary"]
    if summary.get("status")!="pass" or summary.get("check_count")!=11 or summary.get("passed_checks")!=11 or not all(summary.get("checks",{}).values()):
        _fail("Phase 4F1 reconciliation mismatch")
    return result

def load_manifest(path: str|Path) -> dict[str,Any]:
    raw=Path(path).resolve().read_bytes()
    if EXPECTED_MANIFEST_SHA256 and sha256_bytes(raw)!=EXPECTED_MANIFEST_SHA256:
        _fail("Phase 4F2 manifest SHA-256 mismatch")
    value=_strict(raw,"Phase 4F2 manifest")
    keys={"schema_version","manifest_id","source","versions","audit_binding","boundary","identity","files","selected_occurrences","expected"}
    if not isinstance(value,dict) or set(value)!=keys or value["schema_version"]!=1 or value["manifest_id"]!=MANIFEST_ID:
        _fail("Phase 4F2 manifest root/identity mismatch")
    if value["versions"]!=VERSIONS or value["audit_binding"]!=AUDIT_ARTIFACTS:
        _fail("Phase 4F2 manifest version/audit binding mismatch")
    boundary=value["boundary"]
    if boundary!={"family":FAMILY,"family_contract_version":FAMILY_CONTRACT_VERSION,"selected_ids":list(SELECTED_IDS),"selected_primary_rows":5,"selected_support_rows":5,"supplement_count":0,"full_quest_codex_scan":False,"corpus_complete":False,"coverage":"bounded_canary"}:
        _fail("Phase 4F2 bounded boundary mismatch")
    identity=value["identity"]
    if identity!={"raw_evidence_identity":"snapshot_parser_build_ingest_run_source_file_source_record_coordinate_occurrence","logical_identity_created":False,"candidate_identity_effect":"none_physical_lookup_occurrences_only","text_hash_identity_effect":"none_owner_locale_candidate_locator_only","asset_identity_effect":"none_raw_reference_only","get_scope":"single_physical_row_container_only"}:
        _fail("Phase 4F2 identity contract mismatch")
    return value

def _table(path: str) -> str:
    return Path(path).stem

def _digest(items: list[dict[str,Any]]) -> str:
    return sha256_text(canonical_json(sorted(canonical_json(item) for item in items)))

def derive_source_contract(source_root: str|Path, repo_root: str|Path, manifest: Mapping[str,Any]|None=None) -> dict[str,Any]:
    root=Path(source_root).resolve(); verify_source_pin(root); reports=_reports(Path(repo_root))
    inventory=reports["phase4f1-source-inventory.json"]
    wanted_paths={PRIMARY_PATH,SUPPORT_PATH,QUEST_LOOKUP_PATH} | {x["relative_path"] for x in inventory["files"] if x["relative_path"].startswith("TextMap/")}
    files=[]; values={}
    for item in inventory["files"]:
        path=item["relative_path"]
        if path not in wanted_paths: continue
        raw=(root/path).read_bytes()
        if len(raw)!=item["size_bytes"] or sha256_bytes(raw)!=item["sha256"]:
            _fail(f"source file seal mismatch: {path}")
        values[path]=_strict(raw,path)
        files.append({key:item[key] for key in ("relative_path","format","role","source_records","size_bytes","sha256","blob_oid")})
    if len(files)!=9: _fail("Phase 4F2 source file boundary mismatch")

    selection=reports["phase4f1-canary-selection.json"]
    if tuple(selection["selected_ids"])!=SELECTED_IDS or selection["supplements"] or selection["summary"]["selected_chapter_support_rows"]!=5:
        _fail("Phase 4F1 canary selection mismatch")
    selected_scopes=[]
    for item in selection["selected_occurrences"]:
        selected_scopes.extend((item["quest_codex_scope"],item["chapter_scope"]))
    selected={(scope["path"],scope["row"]) for scope in selected_scopes}
    row_report=reports["phase4f1-row-identity-audit.json"]
    audit_rows={(x["scope"]["path"],x["scope"]["row"]):x for x in row_report["row_occurrences"]}
    rows=[]; source_records=[]
    for path,index in sorted(selected,key=lambda key:(0 if key[0]==PRIMARY_PATH else 1,key[1])):
        audit=audit_rows[(path,index)]; raw_row=values[path][index]
        if raw_row.get("id")!=audit["native_id"]: _fail("row locator mismatch")
        checksum=sha256_text(canonical_json(raw_row)); pointer=f"/{index}"
        item={"evidence_kind":audit["evidence_kind"],"table":_table(path),"relative_path":path,"row_index":index,"pointer":pointer,"native_locator_value":str(audit["native_id"]),"raw_checksum":checksum,"raw_value":raw_row}
        rows.append(item); source_records.append({"relative_path":path,"source_pointer":pointer,"record_kind":audit["evidence_kind"],"native_id":str(audit["native_id"]),"raw_checksum":checksum,"raw_value":raw_row})

    raw_fields=[]
    for item in row_report["raw_field_occurrences"]:
        scope=item["scope"]
        if (scope["path"],scope["row"]) in selected:
            raw_fields.append({"table":_table(scope["path"]),"row_index":scope["row"],"source_pointer":scope["pointer"],"field_name":scope["field"],"field_presence":"present","raw_value":item["raw_value"]})
    for row in rows:
        if row["evidence_kind"]!="quest_codex_entry": continue
        for field in ("tabType","isDisuse"):
            if field not in row["raw_value"]:
                raw_fields.append({"table":row["table"],"row_index":row["row_index"],"source_pointer":f"/{row['row_index']}/{field}","field_name":field,"field_presence":"missing","raw_value":None})

    rel_report=reports["phase4f1-relationship-candidate-audit.json"]
    relationships=[]; relationship_candidates=[]
    for source in (rel_report["chapter_relationships"],rel_report["parent_quest_candidate_relationships"]):
        for item in source:
            scope=item["source_scope"]
            if (scope["path"],scope["row"]) not in selected: continue
            rel_key=f"{scope['path']}:{scope['pointer']}"
            relationships.append({"relationship_key":rel_key,"table":_table(scope["path"]),"row_index":scope["row"],"source_pointer":scope["pointer"],"field_name":scope["field"],"raw_value":item["raw_value"],"target_table":item["target_table"],"target_field":item["target_field"],"resolution_status":item["status"]})
            for ordinal,target in enumerate(item["target_scopes"]):
                raw_row=values[target["path"]][target["row"]]; checksum=sha256_text(canonical_json(raw_row))
                candidate={"relationship_key":rel_key,"candidate_ordinal":ordinal,"source_relative_path":target["path"],"source_pointer":target["pointer"],"target_table":item["target_table"],"target_field":item["target_field"],"target_raw_value":raw_row[item["target_field"]],"raw_checksum":checksum,"raw_value":raw_row}
                relationship_candidates.append(candidate)
                record={"relative_path":target["path"],"source_pointer":target["pointer"],"record_kind":"relationship_lookup_candidate","native_id":str(raw_row.get("id",raw_row.get(item["target_field"]))),"raw_checksum":checksum,"raw_value":raw_row}
                if not any(r["relative_path"]==record["relative_path"] and r["source_pointer"]==record["source_pointer"] for r in source_records): source_records.append(record)

    support_rows={row["row_index"] for row in rows if row["evidence_kind"]=="chapter_support"}
    text_report=reports["phase4f1-text-evidence-audit.json"]
    owners=[]
    for item in text_report["owner_occurrences"]:
        scope=item["scope"]
        if scope["row"] in support_rows:
            owners.append({"owner_key":f"{scope['path']}:{scope['pointer']}","table":_table(scope["path"]),"row_index":scope["row"],"source_pointer":scope["pointer"],"field_name":scope["field"],"field_role":item["field_role"],"textmap_hash":str(item["raw_hash"]),"raw_value":item["raw_hash"]})
    owner_keys={x["owner_key"] for x in owners}; resolutions=[]; text_candidates=[]
    for item in text_report["resolutions"]:
        scope=item["owner_scope"]; key=f"{scope['path']}:{scope['pointer']}"
        if key not in owner_keys: continue
        chosen=item["candidates"][0] if item["status"]=="unique_medium" else None
        resolutions.append({"owner_key":key,"table":_table(scope["path"]),"row_index":scope["row"],"source_pointer":scope["pointer"],"locale":item["locale"],"resolution_status":item["status"],"raw_text":None if chosen is None else chosen["raw_text"],"normalized_text":None if chosen is None else normalize_text(chosen["raw_text"]),"raw_checksum":None if chosen is None else sha256_text(chosen["raw_text"]),"normalized_checksum":None if chosen is None else sha256_text(normalize_text(chosen["raw_text"]))})
        for candidate in item["candidates"]:
            raw_text=candidate["raw_text"]
            text_candidates.append({"owner_key":key,"locale":item["locale"],"shard_kind":candidate["shard"],"source_relative_path":candidate["source_path"],"source_pointer":candidate["source_pointer"],"raw_text":raw_text,"normalized_text":normalize_text(raw_text),"raw_checksum":sha256_text(raw_text),"normalized_checksum":sha256_text(normalize_text(raw_text))})
            record={"relative_path":candidate["source_path"],"source_pointer":candidate["source_pointer"],"record_kind":"quest_codex_text_candidate","native_id":item["hash"],"raw_checksum":sha256_text(raw_text),"raw_value":raw_text}
            if record not in source_records: source_records.append(record)

    asset_report=reports["phase4f1-asset-opaque-audit.json"]
    assets=[]; opaque=[]
    for item in asset_report["asset_occurrences"]:
        scope=item["scope"]
        if scope["row"] in support_rows:
            assets.append({"table":_table(scope["path"]),"row_index":scope["row"],"source_pointer":scope["pointer"],"field_name":scope["field"],"asset_kind":item["asset_role"],"raw_reference":str(item["raw_value"]) if item["status"]=="observed" else None,"reference_status":item["status"]})
    for group,array_index in ((asset_report["opaque_field_occurrences"],None),(asset_report["opaque_array_positions"],"scope")):
        for item in group:
            scope=item["scope"]
            if (scope["path"],scope["row"]) in selected:
                opaque.append({"table":_table(scope["path"]),"row_index":scope["row"],"source_pointer":scope["pointer"],"field_name":scope["field"],"array_index":scope["array_index"] if array_index else None,"raw_value":item["raw_value"],"resolution_status":"opaque_unresolved"})

    collections={"rows":rows,"raw_fields":raw_fields,"relationships":relationships,"relationship_candidates":relationship_candidates,"opaque":opaque,"text_owners":owners,"text_resolutions":resolutions,"text_candidates":text_candidates,"assets":assets,"source_records":source_records}
    counts={key:len(value) for key,value in collections.items()}|{"files":len(files)}
    status_counts={
        "row_kinds":dict(sorted(Counter(x["evidence_kind"] for x in rows).items())),
        "field_presence":dict(sorted(Counter(x["field_presence"] for x in raw_fields).items())),
        "relationships":dict(sorted(Counter(x["resolution_status"] for x in relationships).items())),
        "opaque":dict(sorted(Counter(x["resolution_status"] for x in opaque).items())),
        "text_resolutions":dict(sorted(Counter(x["resolution_status"] for x in resolutions).items())),
        "assets":dict(sorted(Counter(x["reference_status"] for x in assets).items())),
    }
    digests={key:_digest(value) for key,value in collections.items() if key!="source_records"}
    result={"files":files,**collections,"counts":counts,"status_counts":status_counts,"occurrence_set_digests":digests}
    if manifest is not None:
        selected_manifest=[{"id":x["id"],"quest_codex_scope":x["quest_codex_scope"],"chapter_scope":x["chapter_scope"]} for x in selection["selected_occurrences"]]
        if manifest["selected_occurrences"]!=selected_manifest or manifest["files"]!=files:
            _fail("Phase 4F2 manifest physical boundary mismatch")
        expected={key:result[key] for key in ("counts","status_counts","occurrence_set_digests")}
        if manifest["expected"]!=expected: _fail("Phase 4F2 derived contract differs from sealed manifest")
    return result

def expected_identity(manifest: Mapping[str,Any]) -> dict[str,str]:
    parser_build_id=digest_id("pb-quest-codex-phase4f2",canonical_json({"versions":manifest["versions"],"manifest_id":manifest["manifest_id"]}))
    config_checksum=sha256_text(canonical_json({"manifest_id":manifest["manifest_id"],"versions":manifest["versions"],"selected_occurrences":manifest["selected_occurrences"],"audit_binding":manifest["audit_binding"]}))
    ingest_run_id=digest_id("ir-quest-codex-phase4f2",EXPECTED_SNAPSHOT_ID,parser_build_id,config_checksum)
    return {"snapshot_id":EXPECTED_SNAPSHOT_ID,"parser_build_id":parser_build_id,"config_checksum":config_checksum,"ingest_run_id":ingest_run_id}

def occurrence_id(kind: str, identity: Mapping[str,str], *parts: Any) -> str:
    return digest_id(f"quest-codex-{kind}",identity["snapshot_id"],identity["parser_build_id"],identity["ingest_run_id"],*parts)
