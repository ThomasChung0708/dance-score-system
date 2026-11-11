"""Bounded, session-scoped consistency evidence; never increases OCR confidence."""
import copy
import hashlib
import math
from pathlib import Path
from .result_ocr import incomplete_result
from .ocr_policy import AUTO_CONFIDENCE, result_required

POLICY='phase5f1-two-fresh-results-v1'
MINOR_FIELDS = ('completion_status', 'player_name')


def strict(field):
    return (isinstance(field, dict) and field.get('value') is not None and field.get('confidence', 0) >= AUTO_CONFIDENCE
            and not field.get('reason'))


def readable_minor(field):
    return (isinstance(field, dict) and field.get('value') is not None and field.get('confidence', 0) >= .9
            and field.get('reason') in (None, 'low_confidence'))


def reliable_result_field(ocr, key, field=None):
    """Minor readings may agree with a strong anchor; score thresholds stay fixed."""
    field = ocr.get('fields', {}).get(key, {}) if field is None else field
    if strict(field):
        return True
    evidence = ocr.get('temporal_validation') or {}
    samples = evidence.get('samples') or []
    return (key in MINOR_FIELDS and readable_minor(field) and evidence.get('status') == 'confirmed'
            and len(samples) == 2 and any(strict(s.get('fields', {}).get(key, {}))
            and s['fields'][key]['value'] == field['value'] for s in samples))


def keys(ocr, samples=()):
    fields = [ocr.get('fields', {})]
    fields.extend(sample.get('fields', {}) for sample in samples)
    optional = tuple(key for key in ('total_notes', 'result_status')
                     if any(row.get(key, {}).get('value') is not None for row in fields))
    return (*result_required(ocr), *optional)


class ResultConsistency:
    def __init__(self):
        self.session_id = None
        self.samples = []
        self.images = []
        self.confirmed = None

    def observe(self,observation,session_id,capture_id,image):
        if observation.get('state') not in ('RESULT','GAME_OVER') or not session_id:
            self.session_id = None
            self.samples = []
            self.images = []
            self.confirmed = None
            return
        if session_id!=self.session_id:
            self.session_id = session_id
            self.samples = []
            self.images = []
            self.confirmed = None
        ocr=observation.get('result_ocr')
        if not ocr:
            return
        if self.confirmed:
            # A later recognition cannot replace the accepted pair or its PNGs.
            # Submission still checks the current fields and result frame hash.
            ocr['temporal_validation'] = copy.deepcopy(self.confirmed)
            return
        if self.samples and (capture_id-self.samples[-1]['capture_id']>15 or capture_id<self.samples[-1]['capture_id']):
            self.samples = []
            self.images = []
        if incomplete_result(ocr):
            ocr['temporal_validation'] = dict(
                policy=POLICY, session_id=session_id, status='pending_review',
                incomplete=True, reasons=['incomplete_result_frame'], samples=copy.deepcopy(self.samples),
            )
            return
        if not any(s['capture_id']==capture_id or s['frame_hash']==observation['frame_hash'] for s in self.samples):
            self.samples.append(dict(capture_id=capture_id,frame_hash=observation['frame_hash'],
                                     fields=copy.deepcopy(ocr['fields']),raw_fields=copy.deepcopy(ocr.get('raw_fields',ocr['fields']))))
            self.images.append(image.copy());self.samples=self.samples[-2:];self.images=self.images[-2:]
        samples=copy.deepcopy(self.samples);status='confirmed';reasons=[]
        if len(samples)<2:reasons.append('two_distinct_result_frames_required')
        for sample in samples:
            for k in keys(ocr, samples):
                f=sample['fields'].get(k,{})
                anchored_minor = (k in MINOR_FIELDS and readable_minor(f)
                    and any(strict(s['fields'].get(k, {})) for s in samples))
                if not strict(f) and not anchored_minor:reasons.append('unreliable_'+k)
                if f.get('value')!=ocr['fields'].get(k,{}).get('value'):reasons.append('conflicting_'+k)
        if reasons:status='pending_review'
        ocr['temporal_validation']=dict(policy=POLICY,session_id=session_id,status=status,reasons=sorted(set(reasons)),samples=samples,
                                       confidence_rule='minimum actual model confidence; no temporal boost')
        if status == 'confirmed':
            self.confirmed = copy.deepcopy(ocr['temporal_validation'])

    def save(self,directory,ocr):
        """At most two original frames, only for the newly captured result."""
        folder=Path(directory)/'result-observations';folder.mkdir(exist_ok=True)
        for i,(sample,image) in enumerate(zip(ocr['temporal_validation']['samples'],self.images)):
            path=folder/f'frame-{i}.png'
            image.save(path);sample['path']='result-observations/'+path.name
            sample['sha256']=hashlib.sha256(path.read_bytes()).hexdigest()


def issues(ocr,session,directory):
    evidence=ocr.get('temporal_validation')
    if not evidence:
        return ['automatic_temporal_confirmation_required']
    errors=[];samples=evidence.get('samples',[])
    if evidence.get('policy')!=POLICY or evidence.get('session_id')!=session['session_id'] or evidence.get('status')!='confirmed':errors.append('automatic_temporal_confirmation_required')
    if len(samples)!=2:return sorted(set([*errors,'automatic_two_result_frames_required']))
    if len({s.get('frame_hash')for s in samples})!=2 or len({s.get('capture_id')for s in samples})!=2:errors.append('automatic_duplicate_temporal_frame')
    try:
        if not 0<samples[1]['capture_id']-samples[0]['capture_id']<=15:errors.append('automatic_temporal_frames_stale')
    except (KeyError,TypeError):errors.append('automatic_temporal_capture_invalid')
    if samples[-1].get('frame_hash')!=(session.get('result')or{}).get('frame_hash'):errors.append('automatic_temporal_result_frame_conflict')
    for i,s in enumerate(samples):
        if not isinstance(s.get('capture_id'),(float,int)) or not math.isfinite(s['capture_id']):errors.append('automatic_temporal_capture_invalid')
        for k in keys(ocr, samples):
            f=s.get('fields',{}).get(k,{})
            anchored_minor = (k in MINOR_FIELDS and readable_minor(f)
                and any(strict(row.get('fields', {}).get(k, {})) for row in samples))
            if (not strict(f) and not anchored_minor) or f.get('value')!=ocr['fields'].get(k,{}).get('value'):errors.append('automatic_temporal_field_conflict')
        try:
            expected=f'result-observations/frame-{i}.png'
            if s.get('path')!=expected:raise ValueError('path')
            file=Path(directory)/expected
            if file.is_symlink() or file.parent.is_symlink() or hashlib.sha256(file.read_bytes()).hexdigest()!=s.get('sha256'):raise ValueError('evidence')
            from PIL import Image
            from .monitor_ocr import image_hash
            with Image.open(file) as image:
                if image_hash(image.convert('RGB').resize((1024,800)))!=s.get('frame_hash'):raise ValueError('pixels')
        except (OSError,ValueError):errors.append('automatic_temporal_screenshot_invalid')
    return sorted(set(errors))
