"""Generate exact M3 input observations for a declared source snapshot.

Only seals and observed integer-field denominators are refreshed. This does
not infer fields, relations, acceptance or cross-snapshot logical identity.
"""
import argparse
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]
from genshin_lore.source_versions import PINS


def prepare(source, version, output):
    source, output = Path(source).resolve(), Path(output)
    commit, tree = PINS[version]
    actual = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
    dirty = subprocess.check_output(['git','-C',str(source),'status','--porcelain','--untracked-files=all'],text=True)
    if actual != [commit, tree] or dirty.strip():
        raise ValueError('clean exact source required')
    template = json.loads((ROOT/'manifests/m3-7.1-provider/input-contract-v1.json').read_text())
    result = copy.deepcopy(template)
    result.update(contract=f'm3-{version}-ten-family-provider-input-v1',
                  source_snapshot=f'anime-game-data:{version}:{commit}', source_tree=tree)
    entries = {}
    for line in subprocess.check_output(['git','-C',str(source),'ls-tree','-rl',commit],text=True).splitlines():
        attrs, path = line.split('\t'); _, kind, blob, size = attrs.split()
        if kind != 'blob':raise ValueError('unsupported source object')
        entries[path] = (blob, int(size))
    def seal(path):
        file = source/path
        if file.is_symlink() or not file.is_file():raise ValueError('source absent/symlink: '+path)
        blob, size = entries[path]
        digest = hashlib.sha256()
        git_blob = hashlib.sha1(f'blob {size}\0'.encode())
        with file.open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):
                digest.update(block);git_blob.update(block)
        if file.stat().st_size != size or git_blob.hexdigest() != blob:raise ValueError('source drift: '+path)
        return {'git_blob_oid':blob,'size_bytes':size,'sha256':digest.hexdigest()}
    for family in result['families']:
        path = family['source_path']
        family['source_identity'] = seal(path)
        # Preserve duplicate object fields and count every consumed occurrence.
        rows = json.loads((source/path).read_bytes(), object_pairs_hook=lambda pairs:pairs)
        if not isinstance(rows,list):raise ValueError('expected physical array')
        expected = set(family['key_observation_fields']) | set(family['v2_text_fields'])
        counts, present = Counter(), Counter()
        for row in rows:
            if not isinstance(row,list) or any(not isinstance(p,tuple) or len(p)!=2 for p in row):
                raise ValueError('physical row must be object')
            fields = set()
            for key,value in row:
                if key in expected:
                    if type(value) is not int:raise ValueError('consumed field is not integer: '+path+'/'+key)
                    counts[key] += 1;fields.add(key)
            present.update(fields)
        if set(counts) != expected:raise ValueError('declared field disappeared: '+path)
        family['source_physical_rows'] = len(rows)
        family['consumed_field_observations'] = {
            key:{'container_mismatches':0,'null_rows':0,'occurrences':counts[key],
                 'present_rows':present[key],'types':{'int':counts[key]}}
            for key in sorted(expected)}
    result['textmap_sources'] = {path:seal(path) for path in template['textmap_sources']}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:
        json.dump(result,stream,ensure_ascii=False,sort_keys=True,indent=2);stream.write('\n')
    return {'status':'SOURCE_CONTRACT_GENERATED_NOT_QUALIFIED','version':version,
            'families':len(result['families']),'snapshot':result['source_snapshot']}


