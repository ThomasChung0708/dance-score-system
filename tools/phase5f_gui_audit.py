"""Audit and retry a known generated HTTP submission; production is read-only."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
from urllib.request import Request,urlopen
from dance_results.sqlite_safety import audit,projection,row_limits,check_integrity
from dance_results.submissions import SubmissionService
from dance_results.recent_updates import recent_updates

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5f';GUI=OUT/'gui-run'


def read(path):return sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)


def main():
    manifest=json.loads((GUI/'manifest.json').read_text(encoding='utf8'));base='http://127.0.0.1:40511'
    token=re.search(r'name="dance-token" content="([^"]+)',urlopen(base+'/reviews').read().decode()).group(1)
    headers={'X-Dance-Token':token,'Content-Type':'application/json'}
    with closing(read(GUI/'ISOLATED.sqlite3')) as db:
        db.row_factory=sqlite3.Row;before=audit(db)
        rid=db.execute('SELECT record_id FROM play_records WHERE session_id=?',(manifest['cases']['score']['session_id'],)).fetchone()[0]
        record=dict(db.execute('SELECT * FROM play_records WHERE record_id=?',(rid,)).fetchone())
    body={**manifest['cases']['score']['body'],'note_review':{},'player_row_id':None,'submission_token':'existing-receipt-only','submit_acknowledge':True}
    assert SubmissionService.request_hash(body)==record['submission_hash']
    retry=json.loads(urlopen(Request(base+'/api/reviews/submit',data=json.dumps(body).encode(),headers=headers)).read())
    assert retry['idempotent'] and retry['record']['record_id']==rid
    with closing(read(OUT/'baseline.sqlite3')) as original,closing(read(GUI/'ISOLATED.sqlite3')) as db:
        db.row_factory=sqlite3.Row
        assert audit(db)==before
        assert audit(db,projection(original),row_limits(original))==audit(original)
        check_integrity(db);recent=recent_updates(db)
        cases={name:SubmissionService._record(db,db.execute('SELECT record_id FROM play_records WHERE session_id=?',(case['session_id'],)).fetchone()[0],include_evidence=False) for name,case in manifest['cases'].items()}
        expected=dict(first='DOUBLE_UPDATE',score='SCORE_UPDATE',lamp='LAMP_UPDATE',failed='SCORE_UPDATE',saved='NO_UPDATE',double='DOUBLE_UPDATE',reduced='DOUBLE_UPDATE')
        assert {name:r['update_summary']['kind'] for name,r in cases.items()}==expected
        assert cases['failed']['max_score'] is None and cases['failed']['percentage'] is None
        assert {(r['record_id'],r['difficulty']) for r in recent['updates'] if r['song_id']==manifest['song_id']}=={(cases['double']['record_id'],'HARD'),(cases['reduced']['record_id'],'NORMAL')}
    report=dict(scope='generated isolated submission and real browser acceptance; not live game',cases=cases,recent=recent,
                http_retry_idempotent=True,all_original_rows_unchanged=True,retry_does_not_add_record_or_event=True,audit=before)
    (OUT/'gui-audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print('Generated GUI sequence, same HTTP retry, old-row preservation, independent bests and Recent checks passed.')


if __name__=='__main__':main()
