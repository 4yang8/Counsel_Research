import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from data import discover, extract, packet, write
from schema import validate
from report import measure, generate
from run import cli_command, read_events, judge, implementation


def fixture(root,bid='b1',date='2026-11-11T01:00:00+00:00',status='completed',counselor='P3rs'):
    rows=[]
    for branch in ('common','provided','withheld'):
        sid=bid+'_'+branch
        rows.append({'profile_id':'p1','profile_hash':'h','branch':branch,'version':'Q3compare_memory',
                     'session_id':sid,'status':'completed','target_turns':2})
        if branch=='common':continue
        public=[{'role':role,'content':f'{role} {t}'} for t in range(1,4) for role in ('counselor','client')]
        records=[{'turn':t,'utterance':f'client {t}','counselor_utterance':f'counselor {t}',
                  'inner':{'private_position':{'withheld_content':'불편하다'}},'memory_input':[]}
                 for t in range(1,4)]
        storage=root/'runs/paired'/sid/'client_state'
        write(storage/'Q3compare_memory'/sid/'memory_state.json',{'records':records})
        steps=[{'actor':u['role'],'utterance':u['content'],'usage':{'total_tokens':10}} for u in public[1:]]
        steps[1]['rupture']={'move':'interpret'} # common counselor turn 2
        steps[3]['rupture']={'move':'check_in','check':{'is_check':True,'counselor_turn':2,'valid':True,'gap':0}}
        write(root/'runs/paired'/sid/'session.json',{'client_storage_dir':str(storage),'client_version':'Q3compare_memory',
              'counselor_version':counselor,'public_dialogue':public,'steps':steps,
              'compare':{'fork_turn':2,'branch':branch},'settings':{}})
    path=root/'runs/profile_batches'/bid/'job.json'
    job={'id':bid,'created_at':date,'status':status,'mode':'compare','versions':['Q3compare_memory'],
         'request':{'counselor_version':counselor},'options':{'fork_turn':2,'turns':3},'rows':rows}
    write(path,job)
    return path,job


def public_answer(p):
    def ev(t,ch='client'):return [{'turn':t,'channel':ch,'quote':f'{ch} {t}'}]
    return {'packet_id':p['packet_id'],
            'counselor_actions':[{'turn':3,'action':'explore','reaction_check':'no','target_turn':None,'reason':'내용 탐색','evidence':ev(3,'counselor')}],
            'issues':[],'client_consistency':{'status':'consistent','reason':'충돌 없음','evidence':ev(2)},
            'naturalness':{'score':4,'reason':'연결됨','evidence':ev(3)},'limitations':[]}


