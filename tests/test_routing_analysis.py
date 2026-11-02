"""Routing denominators, snapshot replacement, provenance and missing-data handling."""
import copy
import json
import unittest
from pathlib import Path
from analysis.routing import build
from client.Q3.note_items import read_items, render_items
from test_lab import fixture,close,post,Model
import client.registry as registry

A='n111111111111';B='n222222222222';C='n333333333333';D='n444444444444'

def items(*ids):return {'notes':[{'id':i,'content':'요약\n'+i} for i in ids]}
def record(i,before,after,chosen,traits):
    return {'turn':i,'turn_id':'t'+str(i),'selected_traits':[{'trait_id':t,'content':t,'background_ids':['/childhood_data'],'backgrounds':[]} for t in traits],
        'notes_before':render_items(before),'notes_after':render_items(after),'note_items_after':after,
        'note_selection':{'selected_ids':chosen},'counselor_utterance':'상담사 '+str(i),'utterance':'내담자 '+str(i)}
def session(count):
    return {'session_id':'test','client_version':'Q3sb','settings':{'scenario':{'profile':{'traits':['태도 A','태도 B'],'childhood_data':'과거 경험'}},'generation':{},'comparison':{'batch_id':'batch'}},
        'steps':[{'actor':'client','turn_id':'t'+str(i),'number':i*2-1} for i in range(1,count+1)]}
def series():
    return [record(1,items(),items(A,B),[],['/traits/0']),record(2,items(A,B),items(A,B,C),[A],['/traits/1']),
            record(3,items(A,B,C),items(D),[A,C],['/traits/0','/traits/1']),record(4,items(D),items(D),[D],['/traits/0'])]
class RoutingStatistics(unittest.TestCase):
    def analyze(self,records=None,**kw):
        return build(session(4),[(r,Path('record.json')) for r in (series() if records is None else records)],read_items,**kw)
    def test_exact_denominators_and_background_deduplication(self):
        d=self.analyze();tot=d['totals'];self.assertEqual((tot['selected_notes'],tot['available_notes']),(4,6));self.assertAlmostEqual(tot['note_selection_rate'],4/6)
        self.assertEqual([(t['id'],len(t['selected_turns']),t['available_turns']) for t in d['traits']],[('/traits/0',3,4),('/traits/1',2,4)])
        self.assertEqual(d['backgrounds'][0]['selected_turns'],[1,2,3,4])
        self.assertEqual(tot['kind_candidates'],{'요약':6});self.assertEqual(tot['kind_distribution'],{'요약':4})
        self.assertEqual(sum(tot['age_candidates'].values()),6)
        self.assertEqual(d['turns'][2]['trait_change'],{'new':['/traits/0'],'kept':['/traits/1'],'dropped':[]})
    def test_empty_candidates_are_not_zero_percent(self):
        d=self.analyze();self.assertIsNone(d['turns'][0]['stats']['selection_rate'])
        self.assertEqual(d['turns'][1]['stats']['selection_rate'],.5)
    def test_replaced_ids_are_excluded_from_reselection_denominator(self):
        d=self.analyze();third=d['turns'][2]['stats'];fourth=d['turns'][3]['stats']
        self.assertEqual((third['eligible_again'],third['selected_again']),(1,1))
        self.assertEqual((fourth['eligible_again'],fourth['selected_again'],fourth['no_longer_available']),(0,0,2))
        self.assertEqual((d['totals']['eligible_again'],d['totals']['selected_again']),(1,1))
        self.assertEqual(d['totals']['reselection_rate'],1)
    def test_range_preserves_birth_age_and_actual_previous_turn(self):
        d=self.analyze(start=3,end=4);self.assertEqual(len(d['turns']),2)
        self.assertEqual([(n['id'],n['age']) for n in d['turns'][0]['notes']],[(A,2),(B,2),(C,1)])
        self.assertEqual((d['totals']['available_notes'],d['totals']['selected_notes']),(4,3))
        self.assertEqual(d['turns'][0]['stats']['eligible_again'],1)
    def test_copied_records_and_uncommitted_turns_are_not_counted(self):
        records=series();records=records+copy.deepcopy(records)+[record(9,items(),items(A),[],[])]
        d=self.analyze(records);self.assertEqual(d['totals']['turns'],4)
        self.assertEqual(d['totals']['selected_notes'],4)
    def test_missing_records_are_visible_and_not_interpolated(self):
        d=self.analyze([r for r in series() if r['turn']!=2]);self.assertEqual(d['session']['recorded_client_turns'],3)
        self.assertTrue(d['warnings']);self.assertIsNone(d['turns'][1]['trait_change']);self.assertIsNone(d['turns'][1]['stats']['eligible_again'])
    def test_missing_routing_field_is_not_an_empty_choice(self):
        records=series();del records[1]['note_selection'];d=self.analyze(records)
        self.assertEqual(d['totals']['notes_recorded'],3);self.assertIsNone(d['turns'][1]['stats']['selected'])
        self.assertEqual(d['totals']['available_notes'],4);self.assertIsNone(d['turns'][2]['stats']['eligible_again'])
    def test_unicode_multiline_and_same_id_content_history_are_preserved(self):
        records=series();records[0]['note_items_after']['notes'][0]['content']='요약\n  원문🙂\r\n다음 줄'
        records[0]['notes_after']=render_items(records[0]['note_items_after']);records[1]['notes_before']=records[0]['notes_after']
        d=self.analyze(records);self.assertEqual(d['turns'][1]['notes'][0]['content'],'요약\n  원문🙂\r\n다음 줄')
        self.assertEqual(len(next(n for n in d['notes'] if n['id']==A)['variants']),2)
    def test_seeded_note_has_unknown_creation_time(self):
        records=series();records[0]['notes_before']=render_items(items(A));records[0]['note_selection']['selected_ids']=[A]
        d=self.analyze(records);self.assertIsNone(d['turns'][0]['notes'][0]['age'])
        self.assertEqual(d['totals']['age_distribution']['시점 미상'],3)

