"""Contrasting profiles, source isolation and selection API checks."""
import copy; import json; import unittest
from urllib.request import urlopen
from test_lab import fixture, close
import resources.contrast_profiles as cp
import resources.profile_retrieval as profile_retrieval
import app.app_server as app_server

class TrustContrastTests(unittest.TestCase):
    def test_same_format_background_and_event_triggers(self):
        _,low=cp.read_profile('trust_011_guarded');_,high=cp.read_profile('trust_011_open')
        def shape(v):
            if isinstance(v,dict):return {k:shape(x) for k,x in v.items()}
            if isinstance(v,list):return [shape(x) for x in v]
            return type(v).__name__
        self.assertEqual(shape(low),shape(high))
        self.assertEqual(low['demographic_context'],high['demographic_context'])
        self.assertEqual([s['situation'] for s in low['situations']],[s['situation'] for s in high['situations']])
        self.assertNotEqual(low['core_belief'],high['core_belief'])
        self.assertNotEqual(low['situations'][3]['emotion'],high['situations'][3]['emotion'])
    def test_source_lookup_uses_selected_profile(self):
        for case in cp.CASES:
            path,profile=cp.read_profile(case)
            for tool,field in [('retrieve_situation','situations'),('retrieve_childhood','childhood_data')]:
                db=profile_retrieval.prepare({'_experience_case_id':case},tool,{'reason':'신뢰 근거를 확인한다','query':'상담'})
                self.assertEqual(db['profile_id'],profile['profile_id']);self.assertEqual(db['source'],str(path))
                self.assertEqual(db['database'],field)
                if field=='situations':self.assertEqual(db['records'][-1]['emotion'],profile['situations'][-1]['emotion'])
    def test_ui_catalog_no_history_contamination_and_old_endpoints_preserved(self):
        server,model,temp=fixture()
        def get(path):return json.load(urlopen(f'http://127.0.0.1:{server.server_port}'+path))
        try:
            self.assertEqual(len(get('/api/profiles/group1')['profiles']),5)
            self.assertEqual(len(get('/api/histories/group1')['profiles']),5)
            self.assertEqual(len(get('/api/profiles')['profiles']),7)
            profiles=get('/api/histories')['profiles'];self.assertEqual(len(profiles),7)
            for entry in profiles[-2:]:
                h=entry['histories'][0];s=h['scenario'];self.assertEqual(s['dialogue'],[])
                self.assertEqual(s['example_counselor_utterance'],cp.OPENING)
                self.assertNotIn('dialogue_turns',s)
                _,original=cp.read_profile(s['id'])
                self.assertEqual(entry['source_profile'],profile_retrieval.initial_profile(original,'full'))
                self.assertEqual(s['source_notes']['profile_source'],'resources/profiles/'+cp.CASES[s['id']]['path'])
        finally:close(server,model,temp)

if __name__=='__main__':unittest.main()
