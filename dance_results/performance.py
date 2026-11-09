"""Read-only presentation of immutable achievement evidence, never best inference."""
from .achievements import RANK


def update_summary(record, feedback, *, eligible=True):
    """Normalize v1-v3 saved feedback without reconstructing missing old bests."""
    result=dict(kind='UNAVAILABLE',label='更新證據未保存',score=None,lamp=None,badges=[],
                source=None,reason=None,historical=True)
    if not isinstance(feedback,dict):return result
    result.update(kind='NO_UPDATE',label='NO UPDATE',source='play_records.evidence_json.achievement_feedback')
    if not eligible or feedback.get('comparison_paused') or feedback.get('new_comparison_group'):
        result['reason']='comparison_paused' if feedback.get('comparison_paused') else 'comparison_unproven'
        return result
    events=feedback.get('events',[])
    if not isinstance(events,list):return result
    old=feedback.get('old_best_score');current=feedback.get('best_ex_score')
    score=(feedback.get('new_score') is True and 'NEW_SCORE' in events and
           type(current) is int and current>=0 and current==record.get('ex_score') and
           (old is None or type(old) is int and 0<=old<current))
    if score and old is not None:
        score=feedback.get('score_delta')==current-old
    typed=feedback.get('score_update')
    if score and typed is not None:
        score=(isinstance(typed,dict) and typed.get('type')=='SCORE_UPDATE' and
               typed.get('previous')==old and typed.get('current')==current and
               typed.get('improvement')==(current-old if old is not None else None))
    if score:
        result['score']=dict(previous=old,current=current,delta=current-old if old is not None else None,
                             first=old is None,label='FIRST BEST' if old is None else 'NEW SCORE')
    old_lamp=feedback.get('old_best_lamp');new_lamp=feedback.get('best_lamp')
    lamp=(feedback.get('new_lamp') is True and 'NEW_LAMP' in events and
          isinstance(old_lamp,str) and isinstance(new_lamp,str) and old_lamp in RANK and new_lamp in RANK and new_lamp==record.get('lamp_status') and
          RANK[new_lamp]>RANK[old_lamp])
    typed=feedback.get('lamp_update')
    if lamp and typed is not None:
        lamp=(isinstance(typed,dict) and typed.get('type')=='LAMP_UPDATE' and
              typed.get('previous')==old_lamp and typed.get('current')==new_lamp)
    if lamp:
        result['lamp']=dict(previous=old_lamp,current=new_lamp,first=old_lamp=='UNPLAYED',
                            label='FIRST LAMP' if old_lamp=='UNPLAYED' else 'NEW LAMP')
    result['badges']=[result[k]['label'] for k in ('score','lamp') if result[k]]
    if score and lamp:result.update(kind='DOUBLE_UPDATE',label='DOUBLE UPDATE')
    elif score:result.update(kind='SCORE_UPDATE',label=result['score']['label'])
    elif lamp:result.update(kind='LAMP_UPDATE',label=result['lamp']['label'])
    return result


def score_status(record):
    """Main UI only exposes reliable MAX. Raw historical values stay in details."""
    paused=bool((record.get('comparison') or {}).get('ranking_paused'))
    maximum=record.get('max_score')
    reliable=record.get('max_status')=='confirmed' and type(maximum) is int and maximum>0 and not paused
    return dict(ex_score=record.get('ex_score'),max_score=maximum if reliable else None,
                display=record.get('display') if reliable else None,
                max_policy=record.get('max_policy'),max_verified=reliable,
                label='比較暫停' if paused else 'MAX 未確認' if not reliable else record.get('max_label'),
                comparison_paused=paused)
