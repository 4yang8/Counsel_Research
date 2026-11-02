"""Controlled memory inputs, real HTTP lifecycle and observer isolation (mock model)."""
import copy
import json
import unittest

import client.registry as registry
from client.claim_experiments import SPECS, condition
from client.claim_experiments.memory import make_view, private_content, CurrentOnlyMemory, selected_episodes
from client.Q3 import runtime as baseline
import test_memory_experiments as base
from test_memory_experiments import Model, post
from test_q3 import plan, progress
from test_memory_experiments import action, commit
from test_f_free_notes import query


def episode(turn=1):
    return {'turn':turn,'turn_id':'t'+str(turn),'counselor_utterance':'試してみますか',
        'utterance':'やってみます','appraisal':{'interpretation':'PRIVATE_ORIGINAL_INTENT',
        'immediate_feeling':'hesitant','relevant_evidence':'prior offer','uncertainty':''},
        'turn_goal':{'intention':'avoid disappointing counselor','grounds':'fear','uncertainty':''},
        'response_plan':{'action':'outward assent','reason':'preserve relationship'}}


class MemoryViewTests(unittest.TestCase):
    def test_same_intent_objects_without_explicit_binding_and_prose_control(self):
        records=[episode(1),episode(2)]
        linked=make_view(records,'intent_linked')
        unlinked=make_view(records,'intent_unlinked')
        self.assertEqual(unlinked['visible']['unbound_intents'],[private_content(r) for r in records])
        self.assertEqual(linked['selected_turns'],unlinked['selected_turns'])
        self.assertNotIn('turn_id',json.dumps(unlinked['visible']))
        self.assertNotIn('utterance',unlinked['visible'])
        self.assertIn('t1',make_view(records,'linked_prose')['visible']['paragraphs'][0])
        self.assertIn('PRIVATE_ORIGINAL_INTENT',make_view(records,'linked_prose')['visible']['paragraphs'][0])
        self.assertNotIn('PRIVATE_ORIGINAL_INTENT',json.dumps(make_view(records,'summary_only')['visible']))
        self.assertNotIn('PRIVATE_ORIGINAL_INTENT',json.dumps(make_view(records,'retrospective',[])['visible']))

    def test_archive_lookup_cannot_bypass_condition(self):
        note={'items':{'notes':[]},'records':[episode()]}
        for source in ('episodes','changes','trajectory'):
            args=query(source);result=CurrentOnlyMemory.lookup(note,args)
            self.assertEqual(result['status'],'blocked_by_condition')
            self.assertEqual(result['results'],[])
        self.assertEqual(CurrentOnlyMemory.lookup(note,query('current'))['available_completed_turns'],[])

    def test_selection_caps_whole_records_and_reports_omissions(self):
        records=[episode(i) for i in range(1,15)]
        records[-1]['appraisal']['interpretation']='x'*20000
        rows,omitted=selected_episodes(records)
        self.assertEqual(omitted,[14]);self.assertEqual(rows[0]['turn'],3)
        self.assertEqual(make_view(records,'intent_linked')['selected_turns'],make_view(records,'intent_unlinked')['selected_turns'])

    def test_versions_are_isolated_and_conditions_validated(self):
        runtimes=[registry.client_runtime(v) for v in SPECS]
        self.assertEqual(len({id(r.TOOLS) for r in runtimes}),len(SPECS))
        self.assertEqual(len({id(r.LOCK) for r in runtimes}),len(SPECS))
        self.assertNotIn('claim',baseline.specification())
        self.assertEqual(next(t for t in baseline.TOOLS if t['function']['name']=='retrieve_client_memory')['function']['parameters']['properties']['source']['enum'],['current','changes','episodes','trajectory'])
        with self.assertRaises(ValueError):condition('Q3p1',{'claim_memory_condition':'intent_unlinked'})
        with self.assertRaises(ValueError):condition('Q3p1',{'claim_memory_condition':[]})