class EvaluationTests(unittest.TestCase):
    def test_latest_is_created_not_mtime_and_excludes_failed(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)
            fixture(root,'older','2026-11-10T00:00:00+00:00')
            fixture(root,'newer','2026-11-11T00:00:00+00:00')
            fixture(root,'failed','2026-11-12T00:00:00+00:00','failed')
            fixture(root,'sub','2026-11-10T00:00:00+00:00',counselor='P3rs_sub')
            selected,excluded=discover(root)
            self.assertEqual({j['id'] for _,j in selected},{'newer','sub'})
            self.assertEqual(len(excluded),2)
            with self.assertRaises(ValueError):discover(root,batches=['failed'])

    def test_fork_alignment_is_not_step_index(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);selected=[fixture(root)]
            docs,_=extract(root,selected)
            self.assertEqual(len(docs),2)
            d=docs[0]
            self.assertEqual(d['scope'],{'client_start':2,'counselor_start':3})
            self.assertEqual(d['code']['labels'],{'check_in':1})
            self.assertEqual(d['code']['checks'][0]['turn'],3)
            self.assertEqual(d['code']['usage']['total_tokens'],30)
            pub=packet(d,'public','random');priv=packet(d,'private','random2')
            self.assertNotIn('inner',pub['turns'][0]);self.assertIn('inner',priv['turns'][0])
            for bad in ('provided','withheld','Q3compare_memory','P3rs','b1_provided'):
                self.assertNotIn(json.dumps(bad),json.dumps(priv))
            self.assertNotIn('branch',priv)
            # No fallback to an arbitrary attempt if the committed pointer is stale.
            sp=root/'runs/paired/b1_provided/session.json';s=json.loads(sp.read_text());s['client_storage_dir']='/missing';write(sp,s)
            with self.assertRaises(ValueError):extract(root,selected)

    def test_invalid_prefix_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);selected=[fixture(root)]
            path=root/'runs/paired/b1_withheld/client_state/Q3compare_memory/b1_withheld/memory_state.json'
            s=json.loads(path.read_text());s['records'][0]['inner']={'changed':True};write(path,s)
            with self.assertRaisesRegex(ValueError,'common prefix'):extract(root,selected)

    def test_evidence_and_turns_are_validated(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);docs,_=extract(root,[fixture(root)])
            p=packet(docs[0],'public','x');r=public_answer(p)
            validate(r,p,'public')
            r['naturalness']['evidence'][0]['quote']='invented'
            with self.assertRaises(ValueError):validate(r,p,'public')
            r=public_answer(p);r['counselor_actions']=[]
            with self.assertRaises(ValueError):validate(r,p,'public')

    def test_no_followup_is_not_zero_error(self):
        d={'fork_turn':2,'memory_input':{}}
        result=measure(d,private={'hidden_reactions':[{'turn':1,'status':'unvoiced'}],'followups':[]})
        self.assertEqual(result['contradiction'],(0,0))
        self.assertEqual(result['followup_any'],(0,1))
        self.assertEqual(result['hidden_any'],(1,1))

    def test_cli_uses_requested_model_and_no_tools(self):
        cmd=cli_command({'model':'gpt-6-luna','effort':'max'},Path('/tmp/a'),Path('/tmp/a/s'),Path('/tmp/a/o'))
        self.assertIn('gpt-6-luna',cmd);self.assertIn('model_reasoning_effort="max"',cmd)
        self.assertIn('features.shell_tool=false',cmd);self.assertIn('--ignore-user-config',cmd)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox',cmd)
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'events'
            p.write_text(json.dumps({'type':'item.completed','item':{'type':'command_execution'}}))
            with self.assertRaises(ValueError):read_events(p)

    def test_runner_retry_preserves_raw_then_resumes_without_call(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);docs,_=extract(root,[fixture(root)])
            run=root/'run';p=packet(docs[0],'public','task')
            write(run/'packets/task.json',p)
            write(run/'manifest.json',{'implementation':implementation()})
            prompts=run/'snapshot/prompts';prompts.mkdir(parents=True)
            (prompts/'common.md').write_text('common');(prompts/'public.md').write_text('public')
            task={'id':'task','kind':'public'}
            config={'model':'gpt-6-luna','effort':'max','retries':1,'timeout_seconds':10,'max_input_chars':10000}
            calls=[]
            def fake(command,**kw):
                calls.append(command)
                result=public_answer(p)
                if len(calls)==1:result['naturalness']['evidence'][0]['quote']='fabricated'
                write(Path(command[command.index('--output-last-message')+1]),result)
                kw['stdout'].write(json.dumps({'type':'turn.completed','usage':{'input_tokens':5}})+'\n')
                class P:returncode=0
                return P()
            with patch('run.subprocess.run',side_effect=fake):
                self.assertEqual(judge(run,task,config),'completed')
                self.assertEqual(judge(run,task,config),'cached')
            self.assertEqual(len(calls),2)
            self.assertTrue((run/'judgments/task/attempt_001/error.json').exists())
            self.assertTrue((run/'judgments/task/attempt_002/raw_answer.json').exists())
            status=json.loads((run/'judgments/task/status.json').read_text())
            self.assertEqual(status['usage']['input_tokens'],10)

    def test_report_incomplete_and_repeats_not_extra_dialogues(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);docs,batches=extract(root,[fixture(root)])
            run=root/'report';run.mkdir();tasks=[]
            for i,d in enumerate(docs):
                d['id']=str(i)
                for rep in range(2):
                    tid=f'{i}_{rep}';p=packet(d,'public',tid)
                    tasks.append({'id':tid,'document':str(i),'kind':'public','repeat':rep})
                    write(run/'packets'/f'{tid}.json',p)
                    if i==0:
                        write(run/'judgments'/tid/'result.json',public_answer(p))
                        write(run/'judgments'/tid/'status.json',{'status':'completed'})
            write(run/'normalized.json',docs)
            write(run/'manifest.json',{'tasks':tasks,'batches':batches,'config':{'repeats':2,'model':'mock','effort':'max'}})
            self.assertEqual(generate(run),2)
            summary=json.loads((run/'summary.json').read_text())
            a=next(x for x in summary['metrics'] if x['branch']=='provided' and x['metric']=='naturalness')
            b=next(x for x in summary['metrics'] if x['branch']=='withheld' and x['metric']=='naturalness')
            self.assertEqual(a['value'],4);self.assertEqual(a['n_evaluable_dialogues'],1)
            self.assertEqual(a['n_ratings'],2);self.assertIsNone(b['value'])
            self.assertTrue((run/'figures/outcomes.png').exists())


if __name__=='__main__':unittest.main()
