"""Explicit, no-fallback standalone 7.1 Character successor builder."""
import argparse
import json

from genshin_lore.character_7_1_successor_build import build_lore, build_text, build_voice


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("text", "lore", "voice"))
    parser.add_argument("--foundation", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--source")
    parser.add_argument("--text")
    args = parser.parse_args()
    if args.kind == "text":
        if not args.source or args.text:
            parser.error("text requires --source and forbids --text")
        result = build_text(args.source, args.foundation, args.output)
    else:
        if not args.text or args.source:
            parser.error("lore/voice require --text and forbid --source")
        result = (build_lore if args.kind == "lore" else build_voice)(args.foundation, args.text, args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