def prepare_special(source, version, output):
    from genshin_lore.source_integrity import pinned_file_seal
    from genshin_lore.batch_m5c_projection import _field_occurrences
    source, output = Path(source).resolve(), Path(output)
    commit, tree = PINS[version]
    pin = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
    dirty = subprocess.check_output(['git','-C',str(source),'status','--porcelain','--untracked-files=all'],text=True)
    if pin != [commit,tree] or dirty.strip():raise ValueError('clean exact source required')
    template_path = ROOT/'manifests/special-core-7.1/input-contract-v1.json'
    result = json.loads(template_path.read_text())
    # Historical tuple samples remain in the immutable old contract. They are
    # not relabelled as observations of the new source.
    for key in ('historical_m5b_report','historical_m5b_sha256','readiness_matrix','readiness_matrix_sha256'):
        result.pop(key,None)
    result.update(contract=f'special-core-{version}-provider-input-v1',source_commit=commit,
        source_tree=tree,source_snapshot=f'anime-game-data:{version}:{commit}',
        status='source_contract_generated_not_qualified',
        inherited_rule_contract={'path':str(template_path.relative_to(ROOT)),
            'sha256':hashlib.sha256(template_path.read_bytes()).hexdigest()})
    rows = {}
    for path,entry in result['source_files'].items():
        seal = pinned_file_seal(source,commit,path)
        entry.update(size_bytes=seal['size_bytes'],sha256=seal['sha256'],git_blob_oid=seal['blob'])
        if not path.startswith('TextMap/'):
            values=json.loads((source/path).read_bytes())
            if not isinstance(values,list) or any(not isinstance(r,dict) for r in values):raise ValueError('physical array required')
            rows[path]=values;entry['physical_rows']=len(values)
    for rule in result['contracts']:
        selected=[row for path in rule['primary_paths'] for row in rows[path]]
        rule['primary_physical_rows']=len(selected)
        selection=rule.get('selected')
        if selection:
            selected=[r for r in selected if r.get(selection['native_field']) in selection['native_values']]
            if Counter(r.get(selection['native_field']) for r in selected)!=Counter(selection['native_values']):
                raise ValueError('declared bounded selection not uniquely present')
        if rule['kind']=='HISTORICAL_SPECIAL':continue
        old_shape=rule['consumed_shape']
        identity={f:sum(f in row for row in selected) for f in rule['identity_fields']}
        if any(n!=len(selected) for n in identity.values()):raise ValueError('identity field disappeared')
        present={f:sum(f in row for row in selected) for f in rule['text_owner_fields']}
        if any(old_shape['text_owner_present'].get(f,0)>0 and n==0 for f,n in present.items()):
            raise ValueError('declared text field disappeared: '+rule['family'])
        rule['consumed_shape']={'selected_rows':len(selected),'identity_field_present':identity,
            'text_owner_present':present,
            'text_owner_types':{f:sorted({type(r[f]).__name__ for r in selected if f in r}) for f in present},
            'relation_slots':{rel['field']:sum(len(_field_occurrences(r,rel['field'])) for r in selected)
                              for rel in rule['relations']}}
        for field, count in old_shape['relation_slots'].items():
            if count and not rule['consumed_shape']['relation_slots'][field]:
                raise ValueError('declared relation field disappeared: '+rule['family']+'/'+field)
        rule['relations']=[{k:v for k,v in rel.items() if k in
            ('field','condition','target_source_file','target_locator_field','evidence_basis')}
            for rel in rule['relations']]
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:
        json.dump(result,stream,ensure_ascii=False,sort_keys=True,indent=2);stream.write('\n')
    return {'status':'SOURCE_CONTRACT_GENERATED_NOT_QUALIFIED','version':version,'contracts':len(result['contracts'])}


