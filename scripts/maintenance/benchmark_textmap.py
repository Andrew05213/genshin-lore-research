"""One fresh, fully verified three-component run for a serial A/B comparison."""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'src'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output-root', type=Path, required=True)
    parser.add_argument('--cache-mib', type=int, choices=(0, 1024), required=True)
    args = parser.parse_args()
    from genshin_lore.m3_7_1_provider_build import build
    from genshin_lore.m3_7_1_provider_verify import verify
    from genshin_lore.textmap_input_cache import textmap_session
    args.output_root.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    rows = []
    with textmap_session(args.cache_mib) as stats:
        for family in ('activity_archive', 'avatar_config_archive', 'achievement_archive'):
            stage = args.output_root/family
            stage.mkdir()
            t = time.perf_counter()
            report = build(args.source, family, stage/'artifact.sqlite')
            (stage/'build.json').write_text(json.dumps(report), encoding='utf-8')
            built = time.perf_counter()
            checked = verify(args.source, family, stage/'artifact.sqlite', stage/'build.json')
            (stage/'verify.json').write_text(json.dumps(checked), encoding='utf-8')
            rows.append({'family': family, 'build_s': built-t,
                         'verify_s': time.perf_counter()-built, 'verification': checked})
        result = {'cache_mib': args.cache_mib, 'total_s': time.perf_counter()-started,
                  'rows': rows, 'cache_stats': {k:v for k,v in stats.items() if k != 'readers'}}
    (args.output_root/'summary.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({'total_s': result['total_s'], 'components': len(rows)}))


if __name__ == '__main__':
    main()
