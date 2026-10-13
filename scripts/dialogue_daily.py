"""Project-local command handoff; no retrieval, rendering or source fallback."""
import json
from pathlib import Path

SERVER = 'genshin-lore-research71'


def settings(project, config_path, overrides):
    project = Path(project)
    daily_path = project/'.codex/research-daily.json'
    daily = json.loads(daily_path.read_text()) if daily_path.is_file() else {}
    root = daily.get('dialogue_runtime_root')
    if not root:
        return list(overrides)
    root = Path(root)
    config = Path(config_path or project/'.codex/research71-release.json')
    if not root.is_absolute() or not (root/'scripts/dialogue_runtime.py').is_file():
        raise ValueError('Configured dialogue runtime unavailable; no fallback')
    release = json.loads(config.read_text())
    retained = Path(release['profile']).resolve().parents[2]
    args = [str(root/'scripts/dialogue_runtime.py'), '--config', str(config.resolve()),
            '--released-runtime-root', str(retained)]
    prefixes = (f'mcp_servers.{SERVER}.args=', f'mcp_servers.{SERVER}.cwd=',
                f'mcp_servers.{SERVER}.required=')
    result = [s for s in overrides if not s.startswith(prefixes)]
    return result + [f'mcp_servers.{SERVER}.args='+json.dumps(args),
                     f'mcp_servers.{SERVER}.cwd='+json.dumps(str(root)),
                     f'mcp_servers.{SERVER}.required=true']


def capture_wrapper(capture_fn, project, config_path):
    def capture(question, cwd, model, effort, timeout, overrides, event_log_path=None, **kwargs):
        return capture_fn(question, cwd, model, effort, timeout,
                          settings(project, config_path, overrides), event_log_path, **kwargs)
    return capture