def prepare_character(source, version, output):
    from genshin_lore.source_integrity import pinned_file_seal
    from genshin_lore.character_7_1_physical import decode_primary_row
    from genshin_lore.character_7_1_voice import LUT_PATH, decode_fetter_lut, decode_fetter_item
    source, output = Path(source).resolve(), Path(output)
    commit, tree = PINS[version]
    pin = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
    dirty = subprocess.check_output(['git','-C',str(source),'status','--porcelain','--untracked-files=all'],text=True)
    if pin != [commit,tree] or dirty.strip():raise ValueError('clean exact source required')
    result = json.loads((ROOT/'manifests/character-7.1-physical-foundation/source-scope.json').read_text())
    result.update(contract=f'character-{version}-physical-foundation-scope-v1',
        source_commit=commit,source_tree=tree,snapshot_id=f'anime-game-data:{version}:{commit}',
        status='source_contract_generated_not_qualified',scope_basis=[
            'existing bounded Character consumer fields', 'source-authored Fetter LUT and exact Items paths'])
    primary = {}
    for path in result['primary_rows']:
        rows = json.loads((source/path).read_bytes())
        for ordinal,row in enumerate(rows):
            decode_primary_row(path,ordinal,row,json.dumps(row),source_version=version)
        primary[path] = rows
    result['primary_rows'] = {path:len(rows) for path,rows in primary.items()}
    result['primary_row_total'] = sum(result['primary_rows'].values())
    if version == '7.2':
        result['consumed_integer_fields']['AvatarRenameExcelConfigData.json'] = ['relatedAvatarID']
        result['rename_locator_policy'] = 'renameID is absent; relatedAvatarID retained under its own source-authored role, not renamed or unique'
    lut = json.loads((source/LUT_PATH).read_bytes())
    records = [decoded for key,row in lut.items()
               if (decoded := decode_fetter_lut(key,row,source_version=version)) is not None]
    item_paths = sorted({record.item_path for record in records})
    items, audio, missing = [], 0, 0
    for path in item_paths:
        for key,row in json.loads((source/path).read_bytes()).items():
            item, variants = decode_fetter_item(path,key,row,source_version=version)
            items.append(item);audio += len(variants);missing += item.variants_status == 'missing'
    voice_rows = primary['ExcelBinOutput/FettersExcelConfigData.json']
    slots = Counter(record.slot for record in records)
    owner_slots = {row['voiceFile'] for row in voice_rows}
    result['voice_scope'] = {
        'selected_fetter_item_files':item_paths, 'selected_fetter_lut_records':len(records),
        'selected_fetter_item_records':len(items), 'audio_variants':audio,
        'items_with_missing_variant_array':missing, 'fetter_owner_rows':len(voice_rows),
        'unattached_fetter_lut_slots':sum(record.slot not in owner_slots for record in records),
        'attachment_states':dict(Counter('missing' if slots[row['voiceFile']]==0 else
            'unique' if slots[row['voiceFile']]==1 else 'ambiguous' for row in voice_rows)),
        'scope_note':'Fetter-category LUT and exact authored Items paths; other Voice categories excluded',
    }
    paths = sorted(set(primary) | {LUT_PATH} | set(item_paths) |
        {f'TextMap/TextMap{tier}{locale}.json' for tier in ('','_Medium') for locale in ('CHS','EN','JP')})
    old_blobs = {}
    for line in subprocess.check_output(['git','-C',str(source),'ls-tree','-r',PINS['7.1'][0]],text=True).splitlines():
        meta,path=line.split('\t');old_blobs[path]=meta.split()[2]
    result['files']=[]
    deltas=Counter()
    for path in paths:
        seal=pinned_file_seal(source,commit,path)
        delta='added' if path not in old_blobs else 'unchanged' if old_blobs[path]==seal['blob'] else 'modified'
        result['files'].append({'path':path,'size_bytes':seal['size_bytes'],'sha256':seal['sha256'],
                                'blob':seal['blob'],'physical_delta':delta})
        deltas[delta]+=1
    result['physical_delta_counts']=dict(deltas)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:
        json.dump(result,stream,ensure_ascii=False,sort_keys=True,indent=2);stream.write('\n')
    return {'status':'SOURCE_CONTRACT_GENERATED_NOT_QUALIFIED','version':version,
            'primary_rows':result['primary_row_total'],'voice_lut_records':len(records),'audio_variants':audio}


