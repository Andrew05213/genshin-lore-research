"""Stable JSON CLI for the standalone Phase 3.1C3 Talk comparison pilot."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from .talk_comparison_retrieval import (
    TalkComparisonNotFound,
    TalkComparisonRepository,
    TalkComparisonRepositoryError,
)


class _JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        _write({"error": {"kind": "ArgumentError", "message": message}}, sys.stderr)
        raise SystemExit(2)


def _parser() -> argparse.ArgumentParser:
    parser = _JsonArgumentParser(prog="genshin-lore-talk-comparison")
    parser.add_argument("--db", required=True, help="explicit standalone Schema 7 pilot database")
    parser.add_argument("--release-report", required=True,
                        help="explicit tracked Phase31C3B release report; never sibling-discovered")
    parser.add_argument("--compare-run-id", required=True, help="exact eligible compare run")
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("summary")
    listing = sub.add_parser("list-match-sets")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--cursor")
    get = sub.add_parser("get-match-set")
    get.add_argument("--match-set-id", required=True)
    return parser


def _write(value: Any, stream: Any = sys.stdout) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True,
                            separators=(",", ":")) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with TalkComparisonRepository(args.db, args.release_report,
                                      args.compare_run_id) as repository:
            if args.operation == "summary":
                result = repository.summary()
            elif args.operation == "list-match-sets":
                result = repository.list_match_sets(limit=args.limit, cursor=args.cursor)
            else:
                try:
                    result = repository.get_match_set(args.match_set_id)
                except TalkComparisonNotFound:
                    result = {
                        "family": "talk_comparison_pilot",
                        "family_contract_version": "talk-comparison-pilot-v1",
                        "compare_run_id": args.compare_run_id,
                        "evidence_only": True,
                        "coverage": "bounded_comparison_pilot",
                        "corpus_complete": False,
                        "data": {"status": "NotFound", "match_set_id": args.match_set_id},
                    }
        _write(result)
        return 0
    except (TalkComparisonRepositoryError, ValueError, OSError) as exc:
        _write({"error": {"kind": type(exc).__name__, "message": str(exc)}}, sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
