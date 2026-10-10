import argparse
import json
import os
import tempfile
from pathlib import Path
from genshin_lore.narrative_7_1_build import build

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--family", required=True, choices=("npc", "dialog", "reminder"))
    p.add_argument("--source", required=True); p.add_argument("--output", required=True)
    p.add_argument("--accounting-root", required=True); p.add_argument("--report", required=True)
    a = p.parse_args()
    if Path(a.report).exists():
        raise FileExistsError(a.report)
    if Path(a.report).resolve() in {Path(a.output + suffix).resolve() for suffix in ("", ".lock", "-wal", "-shm", "-journal")}:
        raise ValueError("report/output alias")
    r = build(a.family, a.source, a.output, a.accounting_root)
    stage_report = None
    report_inode = None
    try:
        fd, name = tempfile.mkstemp(prefix=".narrative71-report-", dir=Path(a.report).absolute().parent)
        stage_report = Path(name)
        with os.fdopen(fd, "w") as f:
            json.dump(r, f, ensure_ascii=False, indent=2, sort_keys=True); f.write("\n")
            f.flush(); os.fsync(f.fileno())
        stat = stage_report.stat()
        report_inode = (stat.st_dev, stat.st_ino)
        os.link(stage_report, a.report)
        stage_report.unlink()
        directory = os.open(Path(a.report).absolute().parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        report = Path(a.report)
        if report_inode is not None and report.exists() and (report.stat().st_dev, report.stat().st_ino) == report_inode:
            report.unlink()
        out = Path(a.output)
        if out.exists() and [out.stat().st_dev, out.stat().st_ino] == r["promotion_inode"]:
            out.unlink()
        if stage_report is not None and stage_report.exists():
            stage_report.unlink()
        raise
