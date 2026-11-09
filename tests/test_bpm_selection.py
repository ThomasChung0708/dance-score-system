"""BPM label/value geometry and acceptance completeness; no catalog policy change."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from PIL import Image
from dance_results.ocr_screens import bpm_field,parse_selection
from dance_results.monitor_ocr import Analyzer
from tools.selection_acceptance import selection_readiness


def token(text,left,top,right,bottom,confidence=.99):
    return dict(text=text,confidence=confidence,box=[[left,top],[right,top],[right,bottom],[left,bottom]])


class BPMTests(unittest.TestCase):
    def label(self,confidence=.99):return token('BPM',60,440,118,460,confidence)
    def number(self,text='147',confidence=.99):return token(text,124,442,163,462,confidence)

    def test_real_cached_roi_split_tokens_preserve_both_anchors(self):
        raw=json.loads((Path(__file__).parent/'fixtures/phase5f1_bpm_split.json').read_text(encoding='utf8'))
        f=parse_selection(raw['tokens'],Image.new('RGB',(1024,800)))['fields']['bpm']
        self.assertEqual((f['value'],f['confidence']),(132,.949))
        self.assertEqual([e['text']for e in f['evidence']],['BPM','132'])
        self.assertEqual(f['evidence'][0]['box'],[[67.,448.],[125.,448.],[125.,468.],[67.,468.]])

    def test_joined_formats_and_decimal_values_remain_supported(self):
        for text,wanted in [('BPM:147',147),('B P M ： 147.5',147.5),('BPM147',147),('BPM:147 音符數:1000',147)]:
            self.assertEqual(bpm_field([token(text,60,440,180,460)])['value'],wanted)

    def test_split_label_colon_and_decimal_are_geometric(self):
        for label in ('BPM','BPM:','B P M：'):
            f=bpm_field([dict(self.label(),text=label),self.number('147.5')])
            self.assertEqual(f['value'],147.5);self.assertEqual(f['confidence'],.99)
        self.assertEqual(bpm_field([self.label(),token(':',119,442,122,460),self.number()])['value'],147)

    def test_missing_value_or_number_without_label_is_not_filled(self):
        for tokens in ([],[self.label()],[self.number()],[self.label(),token('LV:10',200,440,260,460)]):
            self.assertIsNone(bpm_field(tokens)['value'])

    def test_next_row_notes_level_time_and_distant_numbers_never_pair(self):
        for other in (token('935',124,470,163,490),token('10',900,442,935,462),token('2:10',124,442,163,462),token('180',220,440,260,460)):
            self.assertIsNone(bpm_field([self.label(),other])['value'])

    def test_another_field_label_between_bpm_and_number_blocks_pair(self):
        self.assertIsNone(bpm_field([self.label(),token('LV',120,442,135,460),token('10',140,442,160,462)])['value'])

    def test_two_nearby_numbers_are_ambiguous_not_nearest_wins(self):
        f=bpm_field([self.label(),self.number(),token('935',164,442,190,462)])
        self.assertIsNone(f['value']);self.assertEqual(f['reason'],'bpm_pair_ambiguous')

    def test_low_confidence_is_not_raised_by_pairing(self):
        f=bpm_field([self.label(),self.number(confidence=.6)]);self.assertEqual(f['confidence'],.6)
        f=bpm_field([self.label(.4),self.number()]);self.assertEqual(f['confidence'],.4);self.assertEqual(f['reason'],'bpm_label_low_confidence')

    def test_low_label_does_not_trigger_numeric_reread(self):
        raw=json.loads((Path(__file__).parent/'fixtures/phase5f1_bpm_split.json').read_text(encoding='utf8'))
        tokens=copy.deepcopy(raw['tokens'])
        next(t for t in tokens if t['text']=='BPM')['confidence']=.4
        class CachedEngine:
            def recognize(self,image):return tokens
        with patch('dance_results.ocr_numeric.refine_number') as refine:
            result=Analyzer(CachedEngine()).analyze(Image.new('RGB',(1024,800)))
        refine.assert_not_called();self.assertEqual(result['selection']['fields']['bpm']['confidence'],.4)

    def test_competing_joined_and_split_values_do_not_choose_a_winner(self):
        f=bpm_field([token('BPM:150',60,415,180,435),self.label(),self.number()])
        self.assertIsNone(f['value']);self.assertEqual(f['reason'],'bpm_observations_ambiguous')


class AcceptanceTests(unittest.TestCase):
    def selection(self):
        return dict(fields={k:dict(value=v,confidence=.99,reason=None)for k,v in dict(title='Other song',difficulty='NORMAL',game_level=12,total_notes=1000,bpm=147).items()},matching=dict(status='confirmed_official_catalog',confirmed_song_id=7))

    def test_confirmed_identity_is_not_complete_with_missing_bpm(self):
        s=self.selection();s['fields']['bpm']['value']=None;r=selection_readiness(s)
        self.assertTrue(r['matching_identity_confirmed']);self.assertFalse(r['complete_selection_observation']);self.assertIn('bpm_missing_or_invalid',r['reasons'])

    def test_low_confidence_and_other_missing_metadata_are_incomplete(self):
        for key in ('title','difficulty','game_level','total_notes','bpm'):
            s=self.selection();s['fields'][key]['confidence']=.94
            self.assertFalse(selection_readiness(s)['complete_selection_observation'])

    def test_metadata_and_matching_have_separate_statuses(self):
        s=self.selection();self.assertTrue(selection_readiness(s)['complete_selection_observation'])
        s['matching']={};r=selection_readiness(s)
        self.assertTrue(r['observed_metadata_complete']);self.assertFalse(r['matching_identity_confirmed']);self.assertFalse(r['complete_selection_observation'])


if __name__=='__main__':unittest.main()
