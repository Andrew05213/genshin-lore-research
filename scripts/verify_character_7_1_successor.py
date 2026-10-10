"""Independent full-row 7.1 Character successor verifier CLI."""
import argparse
import json

from genshin_lore.character_7_1_successor_verify import verify


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("text", "lore", "voice"))
    parser.add_argument("--foundation", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--source")
    parser.add_argument("--text")
    parser.add_argument("--size-bytes", type=int)
    parser.add_argument("--sha256")
    args = parser.parse_args()
    if (args.size_bytes is None) != (args.sha256 is None):
        parser.error("size and SHA must be supplied together")
    print(json.dumps(verify(args.kind, args.foundation, args.artifact, source_root=args.source, text_path=args.text, expected_artifact={"size_bytes": args.size_bytes, "sha256": args.sha256} if args.sha256 else None), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
