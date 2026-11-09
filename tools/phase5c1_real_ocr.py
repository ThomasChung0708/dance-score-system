"""Rerun the existing engine on three unchanged original result PNGs."""
import hashlib
import json
from pathlib import Path
from time import perf_counter

from PIL import Image
from dance_results.ocr_engine import LocalOCREngine
from dance_results.result_ocr import recognize_result

ROOT=Path(__file__).resolve().parents[1]


def main():
    engine=LocalOCREngine(ROOT/'.ocr-models',low_latency=True)
    results=[]
    for sid,expected in [('944a2977-fe34-4112-a990-e864c317287c',[1145,30,7,10]),
                         ('6e7e3783-80b5-4a2e-8b21-23319ee8ef88',[1212,37,1,3]),
                         ('38f201bf-025a-4eae-9e73-a78d13fa9b3c',[835,3,0,0])]:
        path=ROOT/'data/monitor/sessions'/sid/'result.png'
        sha=hashlib.sha256(path.read_bytes()).hexdigest();start=perf_counter()
        result=recognize_result(Image.open(path),engine,'TC')
        seconds=perf_counter()-start
        values=[result['fields'][k]['value'] for k in ('perfect','cool','bad','miss')]
        assert sha==hashlib.sha256(path.read_bytes()).hexdigest()
        results.append(dict(session_id=sid,image=str(path),sha256=sha,seconds=seconds,
            expected_judgments=expected,judgments_match_expected=values==expected,result=result,
            test_scope='Existing original PNG replay, not a new live game or selection OCR test'))
    (ROOT/'reports/phase5c1/real-ocr.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps([dict(session=r['session_id'],seconds=round(r['seconds'],3),judgments_ok=r['judgments_match_expected']) for r in results]))


if __name__=='__main__':main()
