"""Independently verify a fixed-source Character foundation artifact."""
import argparse
import json

from genshin_lore.character_7_1_foundation_verify import verify


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--artifact", required=True)
    parser.add_argument("--size-bytes", required=True, type=int)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.source, args.scope, args.artifact,
                            expected_artifact={"size_bytes": args.size_bytes, "sha256": args.sha256}),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