def prepare_narrative(source, version, output):
    from genshin_lore.source_integrity import pinned_file_seal
    source, output = Path(source).resolve(), Path(output)
    commit, tree = PINS[version]
    pin = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
    dirty = subprocess.check_output(['git','-C',str(source),'status','--porcelain','--untracked-files=all'],text=True)
    if pin != [commit,tree] or dirty.strip():raise ValueError('clean exact source required')
    result = json.loads((ROOT/'manifests/narrative-7.1/physical-scope.json').read_text())
    result.update(contract=f'npc-dialog-reminder-{version}-physical-scope-v1',
                  snapshot=f'anime-game-data:{version}:{commit}',source_tree=tree)
    for family,spec in result['families'].items():
        rows = json.loads((source/spec['source_path']).read_bytes())
        types = {}
        for row in rows:
            if type(row.get(spec['local_key'])) is not int:raise ValueError('narrative local key shape')
            for field in spec['text_fields']:
                if type(row.get(field)) is not int:raise ValueError('narrative text field shape')
            for field,value in row.items():
                types.setdefault(field,Counter())[type(value).__name__]+=1
        spec.update(physical_rows=len(rows),field_types=types)
    for entry in result['files']:
        seal=pinned_file_seal(source,commit,entry['path'])
        entry.update(size_bytes=seal['size_bytes'],sha256=seal['sha256'],git_blob=seal['blob'])
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:
        json.dump(result,stream,ensure_ascii=False,sort_keys=True,indent=2);stream.write('\n')
    return {'status':'SOURCE_CONTRACT_GENERATED_NOT_QUALIFIED','version':version,
            'rows':{f:s['physical_rows'] for f,s in result['families'].items()}}


