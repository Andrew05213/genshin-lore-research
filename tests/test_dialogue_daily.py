import json
from scripts.dialogue_daily import settings, capture_wrapper


def fixture(tmp_path):
    (tmp_path/'.codex').mkdir()
    root=tmp_path/'code'; (root/'scripts').mkdir(parents=True)
    (root/'scripts/serve_dialogue.py').touch()
    (tmp_path/'.codex/research-daily.json').write_text(json.dumps({'dialogue_runtime_root':str(root)}))
    config=tmp_path/'.codex/research71-release.json'
    config.write_text(json.dumps({'profile':str(tmp_path/'release/manifests/research/profile.json')}))
    return config


def test_replaces_only_research_command_preserving_citation_ledger(tmp_path):
    config=fixture(tmp_path)
    value=settings(tmp_path,config,['mcp_servers.genshin-lore-research71.args=["old"]',
        'mcp_servers.genshin-lore-research71.env.GENSHIN_CITATION_HANDOFF_DIR="private"',
        'mcp_servers.genshin-lore-research71-preview.args=["unchanged"]'])
    assert value[0].endswith('="private"')
    assert 'mcp_servers.genshin-lore-research71-preview.args=["unchanged"]' in value
    assert sum(s.startswith('mcp_servers.genshin-lore-research71.args=') for s in value)==1
    assert any('--released-runtime-root' in s for s in value)


def test_capture_preserves_question_and_resume_parameters(tmp_path):
    config=fixture(tmp_path); seen={}
    def base(*args,**kwargs):seen.update(args=args,kwargs=kwargs);return 'original'
    f=capture_wrapper(base,tmp_path,config)
    assert f('natural question',tmp_path,'model','medium',240,[],resume_thread_id='thread')=='original'
    assert seen['args'][0]=='natural question'
    assert seen['kwargs']=={'resume_thread_id':'thread'}


def test_disabled_handoff_preserves_existing_settings(tmp_path):
    assert settings(tmp_path,None,['original'])==['original']
