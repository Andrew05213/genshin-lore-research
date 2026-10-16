import argparse
import json
from pathlib import Path

from genshin_lore.event16_18_7_1_selection_verify import verify

ap = argparse.ArgumentParser()
ap.add_argument('--source-root', required=True, type=Path)
ap.add_argument('--scope', required=True, type=Path)
ap.add_argument('--artifact', required=True, type=Path)
args = ap.parse_args()
print(json.dumps(verify(args.source_root, args.scope, args.artifact), sort_keys=True))
