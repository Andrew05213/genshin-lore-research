"""Real R0/Readable/Subtitle builders and oracles; synthetic source bytes only."""
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from scripts import build as driver
from genshin_lore import readable_7_1_build as readable
from genshin_lore import readable_7_1_verify as readable_oracle
from genshin_lore import direct_text_7_1_build as subtitle
from genshin_lore import direct_text_7_1_verify as subtitle_oracle
from test_r0_build_paths import real_job


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def blob(raw):
    import hashlib
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


@pytest.fixture
def chain(real_job, monkeypatch, request):
    job, source, code = real_job.job, real_job.job.source, real_job.code
    body = source / "Readable/CHS/synthetic.txt"
    body.parent.mkdir(parents=True)
    body.write_text("synthetic first\r\n合成第二行\n", encoding="utf-8", newline="")
    (source / "Subtitle/synthetic.srt").write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nsynthetic cue\n", encoding="utf-8", newline="")
    localization = [{"id": 1, "readable": "ART/UI/Readable/CHS/synthetic", "subtitle": "synthetic.mihoyobin"}]
    variant = getattr(request, "param", None)
    if variant == "empty":
        for directory in ("Readable", "Subtitle"):
            for path in (source / directory).rglob("*"):
                if path.is_file():
                    path.write_bytes(b"")
    elif variant == "partial":
        localization.append({"id": 2, "readable": "ART/UI/Readable/missing", "subtitle": "missing.mihoyobin"})
    write(source / "ExcelBinOutput/LocalizationExcelConfigData.json", localization)

    def paths(prefix):
        base = source / prefix
        return sorted(p for p in base.rglob("*") if p.is_file()) if base.is_dir() else [base]

    def git(args, **kwargs):
        if "rev-parse" in args:
            return readable.COMMIT + "\n" + readable.TREE + "\n"
        if "status" in args:
            return "" if kwargs.get("text") else b""
        assert "ls-tree" in args
        records = [f"100644 blob {blob(p.read_bytes())}\t{p.relative_to(source).as_posix()}" for p in paths(args[-1])]
        if "-z" in args:
            return ("\0".join(records) + "\0").encode()
        return "\n".join(records) + "\n"

    # The game commit cannot describe these synthetic bytes. Only Git identity
    # replies are replaced; file reads, SQL, publication and rowset oracles run.
    for module in (readable, readable_oracle, subtitle_oracle):
        monkeypatch.setattr(module, "subprocess", SimpleNamespace(check_output=git))

    tree = {p.relative_to(source).as_posix(): blob(p.read_bytes()) for p in paths("Readable")}
    scope = {"contract": "document-readable-7.1-source-scope-v1", "source_commit": readable.COMMIT,
             "source_tree": readable.TREE, "snapshot_id": readable.SNAPSHOT,
             "readable": {"file_count": len(tree), "path_blob_set_sha256": readable.sha(
                 (json.dumps(sorted(tree.items()), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode())},
             "r0_dependency": {"size_bytes": 4711841792, "sha256": "0" * 64,
                               "ingest_run_id": subtitle.R0_RUN}}
    readable_scope = code / "manifests/document-readable-7.1/source-scope.json"
    write(readable_scope, scope)

    def entry(p):
        raw = p.read_bytes()
        return {"path": p.relative_to(source).as_posix(), "size_bytes": len(raw),
                "sha256": readable.sha(raw), "blob": blob(raw)}

    subtitle_scope = code / "manifests/direct-text-7.1/subtitle-scope.json"
    write(subtitle_scope, {"files": [entry(p) for p in paths("Subtitle")],
        "file_denominator": len(paths("Subtitle")),
        "lookup_sources": [entry(source / "ExcelBinOutput/LocalizationExcelConfigData.json")],
        "r0_dependency": {"artifact_size_bytes": 4711841792, "artifact_sha256": "0" * 64,
                          "run": subtitle.R0_RUN, "contract": subtitle.R0_CONTRACT,
                          "family": "raw_subtitle_json_occurrence"}})
    originals = {p: p.read_bytes() for p in (readable_scope, subtitle_scope)}
    job.r0()  # Actual builder, independent source archive oracle and v2 proof.

    def document(path):
        release = {"snapshot_id": readable.SNAPSHOT, "contract": "document-7.1-evidence-v1",
                   "parser_build_id": "synthetic-document-parser", "run_id": "synthetic-document-run"}
        with sqlite3.connect(path) as db:
            db.executescript((readable.ROOT / "schemas/document-v1.sql").read_text())
            db.execute("INSERT INTO document_release VALUES(1,?)", (json.dumps(release),))
        return {**release, "artifact": driver.seal(path)}

    def check_document(path, report):
        # Empty Document is a companion fixture, not a claim that its production
        # source parser was run. Consumers must still enforce its exact proof.
        with sqlite3.connect(path) as db:
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert json.loads(db.execute("SELECT manifest_json FROM document_release").fetchone()[0])["run_id"] == report["run_id"]
        return {"status": "PASS", "artifact": driver.seal(path)}

    job.task("document", document, check_document)
    return SimpleNamespace(job=job, originals=originals)


def test_actual_readable_and_subtitle_use_current_qualified_r0(chain):
    job = chain.job
    job.readable()
    for stage, relative, fields in (
        ("readable", "manifests/document-readable-7.1/source-scope.json", ("size_bytes", "sha256")),
        ("subtitle", "manifests/direct-text-7.1/subtitle-scope.json", ("artifact_size_bytes", "artifact_sha256")),
    ):
        path = job.metadata_path(relative)
        scope = json.loads(path.read_text())
        actual = driver.seal(job.path("r0"))
        assert (scope["r0_dependency"][fields[0]], scope["r0_dependency"][fields[1]]) == (actual["size_bytes"], actual["sha256"])
        proof = json.loads((job.output / stage / "verified.json").read_text())
        assert proof["independent_verifier_completed"] is True
        assert str(path.resolve()) in proof["identity"]["stage_inputs"]
        assert json.loads((job.output / stage / "verify.json").read_text())["counts"][stage + "_file"] == 2
    assert all(p.read_bytes() == raw for p, raw in chain.originals.items())


@pytest.mark.parametrize("chain", ["empty", "partial"], indirect=True)
def test_legal_empty_bodies_and_missing_candidates_pass_exact_oracles(chain, request):
    job = chain.job
    job.readable()
    if request.node.callspec.params["chain"] == "empty":
        assert job.report("readable")["counts"]["readable_line"] == 0
        assert job.report("subtitle")["counts"]["subtitle_record"] == 0
    else:
        assert job.report("readable")["relation_states"]["missing"] == 1
        assert job.report("subtitle")["relation_states"]["missing"] == 1
    assert all((job.output / stage / "verified.json").is_file() for stage in ("readable", "subtitle"))


@pytest.mark.parametrize("stage", ["r0", "document"])
@pytest.mark.parametrize("mutation", ["missing", "artifact", "report", "sidecar", "incomplete", "environment"])
def test_readable_refuses_unqualified_dependencies_before_writing_binding(chain, stage, mutation):
    job = chain.job
    directory = job.output / stage
    if mutation == "missing":
        (directory / "verified.json").unlink()
    elif mutation == "artifact":
        with job.path(stage).open("ab") as stream:
            stream.write(b"synthetic tamper")
    elif mutation == "report":
        write(directory / "verify.json", {"status": "NOT_RUN"})
    elif mutation == "sidecar":
        Path(str(job.path(stage)) + "-journal").write_bytes(b"synthetic retained diagnostic")
    else:
        path = directory / "verified.json"
        value = json.loads(path.read_text())
        if mutation == "incomplete":
            value["independent_verifier_completed"] = False
        else:
            value["identity"]["environment"]["sqlite"] = "synthetic different environment"
        write(path, value)
    with pytest.raises((ValueError, OSError)):
        job.readable()
    assert not job.metadata_path("manifests/document-readable-7.1/source-scope.json").exists()
    assert not (job.output / "readable").exists()
    if mutation == "sidecar":
        assert Path(str(job.path(stage)) + "-journal").read_bytes() == b"synthetic retained diagnostic"


@pytest.mark.parametrize("kind", ["readable", "subtitle"])
@pytest.mark.parametrize("consumer", ["builder", "oracle"])
def test_bound_scope_does_not_disable_exact_dependency_checks(chain, kind, consumer):
    job = chain.job
    job.readable()
    relative = ("manifests/document-readable-7.1/source-scope.json" if kind == "readable"
                else "manifests/direct-text-7.1/subtitle-scope.json")
    path = job.metadata_path(relative)
    scope = json.loads(path.read_text())
    field = "sha256" if kind == "readable" else "artifact_sha256"
    scope["r0_dependency"][field] = "0" * 64
    write(path, scope)
    with pytest.raises(ValueError, match="R0|dependency artifact"):
        if consumer == "builder" and kind == "readable":
            readable.build(job.source, path, job.output / "forged-readable.sqlite", job.path("r0"),
                           job.path("document"), job.report("document"))
        elif consumer == "builder":
            subtitle.build_subtitle(job.source, path, job.output / "forged-subtitle.sqlite", job.path("r0"))
        elif kind == "readable":
            readable_oracle.verify(job.source, path, job.path(kind), job.path("r0"),
                                   job.path("document"), job.report("document"), job.report(kind))
        else:
            subtitle_oracle.verify_subtitle(job.source, path, job.path(kind), job.path("r0"),
                runtime_manifest_path=job.output / "bound-scopes/subtitle-runtime.json")


@pytest.mark.parametrize("kind", ["readable", "subtitle"])
def test_independent_oracles_reject_body_tampering_with_recomputed_artifact_seal(chain, kind):
    job = chain.job
    job.readable()
    table = "readable_line" if kind == "readable" else "subtitle_record"
    with sqlite3.connect(job.path(kind)) as db:
        trigger = db.execute("SELECT sql FROM sqlite_master WHERE name=?", (table + "_immutable_update",)).fetchone()[0]
        db.execute(f"DROP TRIGGER {table}_immutable_update")
        db.execute(f"UPDATE {table} SET raw_text='synthetic forged body'")
        db.execute(trigger)
    report = job.report(kind)
    report["artifact"] = driver.seal(job.path(kind))
    with pytest.raises(ValueError, match="business"):
        if kind == "readable":
            readable_oracle.verify(job.source, job.metadata_path("manifests/document-readable-7.1/source-scope.json"),
                job.path(kind), job.path("r0"), job.path("document"), job.report("document"), report)
        else:
            runtime = json.loads((job.output / "bound-scopes/subtitle-runtime.json").read_text())
            runtime["artifact"] = report["artifact"]
            path = job.output / "tampered-subtitle-runtime.json"
            write(path, runtime)
            subtitle_oracle.verify_subtitle(job.source, job.metadata_path("manifests/direct-text-7.1/subtitle-scope.json"),
                job.path(kind), job.path("r0"), runtime_manifest_path=path)


def test_existing_foreign_scope_is_not_overwritten(chain):
    job = chain.job
    path = job.metadata_path("manifests/document-readable-7.1/source-scope.json")
    write(path, {"contract": "unknown foreign scope"})
    before = path.read_bytes()
    with pytest.raises(FileExistsError, match="binding drift"):
        job.readable()
    assert path.read_bytes() == before
    assert not (job.output / "readable").exists()


def test_another_outputs_receipt_cannot_qualify_local_r0(chain):
    job = chain.job
    other = object.__new__(driver.Rebuild)
    other.source, other.history = job.source, job.history
    other.output = job.output.parent / "other-output"
    other.output.mkdir()
    other.r0()
    (job.output / "r0/verified.json").write_bytes((other.output / "r0/verified.json").read_bytes())
    with pytest.raises(ValueError, match="report drift"):
        job.readable()
    assert not job.metadata_path("manifests/document-readable-7.1/source-scope.json").exists()
