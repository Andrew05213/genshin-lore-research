#!/usr/bin/env python3
import argparse,json
from pathlib import Path
from genshin_lore.batch_space import ProjectSpaceGate
from genshin_lore.research_index import build_index

def main(argv=None):
 p=argparse.ArgumentParser();p.add_argument('--corpus',type=Path,required=True);p.add_argument('--bindings',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--report',type=Path);p.add_argument('--projected-bytes',type=int,default=5*1024**3);a=p.parse_args(argv)
 ProjectSpaceGate(Path(__file__).resolve().parents[1]).check('research index fresh-build peak',projected_bytes=a.projected_bytes,cleanup=False)
 if a.output.exists():raise FileExistsError(a.output)
 if a.report and a.report.exists():raise FileExistsError(a.report)
 r=build_index(a.corpus,a.bindings,a.output)
 if a.report:a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(r,indent=2,sort_keys=True)+'\n')
 print(json.dumps(r));return r
if __name__=='__main__':main()
