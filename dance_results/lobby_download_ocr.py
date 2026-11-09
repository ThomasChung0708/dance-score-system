"""Local roster badge observation; avatar level and global READY are not identity."""
from .ocr_screens import compact,center,in_region


def lobby_download(tokens,image,player_name,badge_tokens=None):
    roster=in_region(tokens,(30,165,995,250))
    own=[t for t in roster if compact(t['text'])==compact(player_name) and t['confidence']>=.95]
    names=[t for t in roster if 185<=center(t)[1]<=245 and compact(t['text']) not in ('HOST','READY','NOMAP')]
    result=dict(local_player_status='UNKNOWN',self_no_map=None,self_ready=None,progress_observed=False,
        progress_percent=None,player_name=player_name,slot=None,confidence=0,evidence=[],
        multiplayer_observed=False,progress_is_not_completion=True)
    if len(own)!=1:return result
    name=own[0];x,y=center(name);slot=int((x-55)//156)
    if not 0<=slot<6:return result
    # Require evidence of another occupied slot, not just six empty placeholders.
    others=[t for t in names if abs(center(t)[0]-x)>100 and abs(center(t)[1]-y)<12 and t['confidence']>=.9]
    badges=[t for t in in_region(tokens,(max(20,x-73),y-100,min(1000,x+73),y-15)) if compact(t['text']) in ('HOST','READY','NOMAP')]
    if badge_tokens:badges.extend(in_region(badge_tokens,(max(20,x-73),y-100,min(1000,x+73),y-15)))
    reliable=[t for t in badges if t['confidence']>=.9]
    result.update(local_player_status='CONFIRMED',slot=slot,confidence=name['confidence'],
        multiplayer_observed=bool(others),self_no_map=any(compact(t['text'])=='NOMAP' for t in reliable),
        self_ready=any(compact(t['text'])=='READY' for t in reliable),evidence=[name,*reliable])
    if reliable:result['confidence']=min(name['confidence'],max(t['confidence'] for t in reliable))
    if not reliable:result.update(self_no_map=None,self_ready=None)
    # Thin green strip confined to this player's card. A colour observation is
    # never a percentage or file-completeness assertion.
    green_rows=[]
    for yy in range(max(0,int(y)-30),max(0,int(y)-5)):
        count=sum(g>90 and g-r>35 and g-b>15 for r,g,b in [image.getpixel((xx,yy)) for xx in range(max(0,int(x)-65),min(1024,int(x)+65))])
        if count>=25:green_rows.append(yy)
    result['progress_observed']=bool(result['self_no_map'] and 1<=len(green_rows)<=8)
    result['progress_pixel_rows']=green_rows
    return result


def reread_own_badge(engine,image,tokens,player_name):
    own=[t for t in in_region(tokens,(30,180,995,245)) if compact(t['text'])==compact(player_name) and t['confidence']>=.95]
    if len(own)!=1 or not hasattr(engine,'recognize_line'):return []
    x,y=center(own[0]);box=(int(x)-65,int(y)-62,int(x)+65,int(y)-22)
    crop=image.crop(box)
    return engine.recognize_line(crop.resize((crop.width*4,crop.height*4)),origin=box[:2],scale=4)