class RoutingAPI(unittest.TestCase):
    def setUp(self):self.server,self.model,self.temp=fixture()
    def tearDown(self):close(self.server,self.model,self.temp)
    def test_catalog_api_and_invalid_key(self):
        from urllib.request import urlopen
        from urllib.error import HTTPError
        base='http://127.0.0.1:'+str(self.server.server_port)
        data=json.load(urlopen(base+'/api/routing/catalog'));self.assertEqual(data['sessions'],[])
        with self.assertRaises(HTTPError) as caught:urlopen(base+'/api/routing?key=../../etc/passwd')
        self.assertEqual(caught.exception.code,400)
        html=urlopen(base+'/routing').read().decode();self.assertIn('routing-results',html)
        self.assertIn(b'note_selection_rate',urlopen(base+'/routing.js').read())
    def test_live_records_api_matches_committed_record_count(self):
        from urllib.request import urlopen
        from test_recording_experiments import judgment
        from test_memory_experiments import commit
        base='http://127.0.0.1:'+str(self.server.server_port);cfg=json.load(urlopen(base+'/api/config'))
        Model.q2a_actions=[judgment(),commit(True)];Model.q1_router_response='{"selections":[],"note_ids":[]}'
        try:
            body={'experiment_version':'Q3x','counselor_version':'basic','scenario':cfg['scenario'],'generation':cfg['generation'],
                'system_prompt':registry.client_runtime('Q3x').specification()['system_prompt'],'messages':[{'role':'user','content':'지금 마음은 어떤가요?'}],
                'comparison':{'batch_id':'test-routing','schedule':'sequential','requested_utterances':1}}
            s=json.loads(post(self.server,'/api/paired/create',body));post(self.server,'/api/paired/step',{'session_id':s['session_id'],'expected_step':0})
            cat=json.load(urlopen(base+'/api/routing/catalog'));self.assertEqual(cat['sessions'][0]['batch_id'],'test-routing')
            d=json.load(urlopen(base+'/api/routing?key='+cat['sessions'][0]['key']));self.assertEqual(d['totals']['turns'],1);self.assertEqual(d['totals']['available_notes'],0)
            self.assertIsNone(d['totals']['note_selection_rate']);self.assertEqual(d['turns'][0]['stats']['after_count'],10)
        finally:Model.q2a_actions=None;Model.q1_router_response=None
if __name__=='__main__':unittest.main()
