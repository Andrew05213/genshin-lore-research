"""Fixed NPC, Generic Dialog and raw Reminder contracts; no entity model."""
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMIT = "9587d1afbd9ab0419cdd00dc05ecd114b9e3fe99"
TREE = "aa578c08c402af39b41f1ffc95124d10f5410272"
SNAPSHOT = "anime-game-data:7.1:" + COMMIT
SCHEMA = ROOT / "schemas/narrative-7.1-npc-dialog-reminder.sql"
SCOPE = ROOT / "manifests/narrative-7.1/physical-scope.json"
SPECS = {
    "npc": ("ExcelBinOutput/NpcExcelConfigData.json", 14444, "id", {"nameTextMapHash": "source_authored_name"}),
    "dialog": ("ExcelBinOutput/DialogExcelConfigData.json", 203908, "GFLDJMJKIKE", {
        "talkContentTextMapHash": "source_authored_content", "talkTitleTextMapHash": "source_authored_title", "talkRoleNameTextMapHash": "source_authored_role_name"}),
    "reminder": ("ExcelBinOutput/ReminderExcelConfigData.json", 18541, "id", {}),
}
TABLES = ("physical_row", "text_owner", "text_resolution", "text_candidate", "locator", "physical_candidate", "release")
LOCALES = ("CHS", "EN", "JP")


def canonical(v):
    return json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def digest(v):
    return hashlib.sha256(canonical(v).encode()).hexdigest()


def guards():
    return "\n".join(f"CREATE TRIGGER {table}_immutable_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'immutable narrative evidence'); END;" for table in TABLES for op in ("INSERT", "UPDATE", "DELETE"))


def check_source(root, scope):
    root = Path(root)
    if subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD", "HEAD^{tree}"], text=True).splitlines() != [COMMIT, TREE]:
        raise ValueError("exact 7.1 checkout required")
    if subprocess.check_output(["git", "-C", str(root), "status", "--porcelain=v1"], text=True).strip():
        raise ValueError("dirty 7.1 source")
    if scope["snapshot"] != SNAPSHOT or scope["source_tree"] != TREE:
        raise ValueError("scope source identity")
    for item in scope["files"]:
        path = root / item["path"]
        if path.stat().st_size != item["size_bytes"] or sha(path) != item["sha256"]:
            raise ValueError("source drift: " + item["path"])
        if subprocess.check_output(["git", "-C", str(root), "rev-parse", COMMIT + ":" + item["path"]], text=True).strip() != item["git_blob"]:
            raise ValueError("source blob drift")


def no_sidecars(path):
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(str(path) + suffix).exists():
            raise ValueError("SQLite sidecar: " + str(path) + suffix)


def ref(family, run, ordinal):
    return "narrative71:" + canonical([SNAPSHOT, family, run, ordinal])