class ClaimLifecycleTests(unittest.TestCase):
    setUp=base.MemoryExperiments.setUp
    tearDown=base.MemoryExperiments.tearDown
    get=base.MemoryExperiments.get
    create=base.MemoryExperiments.create
    step=base.MemoryExperiments.step
    notes=base.MemoryExperiments.notes

    def actions(self):
        Model.q2a_actions=[plan(),commit(True),action('update_client_notes',operations=[]),progress()]

    def test_q3p1_counsel_single_call_public_input_in_all_memory_conditions(self):
        self.check_single_prompt_counselor('Q3p1',('summary_only','intent_linked','linked_prose'))

    def test_q3p2_counsel_single_call_public_input_in_all_memory_conditions(self):
        self.check_single_prompt_counselor('Q3p2',('intent_linked','intent_unlinked','linked_prose'))

    def test_q3p4_counsel_memory_by_intervention_public_input_and_frozen_condition(self):
        spec=next(row for row in self.get('/api/paired/counselor')['versions'] if row['id']=='Q3p4_counsel')
        for memory_condition in ('summary_only','intent_linked'):
            for intervention in ('A','B'):
                with self.subTest(memory=memory_condition,intervention=intervention):
                    s=self.create('Q3p4',counselor_version='Q3p4_counsel',
                        claim_memory_condition=memory_condition,counselor_intervention_condition=intervention)
                    staged=plan();staged['arguments']['turn_goal']['grounds']='PRIVATE_P4_INTENT'
                    Model.q2a_actions=[staged,commit(True),action('update_client_notes',operations=[]),progress()]
                    events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
                    s=events[-1]['session']
                    record=self.notes(s)['records'][-1]
                    self.assertIn('PRIVATE_P4_INTENT',json.dumps(record))
                    self.assertEqual(len(record['claim_experiment']['auxiliary_calls']),1)
                    count=len(Model.requests)
                    # A mid-session request cannot change the frozen treatment.
                    events=[json.loads(line)['event'] for line in post(self.server,'/api/paired/step',{
                        'session_id':s['session_id'],'expected_step':len(s['steps']),
                        'counselor_intervention_condition':'B' if intervention=='A' else 'A'}).splitlines()]
                    self.assertEqual(events[-1]['type'],'done',events[-1])
                    self.assertEqual(len(Model.requests)-count,1)
                    request=Model.requests[-1]
                    self.assertEqual(request['messages'][0]['content'],spec['system_prompt'])
                    public='\n\n'.join(('내담자' if r['role']=='client' else '상담사')+': '+r['content'] for r in s['public_dialogue'])
                    self.assertEqual(request['messages'][1]['content'].rstrip(),
                        '## 이번 세션의 개입 조건\n\n'+intervention+'\n\n## 공개 대화\n\n'+public)
                    for marker in ('PRIVATE_P4_INTENT','CLAIM_OBSERVER_ONLY',memory_condition):
                        self.assertNotIn(marker,json.dumps(request))
                    self.assertNotIn('tools',request)
                    self.assertIsNone(events[-1]['session']['counselor_checkpoint'])
                    saved=self.get('/api/paired/session?session_id='+s['session_id'])
                    self.assertEqual(saved['settings']['counselor_intervention_condition'],intervention)
                    self.assertEqual(saved['settings']['claim_memory_condition'],memory_condition)
                    # Neither the treatment input nor the observer result is fed back to the client.
                    self.actions();events=self.step(saved)
                    self.assertEqual(events[-1]['type'],'done',events[-1])
                    main=[e['payload'] for e in events if e['type']=='request']
                    self.assertTrue(main)
                    for request in main:
                        self.assertNotIn('## 이번 세션의 개입 조건',json.dumps(request,ensure_ascii=False))
                        self.assertNotIn('CLAIM_OBSERVER_ONLY',json.dumps(request))

    def test_q3p4_counsel_default_and_invalid_conditions(self):
        from urllib.error import HTTPError
        s=self.create('Q3p4',counselor_version='Q3p4_counsel')
        self.assertEqual(s['settings']['counselor_intervention_condition'],'A')
        for value in ('','C',None,True,[],{}):
            with self.subTest(value=value),self.assertRaises(HTTPError) as error:
                self.create('Q3p4',counselor_version='Q3p4_counsel',counselor_intervention_condition=value)
            self.assertEqual(error.exception.code,400)
        self.assertEqual(Model.requests,[])

    def check_single_prompt_counselor(self,version,conditions):
        counselor=version+'_counsel'
        private_marker='PRIVATE_'+version.upper()+'_INTENT'
        spec=next(row for row in self.get('/api/paired/counselor')['versions'] if row['id']==counselor)
        for condition_name in conditions:
            with self.subTest(condition=condition_name):
                s=self.create(version,counselor_version=counselor,claim_memory_condition=condition_name)
                staged=plan()
                staged['arguments']['turn_goal']['grounds']=private_marker
                Model.q2a_actions=[staged,commit(True),action('update_client_notes',operations=[]),progress()]
                events=self.step(s)
                self.assertEqual(events[-1]['type'],'done',events[-1])
                s=events[-1]['session']
                self.assertIn(private_marker,json.dumps(self.notes(s)))
                count=len(Model.requests)
                events=self.step(s)
                self.assertEqual(events[-1]['type'],'done',events[-1])
                self.assertEqual(len(Model.requests)-count,1)
                request=Model.requests[-1]
                self.assertEqual(request['messages'][0]['content'],spec['system_prompt'])
                public='\n\n'.join(('내담자' if row['role']=='client' else '상담사')+': '+row['content'] for row in s['public_dialogue'])
                self.assertEqual(request['messages'][1]['content'].rstrip(),'## 공개 대화\n\n'+public)
                self.assertNotIn(private_marker,json.dumps(request))
                self.assertNotIn(condition_name,json.dumps(request))
                self.assertNotIn('tools',request)
                self.assertEqual(len([e for e in events if e['type']=='request']),1)
                saved=self.get('/api/paired/session?session_id='+s['session_id'])
                self.assertEqual(saved['counselor_version'],counselor)
                self.assertEqual(saved['settings']['counselor_system_prompt'],spec['system_prompt'])
                self.assertEqual(saved['settings']['claim_memory_condition'],condition_name)
                self.assertIsNone(saved['counselor_checkpoint'])

    def continue_session(self,s):
        s=self.get('/api/paired/session?session_id='+s['session_id'])
        return json.loads(post(self.server,'/api/paired/manual',{'session_id':s['session_id'],
            'expected_step':len(s['steps']),'actor':'counselor','content':'先ほどの同意について、今はどう感じますか。'}))['session']

    def test_registered_claim_versions_two_turns_owned_prompts_usage_and_observer_non_feedback(self):
        for version in SPECS:
            with self.subTest(version=version):
                s=self.create(version);self.actions()
                events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
                s=self.continue_session(s);self.actions()
                events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
                notes=self.notes(s);self.assertEqual(len(notes['records']),2)
                record=notes['records'][-1];experiment=record['claim_experiment']
                self.assertEqual(experiment['claim'],SPECS[version][0])
                self.assertEqual(experiment['memory_condition'],SPECS[version][2])
                self.assertEqual(record['note_selection']['selective'],False)
                main=[e for e in events if e['type']=='request']
                self.assertEqual(len(main),4)
                self.assertNotIn('CLAIM_OBSERVER_ONLY',json.dumps(record['model_requests']))
                self.assertNotIn('CLAIM_OBSERVER_ONLY',json.dumps(record['router_request']))
                count={'Q3p1':0,'Q3p2':0,'Q3p3':1,'Q3p4':1,'Q3p5':2,'Q3p6':0}[version]
                self.assertEqual(len(experiment['auxiliary_calls']),count)
                aux=sum(r['usage']['total_tokens'] for r in experiment['auxiliary_calls'].values())
                self.assertEqual(sum(u['total_tokens'] for u in record['agent_usage'].values()),650+aux)
                self.assertEqual(events[-1]['usage']['total_tokens'],650+aux)
                for event in main:
                    for group in event['context_manifest']['groups']:
                        if group['role']=='user':
                            self.assertEqual(''.join(p['value'] for p in group['parts'] if p['included']),event['payload']['messages'][group['message_index']]['content'])
                for kind in ('system','user','trait_router','trait_router_user','trajectory'):
                    self.assertEqual(self.get('/api/prompts/source?role=client&version='+version+'&kind='+kind)['version'],version)

    def test_retrospective_model_never_receives_committed_private_archive(self):
        s=self.create('Q3p3');self.actions();self.step(s)
        saved=self.get('/api/paired/session?session_id='+s['session_id'])
        runtime=registry.client_runtime('Q3p3')
        directory=runtime.directory({'_storage_dir':saved['client_storage_dir']},s['session_id'])
        path=next((directory/'appraisals').glob('*.json'))
        record=json.loads(path.read_text());record['appraisal']['interpretation']='PRIVATE_ARCHIVE_MARKER'
        path.write_text(json.dumps(record,ensure_ascii=False))
        s=self.continue_session(s);self.actions();events=self.step(s)
        self.assertEqual(events[-1]['type'],'done',events[-1])
        record=self.notes(s)['records'][-1]
        auxiliary=next(iter(record['claim_experiment']['auxiliary_calls'].values()))
        request=json.dumps(auxiliary['request'],ensure_ascii=False)
        self.assertNotIn('PRIVATE_ARCHIVE_MARKER',request)
        self.assertNotIn('state_notes',request)
        self.assertNotIn('PRIVATE_ARCHIVE_MARKER',json.dumps(record['model_requests']))
        self.assertEqual(record['claim_experiment']['memory_view']['visible']['provenance'],'retrospective_inference_not_original_intent')

    def test_control_conditions_change_actual_main_input(self):
        for version,mode,contains in [('Q3p1','summary_only',False),('Q3p1','intent_linked',True),
                                     ('Q3p2','intent_unlinked',True),('Q3p2','linked_prose',True),
                                     ('Q3p3','intent_linked',True)]:
            with self.subTest(version=version,mode=mode):
                s=self.create(version,claim_memory_condition=mode)
                self.actions();Model.q2a_actions[0]['arguments']['turn_goal']['intention']='CONTROL_PRIVATE_MARKER'
                self.assertEqual(self.step(s)[-1]['type'],'done')
                s=self.continue_session(s);self.actions();events=self.step(s)
                self.assertEqual(events[-1]['type'],'done',events[-1])
                rec=self.notes(s)['records'][-1]
                self.assertEqual('CONTROL_PRIVATE_MARKER' in json.dumps(rec['model_requests']),contains)
                self.assertEqual(rec['claim_experiment']['memory_condition'],mode)
                self.assertEqual(rec['claim_experiment']['auxiliary_calls'],{})

    def test_reconstructor_rejects_foreign_turn_and_keeps_previous_state(self):
        s=self.create('Q3p3');self.actions();self.step(s)
        s=self.continue_session(s);self.actions()
        Model.claim_aux_response={'claim_result':[{'turn':99,'status':'unknown',
            'inferred_intent':'','public_evidence':'','uncertainty':'cannot know'}]}
        try:events=self.step(s)
        finally:Model.claim_aux_response=None
        self.assertEqual(events[-1]['type'],'error')
        self.assertEqual(len(self.notes(s)['records']),1)

    def test_public_progress_observer_is_separate_from_informed_observer(self):
        s=self.create('Q3p5');self.actions();events=self.step(s)
        self.assertEqual(events[-1]['type'],'done',events[-1])
        observers=list(self.notes(s)['records'][0]['claim_experiment']['auxiliary_calls'].values())
        self.assertEqual([x['condition'] for x in observers],['public_only','intent_informed'])
        public=json.loads(observers[0]['request']['messages'][-1]['content'])
        informed=json.loads(observers[1]['request']['messages'][-1]['content'])
        self.assertEqual(set(public),{'dialogue_before','counselor_utterance','client_utterance'})
        self.assertIn('current_committed_judgment',informed)
        # Next actual counselor call must still see only the public transcript.
        s=self.get('/api/paired/session?session_id='+s['session_id'])
        Model.requests.clear()
        events=self.step(s);self.assertEqual(events[-1]['type'],'done',events[-1])
        self.assertTrue(Model.requests)
        wire=json.dumps(Model.requests)
        for hidden in ('CLAIM_OBSERVER_ONLY','historical_committed_episodes','current_committed_judgment','claim_experiment'):
            self.assertNotIn(hidden,wire)

    def test_observer_failure_rolls_back_entire_turn(self):
        s=self.create('Q3p4');self.actions()
        Model.claim_aux_response={'claim_result':{'bad':'invalid'}}
        try:events=self.step(s)
        finally:Model.claim_aux_response=None
        self.assertEqual(events[-1]['type'],'error')
        self.assertEqual(self.notes(s)['records'],[])
        self.assertEqual(self.get('/api/paired/session?session_id='+s['session_id'])['steps'],[])

    def test_condition_is_frozen_and_cannot_change_mid_session(self):
        s=self.create('Q3p1');self.actions();self.step(s)
        saved=self.get('/api/paired/session?session_id='+s['session_id'])
        self.assertEqual(saved['settings']['claim_memory_condition'],'summary_only')
        r=registry.client_runtime('Q3p1')
        cfg={'_storage_dir':saved['client_storage_dir'],'_paired_public_dialogue':saved['public_dialogue']}
        body={**saved['settings'],'session_id':s['session_id'],'messages':[{'role':'user','content':'次は？'}],
              'claim_memory_condition':'intent_linked'}
        with self.assertRaisesRegex(ValueError,'기억 조건'):
            r.context(cfg,body,self.server.runtime if hasattr(self.server,'runtime') else __import__('app.app_server',fromlist=['runtime']).runtime)


if __name__=='__main__':unittest.main()
