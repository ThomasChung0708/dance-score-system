"""Pixel-only numeric ROI experiments; original screenshots remain untouched."""
import json
from pathlib import Path
from PIL import Image,ImageOps
import numpy as np
import cv2
from dance_results.ocr_engine import LocalOCREngine
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/phase5f1';LIVE=ROOT/'reports/phase5f/live-gameplay-20261027-01'

def main():
    engine=LocalOCREngine(ROOT/'.ocr-models',low_latency=True);results=[]
    selection=next((LIVE/'selection-evidence').glob('*.png'))
    for name,path,box,style in [('miss',LIVE/'monitor/sessions/66f5e80e-c894-4103-9994-0778f785ebf9/result.png',(671,237,746,268),'white'),('level',LIVE/'monitor/sessions/66f5e80e-c894-4103-9994-0778f785ebf9/result.png',(303,757,348,797),'white'),('bpm',selection,(60,442,230,474),'cyan')]:
        im=Image.open(path).convert('RGB').resize((1024,800));crop=im.crop(box);pixels=np.array(crop).astype('int16');r,g,b=[pixels[:,:,i]for i in range(3)]
        mask=((g-r>30)&(b-r>20)&(g>130)) if style=='cyan' else ((pixels.min(axis=2)>175)&(pixels.max(axis=2)-pixels.min(axis=2)<65))
        count,labels,stats,centroids=cv2.connectedComponentsWithStats(mask.astype('uint8'),8)
        components=[s for s in stats[1:] if s[4]>=3 and s[3]>=5]
        if not components:continue
        x=min(s[0]for s in components);y=min(s[1]for s in components);right=max(s[0]+s[2]for s in components);bottom=max(s[1]+s[3]for s in components)
        tight=crop.crop((x,y,right,bottom));binary=Image.fromarray(np.where(mask,0,255).astype('uint8')).convert('RGB').crop((x,y,right,bottom))
        for variant,view in [('raw',tight),('mask',binary),('luma',ImageOps.autocontrast(tight.convert('L')).convert('RGB'))]:
            padded=ImageOps.expand(view,border=5,fill='white');v=engine.recognize_line(padded.resize((padded.width*3,padded.height*3)))
            row=dict(field=name,variant=variant,box=box,tight=[int(n)for n in (x,y,right,bottom)],readings=v);results.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
    (OUT/'numeric-probe.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf8')
if __name__=='__main__':main()
