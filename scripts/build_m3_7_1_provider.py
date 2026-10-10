#!/usr/bin/env python3
"""Build one fresh family-local 7.1 M3 provider candidate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from genshin_lore.m3_7_1_provider_build import build


def main() -> None:
    cli = argparse.ArgumentParser()
    cli.add_argument("--source", type=Path, required=True)
    cli.add_argument("--family", required=True)
    cli.add_argument("--output", type=Path, required=True)
    cli.add_argument("--report", type=Path, required=True)
    args = cli.parse_args()
    if args.report.exists():
        raise FileExistsError(args.report)
    report = build(args.source, args.family, args.output)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"family": args.family, "counts": report["counts"],
                      "artifact": report["artifact"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
