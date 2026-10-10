import argparse
import json
from pathlib import Path
from genshin_lore.narrative_7_1_verify import verify

if __name__ == "__main__":
    p = argparse.ArgumentParser(); p.add_argument("--source", required=True); p.add_argument("--artifact", required=True)
    p.add_argument("--build-report", required=True); p.add_argument("--output", required=True)
    a = p.parse_args()
    r = verify(a.source, a.artifact, json.loads(Path(a.build_report).read_text()))
    with Path(a.output).open("x") as f:
        json.dump(r, f, ensure_ascii=False, indent=2, sort_keys=True); f.write("\n")
