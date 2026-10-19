"""Exercise the real R0 path; synthetic Git pin probes are the only oracle stub."""
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from scripts import build as driver
from scripts.validators import source_archive as oracle
from genshin_lore import batch_m3_physical as r0
from genshin_lore import batch_m3_inventory as facade
from genshin_lore import batch_space


@pytest.fixture
def real_job(tmp_path, monkeypatch):
    source=tmp_path/'source'
    payloads={'BinOutput/rows.json':b'[{"id":1},{"id":1}]',
              'ExcelBinOutput/empty.json':b'[]',
              'Readable/body.txt':b'synthetic body',
              'Subtitle/line.json':b'{"key":"synthetic subtitle"}',
              'TextMap/pairs.json':b'{"7":"synthetic first","7":"synthetic second"}'}
    for name,raw in payloads.items():
        p=source/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
    system=tmp_path/'system-temp';system.mkdir()
    monkeypatch.setattr(batch_space,'os',SimpleNamespace(**{**vars(batch_space.os),'name':'nt'}))
    monkeypatch.setattr(batch_space.tempfile,'gettempdir',lambda:str(system))
    # Keep real quota accounting bounded to the synthetic project, not host data.
    code=tmp_path/'code';code.mkdir()
    monkeypatch.setattr(driver,'ROOT',code)
    job=object.__new__(driver.Rebuild)
    job.source=source;job.output=tmp_path/'output';job.output.mkdir();job.history=None
    checks=[]
    def git_pin(args, **kwargs):
        assert args[:3]==['git','-C',str(source)]
        if args[3:]==['rev-parse','HEAD','HEAD^{tree}']:
            return oracle.COMMIT+'\n'+oracle.TREE+'\n'
        assert args[3:]==['status','--porcelain']
        return b''
    # No real source checkout can have the synthetic fixture's bytes and the
    # game's pinned Git SHA. Source scans, row comparison and SQLite are real.
    monkeypatch.setattr(oracle,'subprocess',SimpleNamespace(check_output=git_pin))
    def independent(name, arguments, result):
        assert name=='validators/source_archive.py'
        monkeypatch.setattr('sys.argv',['source_archive',*map(str,arguments)])
        assert oracle.main()==0
        checks.append(result)
        return json.loads(Path(result).read_text())
    monkeypatch.setattr(driver,'script_verify',independent)
    return SimpleNamespace(job=job,system=system,checks=checks,code=code)


def test_actual_rebuild_r0_default_temp_and_independent_oracle(real_job):
    real_job.job.r0()
    directory=real_job.job.output/'r0'
    report=json.loads((directory/'build.json').read_text())
    checked=json.loads((directory/'verify.json').read_text())
    assert Path(report['owned_temp_root']).parent==real_job.system
    assert report['owned_temp_root'] in {row['path'] for row in report['space']['inventory']['roots']}
    assert (checked['file_count'],checked['occurrence_count'])==(5,5)
    assert checked['source_coordinate_equality'] and checked['artifact_unchanged_during_verify']
    assert real_job.checks and (directory/'verified.json').is_file()
    with sqlite3.connect(directory/'artifact.sqlite') as db:
        assert db.execute("SELECT count(*) FROM physical_occurrence p JOIN source_file s USING(source_file_id) WHERE s.root='TextMap'").fetchone()[0]==2


@pytest.mark.parametrize('kind',['canonical','inventory'])
def test_real_r0_entries_share_system_temp_and_preserve_explicit_root(real_job,kind):
    custom=real_job.system.parent/'explicit';custom.mkdir()
    kwargs={'repo_root':real_job.code,'temp_root':custom}
    output=real_job.job.output/'explicit.sqlite'
    build=r0.build_r0_artifact if kind=='canonical' else facade.build_physical_artifact
    result=build(real_job.job.source,output,**kwargs)
    assert Path(result['owned_temp_root'])==custom
    assert output.is_file()


def test_inventory_omitted_artifact_uses_same_system_temp(real_job):
    result=facade.build_physical_artifact(real_job.job.source,repo_root=real_job.code)
    owned=Path(result['owned_temp_root'])
    assert owned.parent==real_job.system
    assert (owned/facade.PHYSICAL_ARTIFACT_NAME).is_file()


@pytest.mark.parametrize('phase',['prepublish','final'])
def test_failed_real_r0_preserves_database_and_never_creates_success(real_job,monkeypatch,phase):
    original=r0.verify_r0_artifact
    calls=[]
    def fail(path,**kwargs):
        result=original(path,**kwargs)
        calls.append(Path(path))
        if (phase=='prepublish' and len(calls)==1) or (phase=='final' and len(calls)==2):
            raise ValueError('synthetic independent check failure')
        return result
    monkeypatch.setattr(r0,'verify_r0_artifact',fail)
    with pytest.raises(ValueError,match='synthetic independent check failure'):
        real_job.job.r0()
    directory=real_job.job.output/'r0'
    assert calls[-1].is_file()
    assert not (directory/'verified.json').exists()
    assert list(directory.glob('failure-*.json'))


def test_real_r0_refuses_existing_output_and_preserves_bytes(real_job):
    path=real_job.job.path('r0');path.parent.mkdir();path.write_bytes(b'previous failure evidence')
    with pytest.raises(ValueError):real_job.job.r0()
    assert path.read_bytes()==b'previous failure evidence'


