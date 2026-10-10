from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .util import canonical_json, digest_id, normalize_text, sha256_text


ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "migrations"

# ``Database.initialize()`` is the long-standing generic Schema 10 entrypoint.
# Family-specific work that opts into a newer migration must name that ceiling
# explicitly with ``initialize_through(11)``, ``initialize_through(12)``, or
# ``initialize_through(13)``/``initialize_through(14)``.
DEFAULT_INITIALIZE_CEILING = 10
_MIGRATION_RAW_SHA256 = {
    "0020_batch_r4_wave01_text_projection.sql":
        "5924df5bb34253da2ef061a16c0de3b4921d96bf430ec041863b96de5801e356",
    "0011_phase4d2a_tutorial_canary_evidence.sql":
        "d852e4646c29d9844d43de1f7e069a9fb4df449182343925acd8147cc7395a91",
    "0012_phase4e2_world_archive_canary_evidence.sql":
        "b722dc2ca288c20cd65a784e60adff7b95f8417d55f17e9b14c094760f4f5717",
    "0013_phase4f2_quest_codex_canary_evidence.sql":
        "605b91f96b76b446774c4036a7438e565b1d9ee7152a6137398605b3c36011b6",
    "0014_phase4g2_avatar_codex_canary_evidence.sql":
        "abc905d8fcb1d1e1507fa7a1cd4447748bcf42d5dc06454a1aaa1f62566f1e48",
    "0015_batch_projection_foundation.sql":
        "be90d86a5363e7af99fed5ebc5780775b9481c3cf6bf58a4acf25fe237b6a18e",
    "0016_batch_wave01_family_projection.sql":
        "a4566887f3a1773486f8725ca2efbf58a7ea0a1efbd41ca49fba92608509d5db",
    "0017_batch_wave02_family_projection.sql":
        "ec8e41402b0c07b5b94aece97607a066c452de12d65c2ee0c49f486df9474c36",
    "0018_batch_wave02_write_once_correction.sql":
        "685e04f15de3cfc7773f4f17ca52367f723a096b243245773ffd7f8420bad22a",
    "0019_batch_r1_wave01_text_projection.sql":
        "05a36070107dcbb04859cdf4278fe1744712fb4e8e7a43d2ed8a7435d25bf16f",
}

_SCHEMA_STRUCTURAL_SIGNATURES = {
    # Canonical digests over object names, PRAGMA structural metadata, and
    # normalized view/index/trigger/virtual-table semantics.  Unlike raw
    # sqlite_master.sql, these seals do not depend on SQLite's DDL formatting.
    1: {"fts5": "068ee62ca8b44c59f897efe22eaec66a48fba97d9ab6e582036cef59bf7e045d", "fallback": "281bbf973909308c0c7911048b685a904c4de8e00477676f2b52160d8b4e5394"},
    2: {"fts5": "cf6a5e13876f21328ee23a2a7d3a5944861d5918b58b99f00cb696f1a6e23a3a", "fallback": "03a8d77e99459a91c469210ec8ea8ef99cb0e9fc0447f6042fa46836550273ba"},
    3: {"fts5": "619c241b3de21b4c77149c9066eb749de8b6b17c67c9ad120b48fed75e6a7152", "fallback": "e7d02cc9460271d8aa809429a569f33ba70f9790a7085a7966471e006d339783"},
    4: {"fts5": "0a85cbe5e1303c741868d15c7a59f54ed9c05f250dac96c4cb41ab718d063d80", "fallback": "c85f146347acdd4951e504de4d6a90f44aac3d01430db3c4a9afe0cd0909112c"},
    5: {"fts5": "8363504e85457a0e8b198f1196a7e22c47846a8ec3da4dad49f122023eb37864", "fallback": "d83d50d75966b5d4d33eb286a983f41fdd4901b2ddb23a91fee46c2e5e67f9c5"},
    6: {"fts5": "218bbf8e30138a49270d5bd7edd87050e9df8d7e551b48fdfa70ce69966ca0f0", "fallback": "54f7712b041f52cb943d9fcca3c2f693c485f51277687556dd9373acbf04f990"},
    7: {"fts5": "a2839758d6133db02ff470002d9b5117203adab1715cd86dccccc9de00ca0469", "fallback": "891b6d972edcae964371bcb6780ed227ba12cd8f8b54d850d681dad5d2a070f6"},
    8: {"fts5": "1b59a23061504a5ca13b070d7d677ad97dc0125766c9943965eb1c6ee80f8ddb", "fallback": "e094dbc566df6952f62431558a2c0ac71e163f267dfce020d3f8c0ac22569f91"},
    9: {"fts5": "571afa1535be4aa37d1b76bd7dc14e80b361557d278b36ab4cc11a4b77fdfa63", "fallback": "f8bef6ab5e5865a2939a963356081de301a478f162ca499a25cac796ae0b7686"},
    10: {"fts5": "dc697ab2f409759c4058278625a71e857e9dee98bf1b59f16a2a7f6a0e5d3433", "fallback": "47f016f70a2160f54c7030cda20f14bf73d198e2885cd1a61528d78e4031f68f"},
    11: {"fts5": "8a7fa67685e01489067765ed294b231b49b8535f2cd6e76261bfa32915cc4ddd", "fallback": "16a3fd7e4d984247f4ff4507d24f05922a4ff6c4b9e08f6d6cd8505d3786fe17"},
    12: {"fts5": "7c619e6492287da259ebebc56d682697b0c37899441aca57fa350bf533031e80", "fallback": "a9dd3b5141281178b5546089093d7727a9031da583dc62fc53503701c24d459e"},
    13: {"fts5": "60e18d79efdaaf30be3ba7781644f01c9e200c1bb5aa06d4f482906820a0725a", "fallback": "b21b57467dcb8afac325e1a43e149aaec8379585a5e18eb40f0d0f4d83cc1a06"},
    14: {"fts5": "2fa8614278301a8e1a0ea7aa411888fada82b18eda7d400a34064b10e11863a2", "fallback": "1004fb91834bdbfd868535dd7d4f9c847d1b1703092d178a7b7f9094dab83fa8"},
    15: {"fts5": "06c7791fb1a48550b2a4e272e4adbf5884887a90260d78faba7fdba102871571", "fallback": "46770dd1334989cad5818ccf14ffc8de9ce6ad1710bc4d548abe1598ac790e8a"},
    16: {"fts5": "219f51d46f8fb07b743bc51ac3d60abd79e8a8cb2684973066f5e74b1a249986", "fallback": "dad2660b32a847e6416cf1acee1f6fdb162c9388e27aa07b0467caf0e92a2c92"},
    17: {"fts5": "db37af15c04ce68027bdb6a1fa2c04acee414d00449789638081284d39a3859b", "fallback": "8c7055fdebb0c4d57bf099bc56c1a98f0b06cd35b2287fb4bac6bcb93e9e9844"},
    18: {"fts5": "2a57bf533da57eecc5872ae5d600fdb47de014aa7eb55da779a846f3fb014624", "fallback": "1952b7b3a7fbe44b708cf05867ec56b5bdcac2a822f663aec848ec14d53caa45"},
    19: {"fts5": "270040496292f63de626aaccd791be19d7165304d64fa6beef5799435b3a5401", "fallback": "cf1aeb4f0b5b35352e4ae20cf484cf10e6108a9daebc5838a58eddd344f808fc"},
    20: {"fts5": "a6f87f886301af0f50e194d3429c784fc9dc5dbf6db5be379559a6c6cc4167b3", "fallback": "b121414be3ae2a01d0c0a61e5239bac3013f42c5b56dc9f5dcdd0b411c753630"},
}

