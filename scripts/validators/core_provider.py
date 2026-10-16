#!/usr/bin/env python3
"""Independently verify one exact 7.1 M3 family candidate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from genshin_lore.m3_7_1_provider_verify import verify


def main() -> None:
    cli = argparse.ArgumentParser()
    cli.add_argument("--source", type=Path, required=True)
    cli.add_argument("--family", required=True)
    cli.add_argument("--artifact", type=Path, required=True)
    cli.add_argument("--report", type=Path, required=True)
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    result = verify(args.source, args.family, args.artifact, args.report)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            json.dump(result, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