def test_canonical_builder_refuses_existing_output_without_overwrite(real_job):
    path=real_job.job.output/'existing.sqlite';path.write_bytes(b'original evidence')
    with pytest.raises(r0.BatchM3PhysicalError,match='target must be absent'):
        r0.build_r0_artifact(real_job.job.source,path,repo_root=real_job.code,
                             temp_root=real_job.system/'owned')
    assert path.read_bytes()==b'original evidence'


@pytest.mark.parametrize('suffix',['-wal','-shm','-journal'])
def test_real_r0_refuses_and_preserves_existing_sidecars(real_job,suffix):
    artifact=real_job.job.output/'direct.sqlite'
    sidecar=Path(str(artifact)+suffix);sidecar.write_bytes(b'previous diagnostic')
    with pytest.raises(r0.BatchM3PhysicalError,match='sidecar'):
        r0.build_r0_artifact(real_job.job.source,artifact,repo_root=real_job.code,
                             temp_root=real_job.system/'owned')
    assert sidecar.read_bytes()==b'previous diagnostic'
    assert not artifact.exists()


@pytest.mark.parametrize('suffix',['-wal','-shm','-journal'])
def test_failed_r0_retains_current_staging_and_sidecar(real_job,monkeypatch,suffix):
    original=r0.verify_r0_artifact
    retained=[]
    def fail(path,**kwargs):
        original(path,**kwargs)
        sidecar=Path(str(path)+suffix);sidecar.write_bytes(b'current failure diagnostic')
        retained.extend((Path(path),sidecar))
        raise ValueError('synthetic failure with sidecar')
    monkeypatch.setattr(r0,'verify_r0_artifact',fail)
    with pytest.raises(ValueError,match='synthetic failure with sidecar'):real_job.job.r0()
    assert all(p.is_file() for p in retained)
    assert retained[1].read_bytes()==b'current failure diagnostic'
    assert not real_job.job.path('r0').exists()
    assert not (real_job.job.output/'r0/verified.json').exists()


@pytest.mark.parametrize('mutation',['hash','omission'])
def test_actual_independent_r0_oracle_rejects_tampered_rows(real_job,monkeypatch,mutation):
    real_job.job.r0()
    artifact=real_job.job.path('r0')
    with sqlite3.connect(artifact) as db:
        if mutation=='hash':db.execute("UPDATE physical_occurrence SET value_sha256=? WHERE occurrence_id=1",('0'*64,))
        else:db.execute('DELETE FROM physical_occurrence')
    report=real_job.job.output/'tamper-oracle.json'
    monkeypatch.setattr('sys.argv',['source_archive','--source',str(real_job.job.source),
        '--artifact',str(artifact),'--output',str(report)])
    with pytest.raises(SystemExit,match='occurrence (closure mismatch|shortage)'):
        oracle.main()
    assert not report.exists()
    with pytest.raises(ValueError,match='artifact drift'):real_job.job.dependency_inputs('r0')


@pytest.mark.parametrize('empty', [False, True])
def test_independent_r0_oracle_rejects_entire_source_file_omission(real_job, monkeypatch, empty):
    if empty:
        (real_job.job.source/'TextMap/zzz-empty.json').write_bytes(b'[]')
    real_job.job.r0()
    artifact = real_job.job.path('r0')
    with sqlite3.connect(artifact) as db:
        last = db.execute('SELECT max(source_file_id) FROM source_file').fetchone()[0]
        db.execute('DELETE FROM physical_occurrence WHERE source_file_id=?', (last,))
        db.execute('DELETE FROM source_file WHERE source_file_id=?', (last,))
        # Forged self-reported counts cannot define the source denominator.
        files = db.execute('SELECT count(*) FROM source_file').fetchone()[0]
        records = db.execute('SELECT count(*) FROM physical_occurrence').fetchone()[0]
        for key, count in [('file_denominator', files), ('record_denominator', records),
                           ('record_numerator', records)]:
            db.execute('UPDATE metadata SET value=? WHERE key=?', (json.dumps(count), key))
    output = real_job.job.output/'omission-oracle.json'
    monkeypatch.setattr('sys.argv', ['source_archive', '--source', str(real_job.job.source),
        '--artifact', str(artifact), '--output', str(output)])
    with pytest.raises(SystemExit, match='source file (set|closure) mismatch'):
        oracle.main()
    assert not output.exists()


@pytest.mark.parametrize('mutation', ['artifact', 'sidecar'])
def test_standalone_r0_oracle_rejects_changes_during_verification(real_job, monkeypatch, mutation):
    real_job.job.r0()
    artifact = real_job.job.path('r0')
    original = oracle.scan
    changed = []

    def drift(path, relative):
        expected = original(path, relative)
        if not changed:
            if mutation == 'artifact':
                with artifact.open('ab') as stream:
                    stream.write(b'synthetic drift')
            else:
                Path(str(artifact)+'-journal').write_bytes(b'synthetic journal')
            changed.append(True)
        return expected

    monkeypatch.setattr(oracle, 'scan', drift)
    output = real_job.job.output/'drift-oracle.json'
    monkeypatch.setattr('sys.argv', ['source_archive', '--source', str(real_job.job.source),
        '--artifact', str(artifact), '--output', str(output)])
    with pytest.raises(SystemExit, match='artifact changed|sidecars'):
        oracle.main()
    assert not output.exists()
