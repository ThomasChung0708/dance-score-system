"""Read small UI numbers from pixels, with two agreeing high-confidence views.

No dictionary of songs, expected numbers, catalog values or temporal confidence
boosts is used. The page reading and every additional model reading are retained.
"""
import copy
import hashlib
import re
from .ocr_screens import value


def numeric(text, decimal=False):
    pattern=r'\d{1,7}(?:\.\d{1,4})?' if decimal else r'\d{1,10}'
    return (float(text) if decimal else int(text)) if re.fullmatch(pattern,text) else None


def refine_number(image,engine,box,original,*,style='white',decimal=False):
    """Return (field, diagnostics); NULL and contradictions remain reviewable."""
    if original.get('value') is not None and original.get('confidence',0)>=.95 and not original.get('reason'):
        return original,[]
    recognize=getattr(engine,'recognize_line',None)
    if recognize is None:return original,[]
    import cv2
    import numpy as np
    from PIL import Image,ImageOps
    crop=image.crop(box).convert('RGB');a=np.array(crop).astype('int16');r,g,b=[a[:,:,i]for i in range(3)]
    attempts=[];accepted=[]
    for threshold,padding in ((175,5),(180,8)):
        mask=((g-r>30)&(b-r>20)&(g>threshold-45)) if style=='cyan' else ((a.min(axis=2)>threshold)&(a.max(axis=2)-a.min(axis=2)<65))
        _,_,stats,_=cv2.connectedComponentsWithStats(mask.astype('uint8'),8)
        components=[s for s in stats[1:] if s[4]>=3 and s[3]>=5]
        if not components:
            attempts.append(dict(region=box,threshold=threshold,padding=padding,reason='no_numeric_foreground',tokens=[]));continue
        x=min(s[0]for s in components);y=min(s[1]for s in components)
        right=max(s[0]+s[2]for s in components);bottom=max(s[1]+s[3]for s in components)
        view=Image.fromarray(np.where(mask,0,255).astype('uint8')).convert('RGB').crop((x,y,right,bottom))
        view=ImageOps.expand(view,border=padding,fill='white')
        digest=hashlib.sha256(view.tobytes()+str(view.size).encode()).hexdigest()
        origin=(int(box[0]+x-padding),int(box[1]+y-padding))
        tokens=recognize(view.resize((view.width*3,view.height*3)),origin=origin,scale=3)
        n=numeric(tokens[0]['text'].strip(),decimal) if len(tokens)==1 else None
        confidence=tokens[0]['confidence'] if len(tokens)==1 else 0
        attempt=dict(region=box,glyph_box=[int(box[0]+x),int(box[1]+y),int(box[0]+right),int(box[1]+bottom)],
                     threshold=threshold,padding=padding,style=style,view_sha256=digest,tokens=tokens,value=n,confidence=confidence)
        attempts.append(attempt)
        if n is not None and confidence>=.95:accepted.append(attempt)
    if len(accepted)<2 or len({a['view_sha256']for a in accepted})<2:return original,attempts
    choices={a['value'] for a in attempts if a.get('value') is not None and a.get('confidence',0)>=.9}
    if len(choices)!=1 or (original.get('value') is not None and original['value'] not in choices):
        field=copy.deepcopy(original);field['reason']='numeric_roi_conflict_requires_review'
        return field,attempts
    n=accepted[0]['value'];tokens=[a['tokens'][0]for a in accepted]
    field=value(n,tokens,method='numeric_roi_two_views')
    field.update(original_reading=copy.deepcopy(original),roi_views=copy.deepcopy(attempts))
    # value() uses the minimum actual model confidence, never a repeated-read boost.
    return field,attempts


def footer_level_box(field):
    """Remove the colored LV prefix using its observed OCR anchor, not its value."""
    evidence=field.get('evidence') or []
    if not evidence:return None
    t=evidence[-1];xs=[p[0]for p in t['box']];ys=[p[1]for p in t['box']]
    text=re.sub(r'\s+','',t['text']).upper()
    m=re.fullmatch(r'LV[:：]?(\d+)',text)
    left=min(xs)
    if m:left+=(max(xs)-left)*(len(text)-len(m[1]))/len(text)
    elif not text.isdigit():return None
    return (int(left),max(740,int(min(ys))-2),int(max(xs))+3,min(800,int(max(ys))+3))
