"""Real Native26 source builder and full oracle, using synthetic bytes only."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import pytest

from genshin_lore import event_7_1_source as source_checks
from genshin_lore import native26_7_1_physical as builder
from genshin_lore import native26_7_1_verify as verifier


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')


def seal(path):
    return {'size_bytes': path.stat().st_size, 'sha256': verifier.sha(path)}


@pytest.fixture(scope='module')
def native26(tmp_path_factory):
    base = tmp_path_factory.mktemp('native26') / '中文 空格 # 100%'
    source, code, metadata = base / 'source', base / 'code', base / 'metadata'
    original = Path(builder.__file__).resolve()
    original_schema = builder.SCHEMA
    copied_code = code / 'src/genshin_lore/native26_7_1_physical.py'
    copied_code.parent.mkdir(parents=True)
    shutil.copyfile(original, copied_code)
    schema = code / 'schemas/native21-7.1-projection-v1.sql'
    schema.parent.mkdir()
    shutil.copyfile(original_schema, schema)
    history = code / 'manifests/native-archive/high-value-open-selection.json'
    event_relative = Path('manifests/event-narrative-7.1/component-16-physical-runtime-v1.json')
    talk_relative = Path('manifests/quest-talk-7.1/standalone-candidate.json')
    talk_source = 'ExcelBinOutput/TalkExcelConfigData_1.json'
    first = [{'id': i + 1, 'text': 7, 'link': 1} for i in range(44)]
    first[-1]['text'] = 8  # Legitimate missing text stays missing, not guessed.
    paths = [talk_source, 'ExcelBinOutput/synthetic-bulk.json'] + [f'ExcelBinOutput/empty-{i}.json' for i in range(89)]
    declarations = []
    for i, path in enumerate(paths):
        rows = first if i == 0 else ([{}] * (107323 - 44) if i == 1 else [])
        write(source / path, rows)
        declarations.append({'path': path, 'boundary': 'synthetic', 'kind': 'array',
            'native_id_field': 'id' if i == 0 else None,
            'text_owners': [{'field': 'text', 'shape': 'scalar'}] if i == 0 else [],
            'relations': [{'field': 'link', 'shape': 'scalar', 'resolution': 'local',
                          'relation_kind': 'synthetic_link', 'target_sources': [talk_source],
                          'target_key': 'id'}] if i == 0 else [], 'field_classification': {}})
    maps = {locale: {tier: f'TextMap/{locale}-{tier}.json' for tier in ('main','medium')}
            for locale in ('CHS','EN','JP')}
    for locale in maps:
        for tier, path in maps[locale].items():
            write(source / path, {'7': 'synthetic body'} if tier == 'main' else {})
    write(history, {'sources': declarations, 'languages': list(maps), 'textmaps': maps})
    event_db, talk_db = base / 'event.sqlite', base / 'talk.sqlite'
    reuse = []
    with sqlite3.connect(event_db) as db:
        db.executescript('CREATE TABLE release(id INTEGER PRIMARY KEY,manifest_json TEXT);'
            'CREATE TABLE selected_occurrence(id TEXT,source_path TEXT,pointer TEXT,ordinal INTEGER,raw_sha256 TEXT,source_kind TEXT);')
        db.execute('INSERT INTO release VALUES(1,?)', (json.dumps({'contract':'synthetic-event'}),))
        for ordinal, row, raw in source_checks.array_records(source / talk_source):
            digest = hashlib.sha256(raw.encode()).hexdigest()
            ref = f'synthetic-event:{ordinal}'
            db.execute('INSERT INTO selected_occurrence VALUES(?,?,?,?,?,?)',
                       (ref,talk_source,f'/{ordinal}',ordinal,digest,'talk_excel_support'))
            reuse.append({'source_pointer':f'/{ordinal}','target_occurrence_id':ref,
                          'source_path':talk_source,'ordinal':ordinal,'raw_sha256':digest})
    with sqlite3.connect(talk_db) as db:
        db.execute('CREATE TABLE physical_container(id TEXT,source_path TEXT,pointer TEXT,native_id TEXT,raw_sha256 TEXT,kind TEXT,ingest_run_id TEXT)')
    event = {'artifact':{**seal(event_db),'path_machine_local':str(event_db)},
             'parser_build_id':'synthetic-event-parser','ingest_run_id':'synthetic-event-run'}
    talk = {'artifact':seal(talk_db),'local_artifact_path':str(talk_db),
            'parser_build_id':'synthetic-talk-parser','ingest_run_id':'synthetic-talk-run','contract':'synthetic-talk'}
    write(metadata / event_relative, event)
    write(metadata / talk_relative, talk)
    current = []
    for i, declaration in enumerate(declarations):
        current.append({**declaration,
            'text_owners':[{**o,'owner_role_status':'mapping_unresolved'} for o in declaration['text_owners']],
            'historical_field_classification':declaration['field_classification'],
            'source_row_count':44 if i == 0 else 107323 - 44 if i == 1 else 0,
            'owner_root_presence':{'text':44} if i == 0 else {}})
    files = paths + [path for locale in maps.values() for path in locale.values()]
    scope = {'contract':'native26-7.1-physical-projection-scope-v1',
        'source_commit':source_checks.COMMIT,'source_tree':source_checks.TREE,'snapshot_id':source_checks.SNAPSHOT,
        'historical_selection_sha256':verifier.sha(history),'sources':current,
        'files':[{'path':path,**seal(source/path),'blob':source_checks.git_blob(source/path)} for path in files],
        'languages':list(maps),'textmaps':maps,'raw_physical_row_denominator':107323,
        'semantic_owner_mapping_unresolved':True,'same_named_historical_owner_roles_carried_as_raw_locators_only':True,
        'missing_historical_owner_specs':[], 'event16_manifest_sha256':verifier.sha(metadata/event_relative),
        'event16_artifact':event['artifact'],'event16_parser':event['parser_build_id'],
        'event16_run':event['ingest_run_id'],'event16_contract':'synthetic-event','event16_reuse_targets':reuse}
    scope_path = base / 'scope.json'
    write(scope_path, scope)
    patch = pytest.MonkeyPatch()
    for module, names in ((builder, {'REPO':code,'SCHEMA':schema,'HISTORY':history,
              'EVENT16':code/event_relative,'QUEST_TALK':code/talk_relative,'__file__':str(copied_code)}),
            (verifier, {'ROOT':code,'SCHEMA':schema,'HISTORY':history,
              'EVENT':code/event_relative,'TALK':code/talk_relative})):
        for name,value in names.items():
            patch.setattr(module,name,value)

    def pin(argv, **kwargs):
        assert argv[:3] == ['git','-C',str(source.resolve())]
        if argv[3:] == ['status','--porcelain']:
            return ''
        assert argv[3] == 'rev-parse'
        ref = argv[4]
        if ref == 'HEAD':
            return source_checks.COMMIT
        if ref == 'HEAD^{tree}':
            return source_checks.TREE
        assert ref.startswith(source_checks.COMMIT + ':')
        return source_checks.git_blob(source / ref.split(':',1)[1])

    # Synthetic JSON cannot have the upstream game's Git identity. Only these
    # replies are replaced: byte seals, parsing, source denominators, SQL,
    # publication, full rowset oracle and mutation guards execute unchanged.
    patch.setattr(source_checks,'subprocess',SimpleNamespace(check_output=pin))
    artifact = base / 'original.sqlite'
    try:
        report = builder.build(source,scope_path,artifact,metadata_root=metadata)
        yield SimpleNamespace(base=base,source=source,scope=scope_path,metadata=metadata,
                              artifact=artifact,report=report)
    finally:
        patch.undo()


def check(case, path):
    return verifier.verify(case.source,case.scope,path,metadata_root=case.metadata)


@pytest.mark.parametrize('filename',['artifact.sqlite','中文 空格.sqlite','symbols # 100%.sqlite'])
def test_real_native26_full_oracle_and_mutations_on_native_paths(native26, tmp_path, filename):
    path = tmp_path / filename
    shutil.copyfile(native26.artifact,path)
    before = seal(path)
    result = check(native26,path)
    assert result['status'] == 'PASS' and result['source_to_sql_rowset_exact'] is True
    assert result['schema_exact'] is True and result['mutation_cases'] == 70
    assert result['counts']['row_occurrence'] == 107323
    assert result['counts']['relation_candidate'] == 44
    assert result['counts']['text_candidate'] == 129
    assert result['text_states']['missing'] == 3
    assert seal(path) == before
    assert not any(Path(str(path)+suffix).exists() for suffix in ('-wal','-shm','-journal'))


@pytest.mark.parametrize('mutation',['body','text_candidate','relation','relation_candidate','parent_identity','foreign_key','schema'])
def test_real_native26_oracle_rejects_tampering_without_losing_guards(native26,tmp_path,mutation):
    path = tmp_path / '篡改 # 100%.sqlite'
    shutil.copyfile(native26.artifact,path)
    changes = {
        'body':('text_candidate','text_update',"UPDATE text_candidate SET raw_text='synthetic forged'"),
        'text_candidate':('text_candidate','text_delete','DELETE FROM text_candidate'),
        'relation':('relation_locator','locator_update',"UPDATE relation_locator SET status='mapping_unresolved',candidate_count=0"),
        'relation_candidate':('relation_candidate','relation_delete','DELETE FROM relation_candidate'),
        'parent_identity':('row_occurrence','row_update',"UPDATE row_occurrence SET source_path='synthetic foreign scope' WHERE id=(SELECT id FROM row_occurrence LIMIT 1)"),
        'foreign_key':('relation_candidate','relation_update',"UPDATE relation_candidate SET locator_id='synthetic absent locator' WHERE id=(SELECT id FROM relation_candidate LIMIT 1)"),
    }
    with sqlite3.connect(path) as db:
        if mutation == 'schema':
            db.execute('DROP TRIGGER row_update')
        else:
            _, trigger, statement = changes[mutation]
            definition = db.execute('SELECT sql FROM sqlite_master WHERE name=?',(trigger,)).fetchone()[0]
            db.execute('DROP TRIGGER '+trigger)
            db.execute(statement)
            db.execute(definition)
    before = seal(path)  # A recomputed artifact seal cannot excuse wrong rows.
    with pytest.raises(ValueError,match='rowset|schema/index/trigger|integrity/FK'):
        check(native26,path)
    assert seal(path) == before


@pytest.mark.parametrize('suffix',['-wal','-shm','-journal'])
def test_real_native26_oracle_preserves_and_refuses_sidecars(native26,tmp_path,suffix):
    path = tmp_path / 'sidecar.sqlite'
    shutil.copyfile(native26.artifact,path)
    sidecar = Path(str(path)+suffix)
    sidecar.write_bytes(b'synthetic retained failure')
    before = seal(path)
    with pytest.raises(ValueError,match='SQLite sidecar'):
        check(native26,path)
    assert sidecar.read_bytes() == b'synthetic retained failure'
    assert seal(path) == before


def test_real_native26_oracle_refuses_missing_database_without_creating_it(native26,tmp_path):
    path = tmp_path / 'missing # 100%.sqlite'
    with pytest.raises(sqlite3.OperationalError,match='unable to open'):
        check(native26,path)
    assert not path.exists()


def test_real_native26_oracle_rejects_source_seal_drift(native26):
    path = native26.source / 'TextMap/CHS-main.json'
    original = path.read_bytes()
    before = seal(native26.artifact)
    try:
        path.write_bytes(b'{"7":"synthetic forged source"}')
        with pytest.raises(ValueError,match='source bytes mismatch'):
            check(native26,native26.artifact)
    finally:
        path.write_bytes(original)
    assert seal(native26.artifact) == before


def test_real_native26_oracle_rejects_reduced_source_denominator(native26,tmp_path):
    value = json.loads(native26.scope.read_text())
    value['raw_physical_row_denominator'] -= 1
    path = tmp_path / 'reduced-scope.json'
    write(path,value)
    with pytest.raises(ValueError,match='source denominator'):
        verifier.verify(native26.source,path,native26.artifact,metadata_root=native26.metadata)