_LEDGER_TABLE_INFO = (
    (0, "version", "INTEGER", 0, None, 1),
    (1, "applied_at", "TEXT", 1, None, 0),
)
_FTS5_OBJECT_NAMES = frozenset(
    {
        "fts_evidence",
        "fts_evidence_data",
        "fts_evidence_idx",
        "fts_evidence_content",
        "fts_evidence_docsize",
        "fts_evidence_config",
    }
)
_FORBIDDEN_MIGRATION_STATEMENTS = frozenset(
    {"BEGIN", "COMMIT", "END", "ROLLBACK", "SAVEPOINT", "RELEASE", "VACUUM", "ATTACH", "DETACH"}
)
_ALLOWED_MIGRATION_STATEMENTS = frozenset(
    {"ALTER", "CREATE", "DROP", "INSERT", "PRAGMA"}
)
_SQL_NUMBER = re.compile(
    r"(?:0[xX][0-9A-Fa-f]+|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)"
)
_SQL_MULTI_OPERATORS = ("->>", "->", "||", "<<", ">>", "<=", ">=", "==", "!=", "<>")
_SQL_SINGLE_TOKENS = frozenset("~&|+-*/%<>=(),.;:?!@$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.fts5 = False

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type:
            self.conn.rollback()
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self.conn.execute("BEGIN")
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    @staticmethod
    def _migration_inventory() -> tuple[tuple[int, Path], ...]:
        paths = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
        if not paths:
            raise FileNotFoundError(f"no migration files found in {MIGRATIONS_DIR}")
        inventory: list[tuple[int, Path]] = []
        for path in paths:
            match = re.fullmatch(r"([0-9]{4})_.+\.sql", path.name)
            if match is None:
                raise RuntimeError(f"invalid migration filename: {path.name}")
            inventory.append((int(match.group(1)), path))
        versions = [version for version, _ in inventory]
        expected = list(range(1, len(inventory) + 1))
        if versions != expected:
            raise RuntimeError(
                f"migration inventory must be one unique continuous prefix: {versions}"
            )
        return tuple(inventory)

    @staticmethod
    def _sql_statements(script: str, path: Path) -> Iterator[str]:
        """Yield complete SQLite statements without ``executescript`` commits."""

        buffer = ""
        for line in script.splitlines(keepends=True):
            buffer += line
            if sqlite3.complete_statement(buffer):
                statement = buffer.strip().lstrip("\ufeff").lstrip()
                buffer = ""
                if statement:
                    yield statement
        if buffer.strip():
            raise RuntimeError(f"incomplete SQL statement in migration {path.name}")

    @staticmethod
    def _validate_migration_statement(statement: str, path: Path) -> None:
        tokens = Database._normalized_sql_semantics(statement)
        if not tokens:
            return
        keyword = tokens[0].upper()
        if keyword in _FORBIDDEN_MIGRATION_STATEMENTS:
            raise RuntimeError(
                f"migration {path.name} contains forbidden transaction-escaping statement: {keyword}"
            )
        if keyword not in _ALLOWED_MIGRATION_STATEMENTS:
            raise RuntimeError(
                f"migration {path.name} has forbidden or unrecognized first keyword: {tokens[0]}"
            )
        if keyword == "PRAGMA":
            pragma = tuple(token.casefold() for token in tokens)
            if pragma not in {
                ("pragma", "foreign_keys", "=", "on", ";"),
                ("pragma", "foreign_keys", "=", "off", ";"),
                ("pragma", "foreign_keys", "=", "0", ";"),
                ("pragma", "foreign_keys", "=", "1", ";"),
            }:
                raise RuntimeError(f"migration {path.name} contains forbidden PRAGMA")

    @staticmethod
    def _pragma_identifier(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    @staticmethod
    def _normalized_sql_semantics(sql: str | None) -> tuple[str, ...]:
        if not sql:
            return ()
        result: list[str] = []
        index = 0
        length = len(sql)
        while index < length:
            character = sql[index]
            if character.isspace() or character == "\ufeff":
                index += 1
                continue
            if sql.startswith("--", index):
                newline = sql.find("\n", index + 2)
                index = length if newline < 0 else newline + 1
                continue
            if sql.startswith("/*", index):
                end = sql.find("*/", index + 2)
                if end < 0:
                    raise RuntimeError("unterminated block comment in SQL semantics")
                index = end + 2
                continue
            if character == "'":
                end = index + 1
                while end < length:
                    if sql[end] == "'":
                        if end + 1 < length and sql[end + 1] == "'":
                            end += 2
                            continue
                        end += 1
                        break
                    end += 1
                else:
                    raise RuntimeError("unterminated string literal in SQL semantics")
                result.append("string:" + sql[index:end])
                index = end
                continue
            if character in {'"', '`', '['}:
                closing = ']' if character == '[' else character
                end = index + 1
                value: list[str] = []
                while end < length:
                    if sql[end] == closing:
                        if end + 1 < length and sql[end + 1] == closing:
                            value.append(closing)
                            end += 2
                            continue
                        end += 1
                        break
                    value.append(sql[end])
                    end += 1
                else:
                    raise RuntimeError("unterminated quoted identifier in SQL semantics")
                result.append("".join(value).casefold())
                index = end
                continue
            if character.isalpha() or character == "_" or ord(character) >= 128:
                end = index + 1
                while end < length and (
                    sql[end].isalnum() or sql[end] in {"_", "$"} or ord(sql[end]) >= 128
                ):
                    end += 1
                result.append(sql[index:end].casefold())
                index = end
                continue
            number = _SQL_NUMBER.match(sql, index)
            if number is not None:
                result.append(number.group(0).casefold())
                index = number.end()
                continue
            operator = next(
                (item for item in _SQL_MULTI_OPERATORS if sql.startswith(item, index)),
                None,
            )
            if operator is not None:
                result.append(operator)
                index += len(operator)
                continue
            if character in _SQL_SINGLE_TOKENS:
                result.append(character)
                index += 1
                continue
            raise RuntimeError(
                f"unrecognized character {character!r} in SQL semantics at offset {index}"
            )
        return tuple(result)

    @classmethod
    def _stage_structural_snapshot(cls, conn: sqlite3.Connection) -> dict[str, Any]:
        rows = [
            tuple(row)
            for row in conn.execute(
                "SELECT type,name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
            )
        ]
        ordinary = [row for row in rows if not str(row[1]).startswith("fts_evidence")]
        objects = [(str(row[0]), str(row[1])) for row in ordinary]
        tables_and_views = [
            str(row[1]) for row in ordinary if str(row[0]) in {"table", "view"}
        ]
        columns: dict[str, list[list[Any]]] = {}
        foreign_keys: dict[str, list[list[Any]]] = {}
        indexes: dict[str, list[dict[str, Any]]] = {}
        for name in tables_and_views:
            quoted = cls._pragma_identifier(name)
            columns[name] = [
                list(row) for row in conn.execute(f"PRAGMA table_xinfo({quoted})")
            ]
            foreign_keys[name] = [
                list(row) for row in conn.execute(f"PRAGMA foreign_key_list({quoted})")
            ]
            table_indexes: list[dict[str, Any]] = []
            for index_row in conn.execute(f"PRAGMA index_list({quoted})"):
                index_name = str(index_row[1])
                index_quoted = cls._pragma_identifier(index_name)
                table_indexes.append(
                    {
                        "name": index_name,
                        "unique": int(index_row[2]),
                        "origin": str(index_row[3]),
                        "partial": int(index_row[4]),
                        "columns": [
                            list(item)
                            for item in conn.execute(f"PRAGMA index_xinfo({index_quoted})")
                        ],
                    }
                )
            indexes[name] = sorted(table_indexes, key=lambda item: item["name"])

        sql_semantics = {
            f"{row[0]}:{row[1]}": list(cls._normalized_sql_semantics(row[2]))
            for row in ordinary
            if str(row[0]) in {"index", "table", "trigger", "view"}
        }
        fts_rows = [row for row in rows if str(row[1]).startswith("fts_evidence")]
        fts_objects = sorted((str(row[0]), str(row[1])) for row in fts_rows)
        fts_structure: dict[str, Any] = {}
        for object_type, name in fts_objects:
            if object_type != "table":
                continue
            quoted = cls._pragma_identifier(name)
            object_indexes = []
            for index_row in conn.execute(f"PRAGMA index_list({quoted})"):
                index_name = str(index_row[1])
                index_quoted = cls._pragma_identifier(index_name)
                object_indexes.append(
                    {
                        "name": index_name,
                        "unique": int(index_row[2]),
                        "origin": str(index_row[3]),
                        "partial": int(index_row[4]),
                        "columns": [
                            list(item)
                            for item in conn.execute(f"PRAGMA index_xinfo({index_quoted})")
                        ],
                    }
                )
            fts_structure[name] = {
                "columns": [
                    list(row) for row in conn.execute(f"PRAGMA table_xinfo({quoted})")
                ],
                "indexes": sorted(object_indexes, key=lambda item: item["name"]),
            }
        main = next((row for row in fts_rows if row[1] == "fts_evidence"), None)
        if main is None or main[0] != "table":
            fts_mode = "missing"
            fts_semantics: list[str] = []
            fts_columns: list[list[Any]] = []
        else:
            fts_semantics = list(cls._normalized_sql_semantics(main[2]))
            fts_mode = "fts5" if "using" in fts_semantics and "fts5" in fts_semantics else "fallback"
            fts_columns = [
                list(row)
                for row in conn.execute('PRAGMA table_xinfo("fts_evidence")')
            ]
        return {
            "objects": objects,
            "columns": columns,
            "foreign_keys": foreign_keys,
            "indexes": indexes,
            "sql_semantics": sql_semantics,
            "fts": {
                "mode": fts_mode,
                "objects": fts_objects,
                "structure": fts_structure,
                "main_type": None if main is None else str(main[0]),
                "columns": fts_columns,
                "create_semantics": fts_semantics,
            },
        }

    @classmethod
    def _stage_structural_signature(cls, conn: sqlite3.Connection) -> str:
        raw = json.dumps(
            cls._stage_structural_snapshot(conn),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _existing_migration_prefix(self, latest: int) -> tuple[int, ...]:
        objects = {
            str(row[0])
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' AND type IN ('table','view','index','trigger')"
            )
        }
        if "schema_migration" not in objects:
            if objects:
                raise RuntimeError(
                    "database contains business objects but has no schema_migration ledger"
                )
            return ()
        ledger_info = tuple(
            tuple(row)
            for row in self.conn.execute("PRAGMA table_info(schema_migration)")
        )
        if ledger_info != _LEDGER_TABLE_INFO:
            raise RuntimeError("schema_migration ledger table shape mismatch")
        try:
            versions = tuple(
                int(row[0])
                for row in self.conn.execute(
                    "SELECT version FROM schema_migration ORDER BY version"
                )
            )
        except sqlite3.DatabaseError as exc:
            raise RuntimeError("cannot read schema_migration ledger") from exc
        if versions and versions != tuple(range(1, versions[-1] + 1)):
            raise RuntimeError(f"schema_migration ledger is not an exact prefix: {versions}")
        if versions and versions[-1] > latest:
            raise RuntimeError(
                f"database schema version {versions[-1]} is newer than supported {latest}"
            )
        if not versions:
            raise RuntimeError("empty schema_migration ledger is not a valid stage zero")
        return versions

    def _validate_stage_objects(self, target_version: int) -> None:
        ledger_info = tuple(
            tuple(row)
            for row in self.conn.execute("PRAGMA table_info(schema_migration)")
        )
        if ledger_info != _LEDGER_TABLE_INFO:
            raise RuntimeError("schema_migration ledger table shape mismatch")
        snapshot = self._stage_structural_snapshot(self.conn)
        fts = snapshot["fts"]
        if fts["mode"] == "fts5":
            expected_fts_objects = {("table", name) for name in _FTS5_OBJECT_NAMES}
            if set(tuple(item) for item in fts["objects"]) != expected_fts_objects:
                raise RuntimeError(f"FTS5 schema object set mismatch: {fts['objects']}")
        elif fts["mode"] == "fallback":
            if fts["objects"] != [("table", "fts_evidence")]:
                raise RuntimeError(f"fallback FTS schema object set mismatch: {fts['objects']}")
            if tuple(tuple(row) for row in fts["columns"]) != (
                (0, "evidence_id", "TEXT", 0, None, 1, 0),
                (1, "stable_id", "TEXT", 1, None, 0, 0),
                (2, "language", "TEXT", 1, None, 0, 0),
                (3, "content", "TEXT", 1, None, 0, 0),
            ):
                raise RuntimeError("fallback FTS table shape mismatch")
        else:
            raise RuntimeError("FTS schema object is missing")

        actual = self._stage_structural_signature(self.conn)
        expected = _SCHEMA_STRUCTURAL_SIGNATURES.get(target_version, {}).get(fts["mode"])
        if actual != expected:
            raise RuntimeError(
                f"schema {target_version} structural signature mismatch: "
                f"expected {expected}, got {actual}"
            )

    def initialize(self) -> None:
        """Initialize through the generic product ceiling (Schema 10).

        Schema 11 is an explicitly opted-in Phase 4D2A family canary.  Keep
        the generic command at its established Schema 10 behavior so opening
        an existing product database cannot silently activate that canary.
        """

        inventory = self._migration_inventory()
        self.initialize_through(min(inventory[-1][0], DEFAULT_INITIALIZE_CEILING))

    def initialize_through(self, target_version: int) -> None:
        """Atomically initialize or upgrade through one exact migration prefix."""

        if self.conn.in_transaction:
            raise RuntimeError("cannot initialize schema inside a caller-owned transaction")
        inventory = self._migration_inventory()
        latest = inventory[-1][0]
        if isinstance(target_version, bool) or not isinstance(target_version, int):
            raise TypeError("target migration version must be a non-boolean integer")
        if not 1 <= target_version <= latest:
            raise ValueError(
                f"target migration version must be between 1 and {latest}: {target_version!r}"
            )
        existing = self._existing_migration_prefix(latest)
        current = existing[-1] if existing else 0
        if current > target_version:
            raise RuntimeError(
                f"database schema {current} exceeds requested ceiling {target_version}"
            )

        pending: list[tuple[int, Path, tuple[str, ...]]] = []
        for version, migration_path in inventory:
            if not current < version <= target_version:
                continue
            raw_script = migration_path.read_bytes()
            expected_raw_sha256 = _MIGRATION_RAW_SHA256.get(migration_path.name)
            if expected_raw_sha256 is not None:
                actual_raw_sha256 = hashlib.sha256(raw_script).hexdigest()
                if actual_raw_sha256 != expected_raw_sha256:
                    raise RuntimeError(
                        f"migration {migration_path.name} raw SQL seal mismatch: "
                        f"expected {expected_raw_sha256}, got {actual_raw_sha256}"
                    )
            script = raw_script.decode("utf-8-sig")
            statements = tuple(self._sql_statements(script, migration_path))
            for statement in statements:
                self._validate_migration_statement(statement, migration_path)
            pending.append((version, migration_path, statements))

        # Migrations 0005/0006 rebuild foreign-key parents. SQLite cannot
        # toggle foreign_keys inside a transaction, so disable enforcement
        # before BEGIN and prove the resulting graph before commit.
        self.conn.execute("PRAGMA foreign_keys=OFF")
        began = False
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            began = True
            if current == 0:
                self.conn.execute(
                    "CREATE TABLE schema_migration ("
                    "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
            for version, migration_path, statements in pending:
                for statement in statements:
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO schema_migration(version, applied_at) VALUES (?, ?)",
                    (version, utc_now()),
                )
            if target_version >= 2:
                self._ensure_identity_columns()
            if target_version >= 1:
                self._ensure_fts_tables()
            ledger = tuple(
                int(row[0])
                for row in self.conn.execute(
                    "SELECT version FROM schema_migration ORDER BY version"
                )
            )
            expected = tuple(range(1, target_version + 1))
            if ledger != expected:
                raise RuntimeError(
                    f"post-migration ledger mismatch: expected {expected}, got {ledger}"
                )
            self._validate_stage_objects(target_version)
            violations = self.conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError(
                    f"foreign key violations after migration: {violations[:5]}"
                )
            self.conn.commit()
            began = False
        except Exception:
            if began:
                self.conn.rollback()
            raise
        finally:
            self.conn.execute("PRAGMA foreign_keys=ON")
        if int(self.conn.execute("PRAGMA foreign_keys").fetchone()[0]) != 1:
            raise RuntimeError("failed to restore SQLite foreign key enforcement")

    def _ensure_identity_columns(self) -> None:
        """Add nullable identity links for databases created before 0002.

        Existing local databases are disposable/rebuildable, but keeping these
        columns nullable makes the migration safe for a user who opens an old
        Phase 1/2 database before rebuilding it with the corrected identity
        semantics.
        """
        additions = {
            "passage": ("talk_evidence_id", "TEXT REFERENCES talk_evidence(id)"),
            "passage_version": ("talk_evidence_id", "TEXT REFERENCES talk_evidence(id)"),
        }
        for table, (column, definition) in additions.items():
            columns = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
            if column not in columns:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_passage_talk_evidence ON passage(talk_evidence_id)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_passage_version_talk_evidence ON passage_version(talk_evidence_id)"
        )

    def _ensure_fts_tables(self) -> None:
        try:
            self.conn.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS fts_evidence
                   USING fts5(evidence_id UNINDEXED, stable_id UNINDEXED,
                              language UNINDEXED, content)"""
            )
            self.fts5 = True
        except sqlite3.OperationalError:
            # Exact search never depends on FTS5.  This transparent fallback is
            # still useful for diagnostics and keeps schema initialization portable.
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS fts_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    stable_id TEXT NOT NULL,
                    language TEXT NOT NULL,
                    content TEXT NOT NULL
                )"""
            )
            self.fts5 = False

    def rebuild_fts(self, snapshot_id: str) -> None:
        self._ensure_fts_tables()
        self.conn.execute("DELETE FROM fts_evidence")
        # Stream the UNION directly into FTS. The old path materialized sqlite
        # rows and a second Python list, then inserted one row at a time.
        self.conn.execute(
            """INSERT INTO fts_evidence(evidence_id, stable_id, language, content)
               SELECT pt.id, p.stable_id, pt.language, pt.raw_text
               FROM passage_text pt JOIN passage p ON p.stable_id=pt.passage_id
               WHERE pt.snapshot_id=?
               UNION ALL
               SELECT lt.id, lt.id, lt.language, lt.raw_text
               FROM localized_text lt
               WHERE lt.snapshot_id=?
                 AND lt.id NOT IN (
                       SELECT pt.localized_text_id
                       FROM passage_text pt
                       WHERE pt.localized_text_id IS NOT NULL
                   )""",
            (snapshot_id, snapshot_id),
        )

    def ensure_source(self, source_id: str, kind: str, url: str, license_note: str | None = None) -> None:
        self.conn.execute(
            """INSERT INTO source_catalog(id, kind, url, license_note) VALUES (?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, url=excluded.url,
                                             license_note=excluded.license_note""",
            (source_id, kind, url, license_note),
        )

    def ensure_snapshot(self, snapshot: dict[str, Any]) -> str:
        """Insert a snapshot once; only its lifecycle status may change later."""
        identity_columns = (
            "source_id",
            "release_label",
            "build",
            "commit",
            "tree",
            "observed_at",
        )
        existing = self.conn.execute(
            'SELECT source_id, release_label, build, "commit", tree, observed_at, status '
            "FROM source_snapshot WHERE id=?",
            (snapshot["id"],),
        ).fetchone()
        if existing is not None:
            expected = tuple(snapshot[column] for column in identity_columns)
            actual = tuple(existing[column] for column in identity_columns)
            if actual != expected:
                raise sqlite3.IntegrityError(
                    f"source_snapshot identity mismatch for {snapshot['id']}"
                )
            if existing["status"] != snapshot["status"]:
                self.conn.execute(
                    "UPDATE source_snapshot SET status=? WHERE id=?",
                    (snapshot["status"], snapshot["id"]),
                )
            return snapshot["id"]

        # Keep the legacy columns populated on first insert for compatibility.
        # Parser/build/run metadata is authoritative in parser_build/ingest_run.
        self.conn.execute(
            """INSERT INTO source_snapshot(
                   id, source_id, release_label, build, "commit", tree, observed_at,
                   parser_version, mapping_version, status, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot["id"],
                snapshot["source_id"],
                snapshot["release_label"],
                snapshot["build"],
                snapshot["commit"],
                snapshot["tree"],
                snapshot["observed_at"],
                snapshot.get("parser_version", "legacy-unscoped"),
                snapshot.get("mapping_version", "legacy-unscoped"),
                snapshot["status"],
                canonical_json(snapshot.get("metadata", {})),
            ),
        )
        return snapshot["id"]

    def ensure_source_file(self, snapshot_id: str, info: dict[str, Any]) -> str:
        relative_path = info["relative_path"]
        file_id = digest_id("sf", snapshot_id, relative_path)
        file_format = info.get("format", Path(relative_path).suffix.lstrip(".") or "unknown")
        expected = (
            snapshot_id,
            relative_path,
            info.get("upstream_blob_oid"),
            info["sha256"],
            info["size_bytes"],
            file_format,
        )
        existing = self.conn.execute(
            """SELECT snapshot_id, relative_path, upstream_blob_oid, sha256,
                      size_bytes, format
               FROM source_file WHERE id=?""",
            (file_id,),
        ).fetchone()
        if existing is not None:
            actual = tuple(existing[column] for column in (
                "snapshot_id",
                "relative_path",
                "upstream_blob_oid",
                "sha256",
                "size_bytes",
                "format",
            ))
            if actual != expected:
                raise sqlite3.IntegrityError(
                    f"source_file identity mismatch for {file_id}"
                )
            return file_id

        self.conn.execute(
            """INSERT INTO source_file(
                   id, snapshot_id, relative_path, upstream_blob_oid, sha256,
                   size_bytes, format, root_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                file_id,
                snapshot_id,
                relative_path,
                info.get("upstream_blob_oid"),
                info["sha256"],
                info["size_bytes"],
                file_format,
                info["root_path"],
            ),
        )
        return file_id

    def ensure_source_record(
        self,
        source_file_id: str,
        pointer: str,
        record_kind: str,
        native_id: str | int | None,
        raw_value: Any,
        raw_json: str | None = None,
    ) -> str:
        pointer = str(pointer)
        record_id = digest_id("sr", source_file_id, pointer)
        if isinstance(raw_value, bytes):
            raw_checksum = sha256_text(raw_value.decode("utf-8", errors="replace"))
        elif isinstance(raw_value, str):
            raw_checksum = sha256_text(raw_value)
        else:
            raw_checksum = sha256_text(canonical_json(raw_value))
        existing = self.conn.execute(
            "SELECT source_file_id, pointer, raw_sha256 FROM source_record WHERE id=?",
            (record_id,),
        ).fetchone()
        if existing is not None:
            actual = (
                existing["source_file_id"],
                existing["pointer"],
                existing["raw_sha256"],
            )
            expected = (source_file_id, pointer, raw_checksum)
            if actual != expected:
                raise sqlite3.IntegrityError(
                    f"source_record identity mismatch for {record_id}"
                )
            return record_id

        self.conn.execute(
            """INSERT INTO source_record(
                   id, source_file_id, pointer, record_kind, native_id, raw_sha256, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                record_id,
                source_file_id,
                pointer,
                record_kind,
                None if native_id is None else str(native_id),
                raw_checksum,
                raw_json,
            ),
        )
        return record_id

    def ensure_parser_build(
        self,
        build_id: str,
        *,
        parser_version: str,
        mapping_version: str,
        fingerprint: str,
        config_checksum: str,
        created_at: str | None = None,
    ) -> str:
        expected = (
            parser_version,
            mapping_version,
            fingerprint,
            config_checksum,
        )
        existing = self.conn.execute(
            """SELECT parser_version, mapping_version, fingerprint, config_checksum
               FROM parser_build WHERE id=?""",
            (build_id,),
        ).fetchone()
        if existing is not None:
            actual = tuple(existing[column] for column in (
                "parser_version",
                "mapping_version",
                "fingerprint",
                "config_checksum",
            ))
            if actual != expected:
                raise sqlite3.IntegrityError(
                    f"parser_build identity mismatch for {build_id}"
                )
            return build_id

        self.conn.execute(
            """INSERT INTO parser_build(
                   id, parser_version, mapping_version, fingerprint,
                   config_checksum, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (build_id, *expected, created_at or utc_now()),
        )
        return build_id

    def ensure_ingest_run(
        self,
        run_id: str,
        *,
        snapshot_id: str,
        parser_build_id: str,
        scope: Any,
        config: Any,
        status: str,
        created_at: str | None = None,
    ) -> str:
        scope_json = scope if isinstance(scope, str) else canonical_json(scope)
        config_json = config if isinstance(config, str) else canonical_json(config)
        expected = (snapshot_id, parser_build_id, scope_json, config_json)
        existing = self.conn.execute(
            """SELECT snapshot_id, parser_build_id, scope_json, config_json, status
               FROM ingest_run WHERE id=?""",
            (run_id,),
        ).fetchone()
        if existing is not None:
            actual = tuple(existing[column] for column in (
                "snapshot_id",
                "parser_build_id",
                "scope_json",
                "config_json",
            ))
            if actual != expected:
                raise sqlite3.IntegrityError(
                    f"ingest_run identity mismatch for {run_id}"
                )
            if existing["status"] != status:
                self.conn.execute(
                    "UPDATE ingest_run SET status=? WHERE id=?",
                    (status, run_id),
                )
            return run_id

        self.conn.execute(
            """INSERT INTO ingest_run(
                   id, snapshot_id, parser_build_id, scope_json, config_json,
                   status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (run_id, *expected, status, created_at or utc_now()),
        )
        return run_id

    def ensure_logical_talk(
        self,
        logical_id: str,
        native_namespace: str,
        native_id: str | int,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Upsert a logical Talk family without collapsing evidence records."""
        self.conn.execute(
            """INSERT INTO logical_talk(id, native_namespace, native_id, status, metadata_json)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   native_namespace=excluded.native_namespace,
                   native_id=excluded.native_id,
                   status=excluded.status,
                   metadata_json=excluded.metadata_json""",
            (logical_id, native_namespace, str(native_id), status, canonical_json(metadata or {})),
        )
        return logical_id

    def ensure_talk_evidence(
        self,
        evidence_id: str,
        snapshot_id: str,
        source_file_id: str,
        source_record_id: str,
        source_pointer: str,
        native_namespace: str,
        native_id: str | int,
        raw_sha256: str,
        normalized_checksum: str,
        status: str = "active",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Register one concrete raw Talk occurrence for a snapshot."""
        self.conn.execute(
            """INSERT INTO talk_evidence(
                   id, snapshot_id, source_file_id, source_record_id,
                   source_pointer, native_namespace, native_id, raw_sha256,
                   normalized_checksum, status, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   snapshot_id=excluded.snapshot_id,
                   source_file_id=excluded.source_file_id,
                   source_record_id=excluded.source_record_id,
                   source_pointer=excluded.source_pointer,
                   native_namespace=excluded.native_namespace,
                   native_id=excluded.native_id,
                   raw_sha256=excluded.raw_sha256,
                   normalized_checksum=excluded.normalized_checksum,
                   status=excluded.status,
                   metadata_json=excluded.metadata_json""",
            (
                evidence_id,
                snapshot_id,
                source_file_id,
                source_record_id,
                source_pointer,
                native_namespace,
                str(native_id),
                raw_sha256,
                normalized_checksum,
                status,
                canonical_json(metadata or {}),
            ),
        )
        return evidence_id

    def link_logical_talk_evidence(
        self,
        logical_id: str,
        evidence_id: str,
        relation_status: str,
        source_class: str,
        source_record_id: str | None = None,
        method: str | None = None,
        confidence: float | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO logical_talk_evidence(
                   logical_talk_id, evidence_id, relation_status, source_class,
                   source_record_id, method, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(logical_talk_id, evidence_id) DO UPDATE SET
                   relation_status=excluded.relation_status,
                   source_class=excluded.source_class,
                   source_record_id=excluded.source_record_id,
                   method=excluded.method,
                   confidence=excluded.confidence""",
            (logical_id, evidence_id, relation_status, source_class, source_record_id, method, confidence),
        )

    def ensure_document(
        self,
        stable: str,
        content_type: str,
        native_namespace: str,
        native_id: str | int,
        status: str = "active",
    ) -> None:
        self.conn.execute(
            """INSERT INTO document(stable_id, content_type, native_namespace, native_id, status)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(stable_id) DO UPDATE SET status=excluded.status""",
            (stable, content_type, native_namespace, str(native_id), status),
        )

    def ensure_document_version(
        self,
        document_id: str,
        snapshot_id: str,
        source_record_id: str,
        raw_value: Any,
        normalized_value: Any,
        metadata: dict[str, Any],
        status: str = "active",
    ) -> None:
        raw_checksum = sha256_text(raw_value if isinstance(raw_value, str) else canonical_json(raw_value))
        normalized_checksum = sha256_text(
            normalized_value if isinstance(normalized_value, str) else canonical_json(normalized_value)
        )
        self.conn.execute(
            """INSERT INTO document_version(
                   document_id, snapshot_id, source_record_id, raw_checksum,
                   normalized_checksum, metadata_json, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(document_id, snapshot_id) DO UPDATE SET
                   source_record_id=excluded.source_record_id,
                   raw_checksum=excluded.raw_checksum,
                   normalized_checksum=excluded.normalized_checksum,
                   metadata_json=excluded.metadata_json, status=excluded.status""",
            (document_id, snapshot_id, source_record_id, raw_checksum, normalized_checksum, canonical_json(metadata), status),
        )

    def ensure_passage(
        self,
        stable: str,
        document_id: str,
        passage_type: str,
        namespace: str,
        native_id: str | int,
        identity_algorithm: str,
        talk_evidence_id: str | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO passage(stable_id, document_id, passage_type, native_namespace, native_id, identity_algorithm, talk_evidence_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(stable_id) DO UPDATE SET
                   document_id=excluded.document_id,
                   native_namespace=excluded.native_namespace,
                   native_id=excluded.native_id,
                   identity_algorithm=excluded.identity_algorithm,
                   talk_evidence_id=excluded.talk_evidence_id""",
            (stable, document_id, passage_type, namespace, str(native_id), identity_algorithm, talk_evidence_id),
        )

    def ensure_passage_version(
        self,
        passage_id: str,
        snapshot_id: str,
        source_record_id: str,
        sequence_key: str,
        raw_value: Any,
        normalized_value: Any,
        speaker: dict[str, Any] | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        status: str = "active",
        talk_evidence_id: str | None = None,
    ) -> None:
        raw_checksum = sha256_text(raw_value if isinstance(raw_value, str) else canonical_json(raw_value))
        normalized_checksum = sha256_text(
            normalized_value if isinstance(normalized_value, str) else canonical_json(normalized_value)
        )
        self.conn.execute(
            """INSERT INTO passage_version(
                   passage_id, snapshot_id, source_record_id, sequence_key, speaker_json,
                   start_ms, end_ms, raw_checksum, normalized_checksum, status, talk_evidence_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(passage_id, snapshot_id) DO UPDATE SET
                   source_record_id=excluded.source_record_id,
                   sequence_key=excluded.sequence_key, speaker_json=excluded.speaker_json,
                   start_ms=excluded.start_ms, end_ms=excluded.end_ms,
                   raw_checksum=excluded.raw_checksum, normalized_checksum=excluded.normalized_checksum,
                   status=excluded.status, talk_evidence_id=excluded.talk_evidence_id""",
            (
                passage_id,
                snapshot_id,
                source_record_id,
                sequence_key,
                None if speaker is None else canonical_json(speaker),
                start_ms,
                end_ms,
                raw_checksum,
                normalized_checksum,
                status,
                talk_evidence_id,
            ),
        )

    def ensure_localized_text(
        self,
        snapshot_id: str,
        language: str,
        shard: str,
        textmap_hash: str,
        source_file_id: str,
        source_record_id: str,
        raw_text: str,
    ) -> str:
        localized_id = digest_id("lt", snapshot_id, language, shard, textmap_hash)
        normalized = normalize_text(raw_text)
        self.conn.execute(
            """INSERT INTO localized_text(
                   id, snapshot_id, language, shard, textmap_hash, source_file_id,
                   source_record_id, raw_text, normalized_text, raw_checksum, normalized_checksum)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(snapshot_id, language, shard, textmap_hash) DO UPDATE SET
                   raw_text=excluded.raw_text, normalized_text=excluded.normalized_text,
                   raw_checksum=excluded.raw_checksum, normalized_checksum=excluded.normalized_checksum""",
            (
                localized_id,
                snapshot_id,
                language,
                shard,
                textmap_hash,
                source_file_id,
                source_record_id,
                raw_text,
                normalized,
                sha256_text(raw_text),
                sha256_text(normalized),
            ),
        )
        return localized_id

    def ensure_passage_text(
        self,
        passage_id: str,
        snapshot_id: str,
        language: str,
        source_record_id: str,
        raw_text: str,
        localized_text_id: str | None = None,
        source_start: int | None = None,
        source_end: int | None = None,
    ) -> str:
        passage_text_id = digest_id("pt", passage_id, snapshot_id, language)
        normalized = normalize_text(raw_text)
        self.conn.execute(
            """INSERT INTO passage_text(
                   id, passage_id, snapshot_id, language, localized_text_id, source_record_id,
                   raw_text, normalized_text, raw_checksum, normalized_checksum,
                   source_start, source_end)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(passage_id, snapshot_id, language) DO UPDATE SET
                   localized_text_id=excluded.localized_text_id,
                   source_record_id=excluded.source_record_id,
                   raw_text=excluded.raw_text, normalized_text=excluded.normalized_text,
                   raw_checksum=excluded.raw_checksum, normalized_checksum=excluded.normalized_checksum,
                   source_start=excluded.source_start, source_end=excluded.source_end""",
            (
                passage_text_id,
                passage_id,
                snapshot_id,
                language,
                localized_text_id,
                source_record_id,
                raw_text,
                normalized,
                sha256_text(raw_text),
                sha256_text(normalized),
                source_start,
                source_end,
            ),
        )
        return passage_text_id

    def add_relation(self, from_id: str, to_id: str, relation_type: str, source_class: str, source_record_id: str | None, method: str | None = None, confidence: float | None = None) -> str:
        relation_id = digest_id("dr", from_id, to_id, relation_type, source_class, method or "")
        self.conn.execute(
            """INSERT INTO document_relation(
                   id, from_document_id, to_document_id, relation_type, source_class,
                   source_record_id, method, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id)
               DO UPDATE SET source_record_id=excluded.source_record_id, method=excluded.method,
                             confidence=excluded.confidence""",
            (relation_id, from_id, to_id, relation_type, source_class, source_record_id, method, confidence),
        )
        return relation_id

    def add_edge(self, from_id: str, to_id: str, edge_type: str, source_class: str, source_record_id: str | None, method: str | None = None, confidence: float | None = None) -> str:
        edge_id = digest_id("pe", from_id, to_id, edge_type, source_class, method or "")
        self.conn.execute(
            """INSERT INTO passage_edge(
                   id, from_passage_id, to_passage_id, edge_type, source_class,
                   source_record_id, method, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id)
               DO UPDATE SET source_record_id=excluded.source_record_id, method=excluded.method,
                             confidence=excluded.confidence""",
            (edge_id, from_id, to_id, edge_type, source_class, source_record_id, method, confidence),
        )
        return edge_id

    def add_fact(
        self,
        subject_type: str,
        subject_id: str,
        key: str,
        value: Any,
        source_class: str,
        source_record_id: str | None,
        language: str | None = None,
        method: str | None = None,
        confidence: float | None = None,
        model: str | None = None,
    ) -> str:
        fact_id = digest_id("mf", subject_type, subject_id, key, language or "", canonical_json(value), source_class, source_record_id or "")
        self.conn.execute(
            """INSERT OR IGNORE INTO metadata_fact(
                   id, subject_type, subject_id, key, language, value_json, source_class,
                   source_record_id, method, confidence, model)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (fact_id, subject_type, subject_id, key, language, canonical_json(value), source_class, source_record_id, method, confidence, model),
        )
        return fact_id

    def add_issue(
        self,
        snapshot_id: str,
        severity: str,
        code: str,
        message: str,
        source_file_id: str | None = None,
        source_record_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> int:
        details_json = canonical_json(details or {})
        existing = self.conn.execute(
            """SELECT id FROM ingest_issue
               WHERE snapshot_id=? AND severity=? AND code=? AND message=?
                 AND source_file_id IS ? AND source_record_id IS ? AND details_json=?
               LIMIT 1""",
            (snapshot_id, severity, code, message, source_file_id, source_record_id, details_json),
        ).fetchone()
        if existing is not None:
            return int(existing[0])
        cursor = self.conn.execute(
            """INSERT INTO ingest_issue(snapshot_id, severity, code, message, source_file_id, source_record_id, details_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (snapshot_id, severity, code, message, source_file_id, source_record_id, details_json),
        )
        return int(cursor.lastrowid)

    def add_textmap_conflict(self, snapshot_id: str, conflict: dict[str, Any]) -> str:
        conflict_id = digest_id("tmc", snapshot_id, conflict["language"], conflict["textmap_hash"], conflict["shard_a"], conflict["shard_b"])
        self.conn.execute(
            """INSERT OR IGNORE INTO textmap_conflict(
                   id, snapshot_id, language, textmap_hash, shard_a, shard_b,
                   value_a, value_b, source_record_a, source_record_b)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                conflict_id,
                snapshot_id,
                conflict["language"],
                conflict["textmap_hash"],
                conflict["shard_a"],
                conflict["shard_b"],
                conflict["value_a"],
                conflict["value_b"],
                conflict.get("source_record_a"),
                conflict.get("source_record_b"),
            ),
        )
        return conflict_id

    def report(self, snapshot_id: str) -> dict[str, Any]:
        def count(sql: str, args: tuple[Any, ...] = ()) -> int:
            return int(self.conn.execute(sql, args).fetchone()[0])

        documents = {
            row["content_type"]: row["n"]
            for row in self.conn.execute("SELECT content_type, COUNT(*) n FROM document GROUP BY content_type")
        }
        issues = {
            row["code"]: row["n"]
            for row in self.conn.execute("SELECT code, COUNT(*) n FROM ingest_issue WHERE snapshot_id=? GROUP BY code", (snapshot_id,))
        }
        return {
            "snapshot_id": snapshot_id,
            "documents": documents,
            "document_count": count("SELECT COUNT(*) FROM document"),
            "passage_count": count("SELECT COUNT(*) FROM passage"),
            "passage_text_count": count("SELECT COUNT(*) FROM passage_text WHERE snapshot_id=?", (snapshot_id,)),
            "localized_text_count": count("SELECT COUNT(*) FROM localized_text WHERE snapshot_id=?", (snapshot_id,)),
            "textmap_conflict_count": count("SELECT COUNT(*) FROM textmap_conflict WHERE snapshot_id=?", (snapshot_id,)),
            "issues": issues,
            "fts5": self.fts5,
        }


def validate_exact_schema_structure(
    conn: sqlite3.Connection, target_version: int
) -> None:
    """Read-only validation of one frozen schema prefix on an existing connection."""

    if target_version not in _SCHEMA_STRUCTURAL_SIGNATURES:
        raise ValueError(f"unsupported structural schema target: {target_version}")
    validator = Database.__new__(Database)
    validator.conn = conn
    validator._validate_stage_objects(target_version)
