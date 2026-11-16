"""Bounded, session-scoped consistency evidence; never increases OCR confidence."""
import copy
import hashlib
import math
from pathlib import Path
from .result_ocr import incomplete_result
from .ocr_policy import AUTO_CONFIDENCE, result_required
from .ocr_matching import same_song_title_key

POLICY='phase5f1-two-fresh-results-v1'
MINOR_FIELDS = ('completion_status', 'player_name')
TITLE_AGREEMENT_FLOOR = .90


def strict(field):
    return (isinstance(field, dict) and field.get('value') is not None and field.get('confidence', 0) >= AUTO_CONFIDENCE
            and not field.get('reason'))


def readable_minor(field):
    return (isinstance(field, dict) and field.get('value') is not None and field.get('confidence', 0) >= .9
            and field.get('reason') in (None, 'low_confidence'))


def agrees(key, first, second):
    if key == 'title':
        return bool(same_song_title_key(first)) and same_song_title_key(first) == same_song_title_key(second)
    return first == second


def anchored_field(key, field, samples):
    floor = TITLE_AGREEMENT_FLOOR if key == 'title' else .9
    return (key in (*MINOR_FIELDS, 'title') and field.get('value') is not None
            and field.get('confidence', 0) >= floor
            and field.get('reason') in (None, 'low_confidence')
            and len(samples) == 2
            and all(agrees(key, s.get('fields', {}).get(key, {}).get('value'), field['value']) for s in samples)
            and any(strict(s.get('fields', {}).get(key, {})) for s in samples))


def pair_decision(ocr, samples):
    """Recomputed at capture and submission; never alters source confidences."""
    reasons = [] if len(samples) == 2 else ['two_distinct_result_frames_required']
    grade_fields = [s.get('fields', {}).get('grade', {}) for s in samples]
    strong_grades = [f for f in grade_fields if strict(f)]
    grade_choice = None
    if (len(samples) == 2 and len(strong_grades) == 1
            and all(f.get('value') and f.get('reason') in (None, 'low_confidence') for f in grade_fields)
            and grade_fields[0]['value'] != grade_fields[1]['value']):
        grade_choice = strong_grades[0]
    for sample in samples:
        for key in keys(ocr, samples):
            field = sample.get('fields', {}).get(key, {})
            if key == 'grade' and grade_choice:
                continue
            if not strict(field) and not anchored_field(key, field, samples):
                reasons.append('unreliable_' + key)
            if not agrees(key, field.get('value'), ocr['fields'].get(key, {}).get('value')):
                reasons.append('conflicting_' + key)
    # A grade override never rescues disagreement in any other required field.
    return sorted(set(reasons)), grade_choice if not reasons else None


def reliable_result_field(ocr, key, field=None):
    field = ocr.get('fields', {}).get(key, {}) if field is None else field
    if strict(field):
        return True
    evidence = ocr.get('temporal_validation') or {}
    return (evidence.get('status') == 'confirmed'
            and anchored_field(key, field, evidence.get('samples') or []))


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
        samples = copy.deepcopy(self.samples)
        reasons, grade_choice = pair_decision(ocr, samples)
        if grade_choice:
            original = copy.deepcopy(ocr['fields']['grade'])
            ocr['fields']['grade'] = copy.deepcopy(grade_choice)
            # The local row and aggregate fields must still describe the same
            # accepted reading. Other players' rows are never changed.
            for row in ocr.get('player_rows', []):
                if row.get('name') == ocr['fields'].get('player_name', {}).get('value'):
                    row['fields']['grade'] = copy.deepcopy(grade_choice)
            ocr['grade_resolution'] = dict(
                rule='one_reliable_grade_with_all_other_fields_agreeing',
                previous=original, chosen=copy.deepcopy(grade_choice))
        status = 'pending_review' if reasons else 'confirmed'
        ocr['temporal_validation'] = dict(
            policy=POLICY, session_id=session_id, status=status, reasons=reasons, samples=samples,
            confidence_rule='original model confidence; anchored title floor 0.90; unique strong grade')
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
    reasons, grade_choice = pair_decision(ocr, samples)
    if reasons:
        errors.append('automatic_temporal_field_conflict')
    if grade_choice and ocr['fields'].get('grade') != grade_choice:
        errors.append('automatic_temporal_field_conflict')
    for i,s in enumerate(samples):
        if not isinstance(s.get('capture_id'),(float,int)) or not math.isfinite(s['capture_id']):
            errors.append('automatic_temporal_capture_invalid')
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
