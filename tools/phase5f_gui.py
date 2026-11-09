"""Clearly generated GUI acceptance fixtures, isolated from all formal records."""
import argparse
import json
from pathlib import Path
import tools.phase5e_gui as fixtures
from dance_results.gui import create_server

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5f/gui-run';DB=OUT/'ISOLATED.sqlite3';MON=OUT/'monitor'
fixtures.OUT=OUT;fixtures.DB=DB;fixtures.MON=MON;fixtures.MANIFEST=OUT/'manifest.json';fixtures.TITLE='[PHASE5F ISOLATED GENERATED] Pulse Archive 夜の軌跡'


def main():
    p=argparse.ArgumentParser();p.add_argument('--prepare',action='store_true');p.add_argument('--add',choices=fixtures.CASES);p.add_argument('--commit',action='store_true');args=p.parse_args()
    if args.prepare:
        fixtures.prepare();fixtures.add_review('score')
    elif args.add:fixtures.add_review(args.add,commit=args.commit)
    else:
        server=create_server(DB,40511,MON)
        print('GENERATED ISOLATED GUI http://127.0.0.1:40511/reviews',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:server.server_close()


if __name__=='__main__':main()
