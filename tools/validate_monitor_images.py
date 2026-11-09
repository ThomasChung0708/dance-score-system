"""Execute monitor OCR on original PNGs and record full/ROI accuracy and timing."""
from pathlib import Path
import json
import time

from PIL import Image

from dance_results.monitor_ocr import Analyzer
from dance_results.ocr_engine import LocalOCREngine


def main():
    labels=json.loads(Path('tests/fixtures/phase4a_ground_truth.json').read_text(encoding='utf8'))['images']
    analyzer=Analyzer(LocalOCREngine(Path('.ocr-models'),low_latency=True));rows=[]
    state_map={'song_select':'SONG_SELECT','gameplay':'PLAYING','waiting_room':'WAITING_ROOM','profile':'PROFILE',
               'results':'RESULT','login':'LOGIN','lobby':'LOBBY','spectating':'SPECTATING'}
    for name,expected in labels.items():
        with Image.open(Path('tests/專案測試圖片')/name) as image:
            for full in ([True,False] if expected['screen_type'] in ('song_select','gameplay') else [True]):
                started=time.perf_counter();r=analyzer.analyze(image,state_map[expected['screen_type']],full=full)
                wanted=state_map[expected['screen_type']]
                if expected.get('local_player_status')=='failed':wanted='GAME_OVER'
                checks=[{'field':'state','actual':r['state'],'expected':wanted,'correct':r['state']==wanted}]
                for key,value in expected.get('fields',{}).items():
                    actual=r.get('selection',{}).get('fields',{}).get(key,{}).get('value')
                    checks.append(dict(field=key,actual=actual,expected=value,correct=actual==value))
                rows.append(dict(image=name,full_requested=full,milliseconds=round((time.perf_counter()-started)*1000,2),
                                 roi_used=r['roi_mode'],fallback_reason=r.get('roi_fallback_reason'),checks=checks))
                print(name,'full' if full else 'ROI',rows[-1]['milliseconds'],'PASS' if all(c['correct'] for c in checks)else'FAIL',flush=True)
    output=Path('reports/phase4b');output.mkdir(exist_ok=True,parents=True)
    report={'test_kind':'original_static_screenshots_not_live_gameplay','runs':rows,
            'all_checks_passed':all(c['correct'] for r in rows for c in r['checks'])}
    (output/'original-image-validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    return 0 if report['all_checks_passed']else 2


if __name__=='__main__':raise SystemExit(main())
