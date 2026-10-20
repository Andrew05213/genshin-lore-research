"""Output-local proof boundaries use synthetic bytes, never game data."""
import hashlib
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from scripts.build import Rebuild, ROOT, seal
from genshin_lore import character_7_1_successor_build as builder
from genshin_lore import character_7_1_successor_verify as verifier


def write(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def text_output(path):
    db = sqlite3.connect(path)
    db.executescript((ROOT / builder.SCHEMAS["text"]).read_text())
    return db


def test_successor_release_binds_actual_foundation_and_changes_run(tmp_path):
    releases = []
    for name, content in (("first", b"synthetic one"), ("second", b"synthetic two")):
        foundation = tmp_path / (name + ".foundation")
        foundation.write_bytes(content)
        db = text_output(tmp_path / (name + ".sqlite"))
        try:
            release = builder._release("text", db, foundation)
            assert release["foundation_sha256"] == hashlib.sha256(content).hexdigest()
            assert release["foundation_size_bytes"] == len(content)
            releases.append(release)
        finally:
            db.close()
    assert releases[0]["ingest_run_id"] != releases[1]["ingest_run_id"]


@pytest.fixture
def fresh(tmp_path, monkeypatch, request):
    from genshin_lore import character_7_1_foundation_binding as binding
    from genshin_lore import character_7_1_foundation_verify as foundation_oracle

    job = object.__new__(Rebuild)
    job.output = tmp_path / "fresh"
    job.output.mkdir()
    job.source = tmp_path / "source"
    job.source.mkdir()
    job.history = None
    expected = b"synthetic independently checked physical foundation"
    recipe = json.loads((ROOT / "recipes/character-foundation.json").read_text())
    if getattr(request, "param", None) == "current":
        recipe.update(parser_build_id="character71:synthetic-current-parser",
                      ingest_run_id="character71:synthetic-current-run")
    calls = []

    def oracle(source, scope, artifact, *, expected_artifact):
        calls.append(Path(artifact))
        if Path(artifact).read_bytes() != expected:
            raise ValueError("synthetic independent source mismatch")
        return {
            "status": "PASS",
            "contract": "character-7.1-physical-foundation-independent-verifier-v1",
            "artifact": seal(artifact), "source_scope_sha256": seal(scope)["sha256"],
            "parser_build_id": recipe["parser_build_id"],
            "ingest_run_id": recipe["ingest_run_id"],
            "silent_skips": 0, "wrong_cross_snapshot_identity_merges": 0,
        }

    # Only the expensive source row oracle is substituted at this metadata
    # boundary. Rebuild.task, v2 proofs, seal checking and replay are real.
    monkeypatch.setattr(foundation_oracle, "verify", oracle)

    def build(path):
        path.write_bytes(expected)
        return {**recipe, "artifact": seal(path)}

    job.task("character_foundation", build,
             lambda path, report: oracle(job.source, ROOT / recipe["source_scope"], path,
                                         expected_artifact=report["artifact"]))
    artifact = job.path("character_foundation")
    manifest = {**recipe, "artifact": {**seal(artifact), "machine_local_path": str(artifact)}}
    receipt_path = artifact.parent / "verified.json"
    manifest["local_rebuild"] = {"contract": "local-character-foundation-v1", "receipt": seal(receipt_path)}
    manifest_path = job.metadata_path("manifests/character-7.1-physical-foundation/immutable-runtime.json")
    manifest_path.parent.mkdir(parents=True)
    write(manifest_path, manifest)
    return SimpleNamespace(job=job, artifact=artifact, manifest=manifest, manifest_path=manifest_path,
                           receipt_path=receipt_path, binding=binding, calls=calls)


def qualify(fresh):
    return fresh.binding.qualify_local_foundation(fresh.artifact, fresh.manifest_path, fresh.manifest)


def reseal(fresh):
    receipt = json.loads(fresh.receipt_path.read_text())
    receipt["records"] = {name: seal(fresh.artifact.parent / name)
                          for name in ("build.json", "verify.json", "invocation.json")}
    write(fresh.receipt_path, receipt)
    fresh.manifest["local_rebuild"]["receipt"] = seal(fresh.receipt_path)
    write(fresh.manifest_path, fresh.manifest)


def test_fresh_foundation_requires_and_replays_complete_independent_proof(fresh):
    count = len(fresh.calls)
    assert qualify(fresh) == seal(fresh.artifact)
    assert len(fresh.calls) == count + 1


@pytest.mark.parametrize("fresh", ["current"], indirect=True)
def test_current_foundation_namespace_requires_replayed_proof(fresh):
    assert qualify(fresh) == seal(fresh.artifact)


@pytest.mark.parametrize("fresh", ["current"], indirect=True)
@pytest.mark.parametrize("field", ["parser_build_id", "ingest_run_id"])
def test_current_namespace_cannot_be_rebound_by_resealing_reports(fresh, field):
    fresh.manifest[field] = "synthetic-forged-identity"
    for name in ("build.json", "verify.json"):
        path = fresh.artifact.parent / name
        report = json.loads(path.read_text())
        report[field] = fresh.manifest[field]
        write(path, report)
    reseal(fresh)
    with pytest.raises(ValueError):
        qualify(fresh)


@pytest.mark.parametrize("fresh", ["current"], indirect=True)
@pytest.mark.parametrize("consumer", [builder.qualify_foundation, verifier._foundation])
def test_current_namespace_without_proof_cannot_enter_historical_mode(fresh, consumer):
    del fresh.manifest["local_rebuild"]
    write(fresh.manifest_path, fresh.manifest)
    with pytest.raises(ValueError):
        consumer(fresh.artifact, foundation_manifest=fresh.manifest_path)


def test_successor_release_uses_qualified_current_foundation_run(tmp_path):
    foundation = tmp_path / "foundation"
    foundation.write_bytes(b"synthetic current foundation")
    conn = text_output(tmp_path / "text.sqlite")
    try:
        release = builder._release("text", conn, foundation, foundation_run_id="synthetic-current-run")
        assert release["foundation_run_id"] == "synthetic-current-run"
        assert release["ingest_run_id"] == builder._identity(
            "text", "run", release["parser_build_id"], "synthetic-current-run",
            seal(foundation)["sha256"], None)
    finally:
        conn.close()


@pytest.mark.parametrize("consumer", [builder._qualify_text, verifier._text])
def test_current_text_dependency_requires_exact_current_run(tmp_path, consumer):
    foundation = tmp_path / "foundation"
    foundation.write_bytes(b"synthetic current foundation")
    text = tmp_path / "text.sqlite"
    conn = text_output(text)
    try:
        conn.executemany("INSERT INTO resolution VALUES(?,?,?,?,?,?,?,?)",
                         ((str(i), "row", "/field", "field", "1", locale, "missing", 0)
                          for i in range(34817) for locale in ("CHS", "EN", "JP")))
        release = {"contract": "character-7.1-text-successor-v1", "snapshot_id": builder.SNAPSHOT,
                   "foundation_run_id": "synthetic-current-run",
                   "foundation_sha256": seal(foundation)["sha256"],
                   "foundation_size_bytes": foundation.stat().st_size}
        conn.execute("INSERT INTO release VALUES(1,?)", (json.dumps(release),))
        conn.commit()
    finally:
        conn.close()
    accepted = consumer(text, foundation, foundation_run_id="synthetic-current-run")
    accepted.close()
    with pytest.raises(ValueError):
        consumer(text, foundation, foundation_run_id=builder.FOUNDATION_RUN)


@pytest.mark.parametrize("consumer", [builder.qualify_foundation, verifier._foundation])
def test_new_seal_cannot_enter_historical_mode(fresh, consumer):
    del fresh.manifest["local_rebuild"]
    write(fresh.manifest_path, fresh.manifest)
    with pytest.raises(ValueError):
        consumer(fresh.artifact, foundation_manifest=fresh.manifest_path)


@pytest.mark.parametrize("sidecar", ["-wal", "-shm", "-journal"])
def test_foundation_sidecars_rejected_without_deletion(fresh, sidecar):
    path = Path(str(fresh.artifact) + sidecar)
    path.write_bytes(b"retained diagnostic")
    with pytest.raises(ValueError, match="sidecar"):
        qualify(fresh)
    assert path.read_bytes() == b"retained diagnostic"


@pytest.mark.parametrize("field", ["receipt", "artifact", "source_scope", "contract"])
def test_manifest_identity_cannot_be_rebound(fresh, field):
    if field == "receipt":
        fresh.manifest["local_rebuild"]["receipt"]["sha256"] = "0" * 64
    elif field == "artifact":
        fresh.manifest["artifact"]["sha256"] = "0" * 64
    elif field == "source_scope":
        fresh.manifest["source_scope"] = "recipes/character-foundation.json"
    else:
        fresh.manifest["local_rebuild"]["contract"] = "unknown"
    write(fresh.manifest_path, fresh.manifest)
    with pytest.raises(ValueError):
        qualify(fresh)


@pytest.mark.parametrize("value", [None, [], "local-character-foundation-v1"])
def test_malformed_local_qualification_does_not_fall_back(fresh, value):
    fresh.manifest["local_rebuild"] = value
    write(fresh.manifest_path, fresh.manifest)
    with pytest.raises(ValueError):
        qualify(fresh)


@pytest.mark.parametrize("name", ["verified.json", "build.json", "verify.json", "invocation.json"])
def test_missing_or_changed_record_rejected(fresh, name):
    (fresh.artifact.parent / name).unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        qualify(fresh)


@pytest.mark.parametrize("mutation", ["legacy", "incomplete", "stage", "implementation", "environment", "inputs"])
def test_resealed_receipt_cannot_claim_a_different_execution(fresh, mutation):
    receipt = json.loads(fresh.receipt_path.read_text())
    if mutation == "legacy":
        receipt["contract"] = "local-build-stage-receipt-v1"
    elif mutation == "incomplete":
        receipt["independent_verifier_completed"] = False
    else:
        identity = receipt["identity"]
        if mutation == "stage":
            identity["parameters"]["stage"] = "character_text"
        elif mutation == "implementation":
            next(iter(identity["implementation_and_contracts"].values()))["sha256"] = "0" * 64
        elif mutation == "environment":
            identity["environment"]["sqlite"] = "stale SQLite"
        else:
            identity["stage_inputs"] = {str(fresh.artifact): seal(fresh.artifact)}
        invocation_path = fresh.artifact.parent / "invocation.json"
        invocation = json.loads(invocation_path.read_text())
        invocation["identity"] = identity
        write(invocation_path, invocation)
    write(fresh.receipt_path, receipt)
    reseal(fresh)
    with pytest.raises(ValueError):
        qualify(fresh)


@pytest.mark.parametrize("state", ["NOT_RUN", "PARTIAL", "SKIPPED", "UNKNOWN", "FAIL"])
def test_resealed_non_success_verifier_is_rejected(fresh, state):
    path = fresh.artifact.parent / "verify.json"
    value = json.loads(path.read_text())
    value["status"] = state
    write(path, value)
    reseal(fresh)
    with pytest.raises(ValueError):
        qualify(fresh)


def test_resealed_artifact_and_all_reports_still_require_independent_source_oracle(fresh):
    fresh.artifact.write_bytes(b"synthetic forged foundation with recomputed seals")
    actual = seal(fresh.artifact)
    receipt = json.loads(fresh.receipt_path.read_text())
    receipt["artifact"] = actual
    write(fresh.receipt_path, receipt)
    for name in ("build.json", "verify.json"):
        path = fresh.artifact.parent / name
        value = json.loads(path.read_text())
        value["artifact"] = actual
        write(path, value)
    fresh.manifest["artifact"].update(actual)
    reseal(fresh)
    with pytest.raises(ValueError, match="independent source mismatch"):
        qualify(fresh)


def test_manifest_from_another_output_cannot_qualify_current_foundation(fresh, tmp_path):
    other = tmp_path / "other" / "metadata/manifests/character-7.1-physical-foundation/immutable-runtime.json"
    other.parent.mkdir(parents=True)
    write(other, fresh.manifest)
    with pytest.raises(ValueError, match="output"):
        fresh.binding.qualify_local_foundation(fresh.artifact, other, fresh.manifest)


def test_resealed_pass_report_must_match_replayed_oracle(fresh):
    path = fresh.artifact.parent / "verify.json"
    value = json.loads(path.read_text())
    value["silent_skips"] = 1
    write(path, value)
    reseal(fresh)
    with pytest.raises(ValueError, match="oracle/report mismatch"):
        qualify(fresh)


@pytest.mark.parametrize("sha_value", ["0" * 64, builder.FOUNDATION_SHA])
def test_text_from_another_foundation_is_rejected_by_both_consumers(tmp_path, sha_value):
    foundation = tmp_path / "foundation"
    foundation.write_bytes(b"synthetic current foundation")
    text = tmp_path / "text.sqlite"
    with text_output(text) as db:
        db.executemany("INSERT INTO resolution VALUES(?,?,?,?,?,?,?,?)",
                       ((str(i), "row", "/field", "field", "1", locale, "missing", 0)
                        for i in range(34817) for locale in ("CHS", "EN", "JP")))
        release = {"contract":"character-7.1-text-successor-v1", "snapshot_id":builder.SNAPSHOT,
                   "foundation_run_id":builder.FOUNDATION_RUN,
                   "foundation_sha256":sha_value,"foundation_size_bytes":foundation.stat().st_size}
        db.execute("INSERT INTO release VALUES(1,?)", (json.dumps(release),))
    with pytest.raises(ValueError):
        builder._qualify_text(text, foundation)
    with pytest.raises(ValueError):
        verifier._text(text, foundation)


@pytest.mark.parametrize("mutation", ["none", "missing", "extra", "body"])
def test_successor_independent_text_rowset_oracle_stays_exact(tmp_path, monkeypatch, mutation):
    source = tmp_path / "source"
    (source / "TextMap").mkdir(parents=True)
    for locale in ("CHS", "EN", "JP"):
        write(source / f"TextMap/TextMap{locale}.json", {"1":"synthetic independent source text"})
        write(source / f"TextMap/TextMap_Medium{locale}.json", {})
    # This unit exercises the unmodified full rowset oracle with real source
    # file reads. Git pin and source seal qualification are separate boundaries.
    monkeypatch.setattr(verifier, "_source", lambda *_: None)
    foundation = sqlite3.connect(":memory:")
    foundation.execute("CREATE TABLE foundation_text_owner(id,row_id,pointer,field_name,textmap_hash)")
    foundation.executemany("INSERT INTO foundation_text_owner VALUES(?,?,?,?,?)",
                          ((str(i), "row", f"/owner/{i}", "field", "1") for i in range(34817)))
    conn = text_output(tmp_path / "text.sqlite")
    try:
        conn.executemany("INSERT INTO resolution VALUES(?,?,?,?,?,?,?,?)",
                         ((str(i), "row", f"/owner/{i}", "field", "1", locale, "resolved_unique", 1)
                          for i in range(34817) for locale in ("CHS", "EN", "JP")))
        conn.executemany("INSERT INTO candidate VALUES(?,?,?,?,?,?,?)",
                         ((str(i), locale, "main", f"TextMap/TextMap{locale}.json", 0, "1", "synthetic independent source text")
                          for i in range(34817) for locale in ("CHS", "EN", "JP")))
        if mutation == "missing":
            conn.execute("DELETE FROM candidate WHERE owner_id='0' AND locale='CHS'")
        elif mutation == "extra":
            conn.execute("INSERT INTO candidate VALUES('0','CHS','medium','TextMap/TextMap_MediumCHS.json',0,'1','synthetic extra')")
        elif mutation == "body":
            conn.execute("UPDATE candidate SET raw_text='synthetic tamper' WHERE owner_id='0' AND locale='CHS'")
        if mutation == "none":
            result = verifier._verify_text(conn, foundation, source, {})
            assert result["owner_count"] == 34817 and result["candidate_count"] == 104451
        else:
            with pytest.raises(ValueError, match="all exact TextMap candidate rows"):
                verifier._verify_text(conn, foundation, source, {})
    finally:
        conn.close()
        foundation.close()
