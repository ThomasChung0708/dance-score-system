"""Numeric evidence and conservative temporal/upgrade guards on isolated data."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image,ImageDraw
import test_result_review as fixture
import test_phase5c as auto_fixture
from dance_results.ocr_numeric import refine_number,footer_level_box,numeric
from dance_results.ocr_temporal import ResultConsistency,issues
from dance_results.monitor_ocr import image_hash
from dance_results.monitor_tracking import atomic_json
from dance_results.result_ocr import parse_result
from dance_results.verified_auto import AutomaticStorage,automatic_request


class Engine:
    def __init__(self,readings):self.readings=iter(readings);self.calls=0
    def recognize_line(self,image,**kwargs):
        self.calls+=1;text,confidence=next(self.readings)
        return [fixture.token(text,10,10,confidence)]


class NumericTests(unittest.TestCase):
    def image(self):
        im=Image.new('RGB',(80,40),'#504050');ImageDraw.Draw(im).rectangle((30,12,35,27),fill='white');return im
    def original(self,value=None,confidence=0):return dict(value=value,confidence=confidence,evidence=[],reason=None)
    def read(self,answers,original=None):return refine_number(self.image(),Engine(answers),(0,0,80,40),original or self.original())

    def test_two_strong_views_keep_model_minimum_and_original_null(self):
        f,v=self.read([('1',.99),('1',.96)])
        self.assertEqual((f['value'],f['confidence']),(1,.96));self.assertIsNone(f['original_reading']['value']);self.assertEqual(len(v),2)

    def test_repeated_low_confidence_never_becomes_reliable(self):
        f,v=self.read([('1',.89),('1',.89)])
        self.assertIsNone(f['value']);self.assertEqual(len(v),2)

    def test_zero_is_a_model_reading_not_a_null_default(self):
        self.assertEqual(self.read([('0',.99),('0',.99)])[0]['value'],0)
        im=Image.new('RGB',(80,40),'black');eng=Engine([])
        f,_=refine_number(im,eng,(0,0,80,40),self.original());self.assertIsNone(f['value']);self.assertEqual(eng.calls,0)

    def test_disagreeing_views_or_page_value_are_not_selected_by_confidence(self):
        for readings,original in [([('1',.99),('7',.98)],self.original()),([('1',.99),('1',.98)],self.original(4,.7))]:
            f,_=self.read(readings,original);self.assertEqual(f['value'],original['value']);self.assertEqual(f['reason'],'numeric_roi_conflict_requires_review')

    def test_letters_and_negative_values_are_not_repaired_to_digits(self):
        for text in ('I','l','O','-1','1 2'):
            f,_=self.read([(text,.99),(text,.99)]);self.assertIsNone(f['value'])
        self.assertEqual(numeric('132.5',True),132.5);self.assertIsNone(numeric('132.5'))

    def test_strong_existing_field_is_unchanged_and_not_reread(self):
        original=self.original(0,.99);eng=Engine([]);f,v=refine_number(self.image(),eng,(0,0,80,40),original)
        self.assertIs(f,original);self.assertEqual(v,[]);self.assertEqual(eng.calls,0)

    def test_level_crop_requires_observed_lv_or_numeric_anchor(self):
        self.assertIsNone(footer_level_box(dict(evidence=[])))
        self.assertIsNone(footer_level_box(dict(evidence=[fixture.token('人物等級',300,780)])))
        self.assertIsNotNone(footer_level_box(dict(evidence=[fixture.token('LV:10',300,780)])))


class TemporalTests(unittest.TestCase):
    def observation(self,color='gray'):
        image=Image.new('RGB',(1024,800),color);ocr=parse_result(fixture.result_tokens())
        return dict(state='RESULT',frame_hash=image_hash(image),result_ocr=ocr),image

    def test_single_or_identical_frame_does_not_count_twice(self):
        c=ResultConsistency();o,im=self.observation();c.observe(o,'a',1,im);c.observe(o,'a',2,im)
        self.assertEqual(o['result_ocr']['temporal_validation']['status'],'pending_review');self.assertEqual(len(c.samples),1)

    def test_two_distinct_high_confidence_frames_are_traceable(self):
        c=ResultConsistency();a,im=self.observation();b,im2=self.observation('navy')
        c.observe(a,'a',1,im);c.observe(b,'a',2,im2)
        ocr=b['result_ocr'];self.assertEqual(ocr['temporal_validation']['status'],'confirmed')
        with tempfile.TemporaryDirectory() as td:
            c.save(td,ocr);session=dict(session_id='a',result=dict(frame_hash=b['frame_hash']))
            self.assertEqual(issues(ocr,session,td),[])
            Path(td,'result-observations/frame-0.png').write_bytes(b'bad')
            self.assertIn('automatic_temporal_screenshot_invalid',issues(ocr,session,td))

    def test_low_confidence_null_or_conflicting_player_number_level_cannot_confirm(self):
        for key,value,confidence in [('miss',None,0),('cool',1,.7),('player_name','Host',.99),('game_level',18,.99),('perfect',99,.99)]:
            c=ResultConsistency();a,im=self.observation();b,im2=self.observation('navy')
            b['result_ocr']['fields'][key].update(value=value,confidence=confidence)
            c.observe(a,'a',1,im);c.observe(b,'a',2,im2)
            self.assertEqual(b['result_ocr']['temporal_validation']['status'],'pending_review')

    def test_new_session_screen_gap_and_stale_capture_reset_evidence(self):
        for change in ('session','screen','stale'):
            c=ResultConsistency();a,im=self.observation();b,im2=self.observation('navy');c.observe(a,'a',1,im)
            if change=='screen':c.observe(dict(state='PLAYING'),'a',2,im)
            c.observe(b,'b' if change=='session' else 'a',30 if change=='stale' else 3,im2)
            self.assertEqual(len(c.samples),1)


class AutomaticIntegrationTests(unittest.TestCase):
    setUp=auto_fixture.Phase5CTests.setUp
    tearDown=auto_fixture.Phase5CTests.tearDown
    store=auto_fixture.Phase5CTests.store
    official_auto=auto_fixture.Phase5CTests.official_auto

    def temporal_row(self):
        self.official_auto();c=ResultConsistency()
        for i,color in enumerate(('gray','navy'),1):
            image=Image.new('RGB',(1024,800),color);obs=dict(state='RESULT',frame_hash=image_hash(image),result_ocr=copy.deepcopy(self.ocr));c.observe(obs,self.sid,i,image)
        self.session['result']['frame_hash']=obs['frame_hash'];folder=self.output/'sessions'/self.sid
        atomic_json(folder/'session.json',self.session);c.save(folder,obs['result_ocr'])
        row=self.service._read(self.sid)[1];row.update(locked_session=copy.deepcopy(self.session),ocr=obs['result_ocr']);atomic_json(folder/'result-review.json',row)
        return row

    def test_new_verified_result_uses_existing_transaction_and_is_idempotent(self):
        row=self.temporal_row();self.assertEqual(automatic_request(self.service,row)[1],[])
        store=self.store();store.auto_allowed=lambda sid:True;a=store.submit_verified(self.sid);b=store.submit_verified(self.sid)
        self.assertTrue(b['idempotent']);self.assertEqual(a['record']['record_id'],b['record']['record_id'])

    def test_temporal_conflict_keeps_all_other_automatic_gates(self):
        row=self.temporal_row();row['ocr']['temporal_validation']['samples'][0]['fields']['miss']['value']=9
        self.assertIn('automatic_temporal_field_conflict',automatic_request(self.service,row)[1])
        row=self.temporal_row();row['ocr']['fields']['cool']['value']=None
        self.assertIn('automatic_unreliable_cool',automatic_request(self.service,row)[1])

    def test_new_parser_single_static_frame_cannot_bypass_consistency_gate(self):
        row=self.official_auto();row['ocr']['parser_version']='phase5f1-v1'
        row['ocr'].pop('temporal_validation', None)
        self.assertIn('automatic_temporal_confirmation_required',automatic_request(self.service,row)[1])

    def test_ocr_upgrade_excludes_old_pending_without_changing_its_evidence(self):
        self.official_auto();file=self.output/'sessions'/self.sid/'result-review.json';before=file.read_bytes()
        atomic_json(self.output/'storage-mode.json',dict(mode='VERIFIED_AUTO',catalog_path=str(self.db.resolve()),excluded_sessions=[],enabled_at='2026-10-27T00:00:00+00:00'))
        mode=AutomaticStorage(self.store())
        try:
            mode.queue.join();self.assertFalse(mode.allowed(self.sid));self.assertEqual(file.read_bytes(),before);self.assertEqual(mode.status()['events'],[])
        finally:mode.close()


if __name__=='__main__':unittest.main()
