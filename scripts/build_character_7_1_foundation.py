"""Build a fresh, no-clobber 7.1 Character physical foundation candidate."""
import argparse
import json

from genshin_lore.character_7_1_foundation_build import build


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.source, args.scope, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
