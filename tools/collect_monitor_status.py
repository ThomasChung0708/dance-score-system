"""Local monitoring validation collector; no game input and no frame-by-frame images."""
import argparse
import json
from pathlib import Path
import re
import time
from urllib.request import Request,urlopen


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=40463)
    parser.add_argument('--seconds',type=float,default=600);parser.add_argument('--until-result',action='store_true')
    parser.add_argument('--output',type=Path,default=Path('reports/phase4b/real-game'))
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    base=f'http://127.0.0.1:{args.port}'
    page=urlopen(base+'/monitor').read().decode('utf8');token=re.search('name="dance-token" content="([^"]+)',page)[1]
    started=time.monotonic();rows=[];result_at=None
    with (args.output/'status-trace.jsonl').open('w',encoding='utf8') as stream:
        while time.monotonic()-started<args.seconds:
            status=json.load(urlopen(Request(base+'/api/monitor/status',headers={'X-Dance-Token':token}),timeout=5))
            row=dict(elapsed=round(time.monotonic()-started,2),status=status)
            rows.append(row);stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush()
            if (status['tracking'] or {}).get('last_result'):
                result_at=result_at or time.monotonic()
            if args.until_result and result_at and time.monotonic()-result_at>10:break
            if not status['running']:break
            time.sleep(2)
    (args.output/'status-trace.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
    print('Samples:',len(rows),'states:',sorted({(r['status']['tracking'] or {}).get('state','UNKNOWN') for r in rows}))


if __name__=='__main__':main()