def prepare_quest(source, version, output):
    from genshin_lore.source_integrity import pinned_file_seal
    from genshin_lore.quest_talk_7_1_physical import root_signature_digest
    source, output = Path(source).resolve(), Path(output).resolve()
    commit, tree = PINS[version]
    pin = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
    dirty = subprocess.check_output(['git','-C',str(source),'status','--porcelain','--untracked-files=all'],text=True)
    if pin != [commit,tree] or dirty.strip():raise ValueError('clean exact source required')
    snapshot=f'anime-game-data:{version}:{commit}'
    inventory = subprocess.check_output(['git','-C',str(source),'ls-tree','-r','--name-only',commit,'--','BinOutput/Quest','BinOutput/Talk'],text=True).splitlines()
    signature = {'contract':f'quest-talk-{version}-observed-root-signatures-v1',
                 'source_commit':commit,'source_tree':tree}
    physical = {}
    for kind in ('quest','talk'):
        prefix='BinOutput/'+kind.capitalize()+'/'
        paths=sorted(path for path in inventory if path.startswith(prefix) and path.endswith('.json'))
        counts=Counter(root_signature_digest(json.loads((source/path).read_bytes())) for path in paths)
        signature[kind]={'file_count':len(paths),'signature_digests':sorted(counts),
                         'signature_counts':dict(sorted(counts.items()))}
        physical[kind]={'source_root':prefix.rstrip('/'),'file_count':len(paths),
                        'path_set_sha256':hashlib.sha256((json.dumps(paths,ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()).hexdigest()}
    signature_path=output.with_name('root-signature-digests.json')
    output.parent.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(signature,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
    with signature_path.open('xb') as stream:stream.write(raw)
    main='ExcelBinOutput/MainQuestExcelConfigData.json'
    main_seal=pinned_file_seal(source,commit,main)
    shards=[]
    for locale in ('CHS','EN','JP'):
        for suffix,tier in (('','main'),('_Medium','medium')):
            path=f'TextMap/TextMap{suffix}{locale}.json'
            seal=pinned_file_seal(source,commit,path)
            shards.append({'path':path,'size_bytes':seal['size_bytes'],'sha256':seal['sha256'],
                           'locale':locale,'tier':tier,'blob':seal['blob']})
    result={'contract':f'quest-talk-{version}-standalone-physical-v1',
            'status':'source_contract_generated_not_qualified','product_accepted':False,
            'source_commit':commit,'source_tree':tree,'snapshot_id':snapshot,
            'physical_scope':physical,'main_quest_source':{
                'path':main,'size_bytes':main_seal['size_bytes'],'sha256':main_seal['sha256'],
                'physical_rows':len(json.loads((source/main).read_bytes()))},
            'textmap_shards':shards,'root_signature_manifest':{
                'path':str(signature_path.relative_to(ROOT)),
                'sha256':hashlib.sha256(raw).hexdigest(),
                **{kind+'_distinct_signatures':len(signature[kind]['signature_digests']) for kind in ('quest','talk')}},
            'decoder_contract':{'version':f'quest-talk-{version}-physical-v1',
                'source_authored_fields':['id','subQuests','talks','initDialog','dialogList','talkId','nextDialogs','talkContentTextMapHash'],
                'identity_policy':'physical coordinates only; unresolved registered shapes remain partial'}}
    with output.open('x') as stream:
        json.dump(result,stream,ensure_ascii=False,sort_keys=True,indent=2);stream.write('\n')
    return {'status':'SOURCE_CONTRACT_GENERATED_NOT_QUALIFIED','version':version,
            'files':{kind:physical[kind]['file_count'] for kind in physical}}


def prepare_text_scope(source, version, output, kind):
    from genshin_lore.source_integrity import pinned_file_seal
    source, output = Path(source).resolve(), Path(output).resolve()
    commit, tree = PINS[version]
    pin = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()
    dirty = subprocess.check_output(['git','-C',str(source),'status','--porcelain','--untracked-files=all'],text=True)
    if pin != [commit,tree] or dirty.strip():raise ValueError('clean exact source required')
    template, contract = {
        'document':('document-readable-7.1/source-scope.json', f'document-readable-{version}-source-scope-v1'),
        'gcg':('gcg-card-7.1/scope.json',f'gcg-card-source-{version}-v1'),
        'loading':('loading-tips-7.1/scope.json',f'loading-tips-{version}-source-scope-v1'),
        'mail':('mail-archive-7.1/source-scope-v1.json',f'mail-{version}-exact-source-scope-v1'),
    }[kind]
    result=json.loads((ROOT/'manifests'/template).read_text())
    result.update(contract=contract,source_commit=commit,source_tree=tree,
                  status='source_contract_generated_not_qualified',product_accepted=False)
    result['source_snapshot' if kind=='mail' else 'snapshot_id']=f'anime-game-data:{version}:{commit}'
    files=result['document_sources' if kind=='document' else 'files']
    physical={}
    for entry in files:
        seal=pinned_file_seal(source,commit,entry['path'])
        entry.update(size_bytes=seal['size_bytes'],sha256=seal['sha256'],blob=seal['blob'])
        if not entry['path'].startswith('TextMap/'):
            physical[entry['path']]=json.loads((source/entry['path']).read_bytes())
    if kind=='document':
        result['document_source_row_counts']={path:len(rows) for path,rows in physical.items()}
        result['document_primary_rows']=len(physical['ExcelBinOutput/DocumentExcelConfigData.json'])
        paths={}
        for line in subprocess.check_output(['git','-C',str(source),'ls-tree','-r',commit,'--','Readable'],text=True).splitlines():
            meta,path=line.split('\t');paths[path]=meta.split()[2]
        result['readable']={'file_count':len(paths),'path_blob_set_sha256':hashlib.sha256(
            (json.dumps(sorted(paths.items()),ensure_ascii=False,sort_keys=True,separators=(',',':'))+'\n').encode()).hexdigest()}
        result.pop('locator_fields_7_1',None);result.pop('old_chs_locator_fields_absent',None)
        result['locator_fields']=['BFDDHOKGBOH','OKIHBKFCDND','enPath','jpPath'] if version=='7.2' else ['MBOPLLJEHCJ','FCNGPFLCLFA','enPath','jpPath']
        result['r0_dependency']={'ingest_run_id':None,'sha256':None,'size_bytes':None,'status':'unbound_until_fresh_r0_qualification'}
        result['opaque_document_array_policy']=(
            'PLOJOMIPJGH and questIDList are physical Localization candidates only; exported Readable paths may be delivered as candidates, never confirmed title/file ownership'
            if version == '7.2' else 'contentLocalizedId is an equality observation; its namespace remains unresolved')
    elif kind=='gcg':
        result['primary_rows']=[len(physical['ExcelBinOutput/'+name+'ExcelConfigData.json']) for name in ('GCGDeckCard','GCGCardFace')]
        result['corpus_complete']='two declared primary files and six pinned TextMaps only'
    elif kind=='loading':
        result['primary_rows']=len(physical['ExcelBinOutput/LoadingTipsExcelConfigData.json'])
    else:
        result['row_denominator']={name:len(physical[path]) for name,path in zip(
            ('mail_template','birthday_schedule','alchemy_sim_letter'),result['primary_sources'])}
        path='ExcelBinOutput/AlchemySimLetterExcelConfigData.json'
        previous=json.loads(subprocess.check_output(['git','-C',str(source),'show',PINS['7.1'][0]+':'+path]))
        current=physical[path]
        old_by={r['id']:r for r in previous};new_by={r['id']:r for r in current}
        if len(old_by)!=len(previous) or len(new_by)!=len(current) or set(old_by)!=set(new_by):
            raise ValueError('Alchemy physical locator comparison boundary changed')
        candidates=set.intersection(*(set(row) for row in current))-{'id','contentTextMapHash'}
        matches=[field for field in candidates if all(type(new_by[key][field]) is int and
                 new_by[key][field]==old_by[key]['AICANPKILKN'] for key in old_by)]
        if len(matches)!=1:raise ValueError('second raw Alchemy locator observation has no unique full physical correspondence')
        result['text_fields_by_source']['alchemy_sim_letter']=['contentTextMapHash',matches[0]]
        result['field_continuity']={'old_source_commit':PINS['7.1'][0],'new_source_commit':commit,
            'old_field':'AICANPKILKN','new_field':matches[0],'physical_rows_compared':len(previous),
            'unique_full_raw_value_correspondence':True,'semantic_role_inferred':False,
            'logical_identity_created':False,'limitation':'physical locator alignment is not cross-snapshot logical identity or a title/body role'}
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:
        json.dump(result,stream,ensure_ascii=False,sort_keys=True,indent=2);stream.write('\n')
    return {'status':'SOURCE_CONTRACT_GENERATED_NOT_QUALIFIED','version':version,'scope':kind}


def prepare_subtitle(source, version, output):
    from genshin_lore.direct_text_7_1_source import inventory
    from genshin_lore.source_integrity import pinned_file_seal
    source, output = Path(source).resolve(), Path(output)
    commit, tree = PINS[version]
    if subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD','HEAD^{tree}'],text=True).splitlines()!=[commit,tree]:
        raise ValueError('Subtitle exact source required')
    result=json.loads((ROOT/'manifests/direct-text-7.1/subtitle-scope.json').read_text())
    result.update(snapshot_id=f'anime-game-data:{version}:{commit}', source_commit=commit, source_tree=tree,
                  completeness=f'all pinned {version} Subtitle physical files; not full product')
    result['files']=inventory(source,'Subtitle',commit)
    result['file_denominator']=len(result['files'])
    for entry in result['lookup_sources']:
        item=pinned_file_seal(source,commit,entry['path'])
        entry.update(blob=item['blob'],size_bytes=item['size_bytes'],sha256=item['sha256'])
    result['r0_dependency'].update(run=None,artifact_size_bytes=None,artifact_sha256=None)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x') as stream:
        json.dump(result,stream,ensure_ascii=False,sort_keys=True,indent=2);stream.write('\n')
    return {'status':'SOURCE_CONTRACT_GENERATED_NOT_QUALIFIED','version':version,'files':result['file_denominator']}


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--version',choices=sorted(PINS),required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--scope',choices=['m3','special','character','narrative','quest','document','gcg','loading','mail','subtitle'],default='m3')
    a=p.parse_args()
    fn={'m3':prepare,'special':prepare_special,'character':prepare_character,'narrative':prepare_narrative,'quest':prepare_quest,'subtitle':prepare_subtitle}.get(a.scope)
    if fn is None:
        fn=lambda source,version,output:prepare_text_scope(source,version,output,a.scope)
    print(json.dumps(fn(a.source,a.version,a.output),sort_keys=True))
