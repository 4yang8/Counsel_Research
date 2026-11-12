"""Matrix scheduling and web evaluation integration, without paid LLM calls."""
import copy
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

from test_lab import fixture, close, post, ROOT
from test_cm_rs_variants import VariantModel
from runtime import profile_batch, batch_matrix, evaluation_jobs, job_ids


def workbench_fixture():
    server,model,temp=fixture(model_handler=VariantModel)
    target=Path(temp.name)/'eval'
    shutil.copytree(ROOT/'eval',target,ignore=shutil.ignore_patterns('results','web_jobs','__pycache__'))
    server.eval_patch=patch.object(evaluation_jobs,'EVAL',target)
    server.eval_patch.start()
    return server,model,temp


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        VariantModel.fail_review=False
        self.server,self.model,self.temp=workbench_fixture()
    def tearDown(self):
        for gid,worker in self.server.matrix_workers.items():
            batch_matrix.stop(self.server,gid);worker['thread'].join(15)
        for worker in self.server.batch_workers.values():
            worker['flag'].set();worker['thread'].join(15)
        for p in (evaluation_jobs.EVAL/'web_jobs').glob('*/job.json'):
            status=evaluation_jobs.read(p.parent.name)
            if status['status'] in ('running','starting','stopping'):
                (p.parent/'stop').touch()
        self.server.eval_patch.stop()
        close(self.server,self.model,self.temp)
    def get(self,path): return json.loads(urlopen(f'http://127.0.0.1:{self.server.server_port}'+path).read())
    def send(self,path,body): return json.loads(post(self.server,path,body))
    def body(self,**extra):
        return {'profile_directory':profile_batch.DEFAULT_DIRECTORY,'filenames':['gold_ccd_profile_001.json'],
                'clients':['Q3cm','Q3cm_split'],'counselors':['P3rs','P3rs_sub_gate'],
                'turns':3,'fork_turn':2,'concurrency':2,'retries':0,**extra}
    def wait(self,path,seconds=60):
        deadline=time.monotonic()+seconds
        while time.monotonic()<deadline:
            data=self.get(path)
            if data['status'] not in ('starting','running','stopping'): return data
            time.sleep(.1)
        self.fail('timeout: '+str(data))
    def generate(self,**extra):
        group=self.send('/api/batch_matrix/create',self.body(**extra))
        self.send('/api/batch_matrix/start',{'id':group['id']})
        return self.wait('/api/batch_matrix/job?id='+group['id'])

    def test_cartesian_product_shares_global_capacity_and_preserves_settings(self):
        lock=threading.Lock();current=0;peak=0
        original=profile_batch.advance
        def measured(*args,**kwargs):
            nonlocal current,peak
            with lock: current+=1;peak=max(peak,current)
            try:
                time.sleep(.02)
                return original(*args,**kwargs)
            finally:
                with lock: current-=1
        with patch.object(profile_batch,'advance',measured): group=self.generate()
        self.assertEqual(group['status'],'completed',group)
        self.assertEqual(peak,2)
        self.assertEqual(len(group['children']),4)
        self.assertEqual(len(set(c['id'] for c in group['children'])),4)
        self.assertEqual({(c['client'],c['counselor']) for c in group['children']},
                         {(c,s) for c in ['Q3cm','Q3cm_split'] for s in ['P3rs','P3rs_sub_gate']})
        self.assertEqual(group['turn_percent'],100)
        for child in group['children']:
            self.assertRegex(child['id'],r'^\d{4}_\d{6}(?:_\d+)?$')
            job=profile_batch.read(self.server,child['id'])
            self.assertEqual(job['options']['fork_turn'],2)
            self.assertEqual(job['options']['turns'],3)
            self.assertEqual(job['group_id'],group['id'])
            self.assertIn('counselor_system_prompt',job['request'])

    def test_invalid_matrix_is_rejected_before_any_jobs_created(self):
        with self.assertRaises(HTTPError): self.send('/api/batch_matrix/create',self.body(clients=['Q3cm','missing']))
        self.assertEqual(list((self.server.storage_dir/'profile_batches').glob('*/job.json')),[])
        with self.assertRaises(HTTPError): self.send('/api/batch_matrix/create',self.body(fork_turn=99))
        self.assertEqual(list((self.server.storage_dir/'batch_groups').glob('*/job.json')),[])

    def test_matrix_stop_resume_and_no_independent_child_restart(self):
        original=profile_batch.advance
        def slow(*a,**k): time.sleep(.1);return original(*a,**k)
        with patch.object(profile_batch,'advance',slow):
            group=self.send('/api/batch_matrix/create',self.body(turns=4))
            self.send('/api/batch_matrix/start',{'id':group['id']})
            time.sleep(.15)
            self.send('/api/batch_matrix/stop',{'id':group['id']})
            stopped=self.wait('/api/batch_matrix/job?id='+group['id'])
        self.assertEqual(stopped['status'],'cancelled')
        with self.assertRaises(HTTPError): self.send('/api/profile_batch/start',{'id':group['batches'][0]})
        self.send('/api/batch_matrix/start',{'id':group['id']})
        done=self.wait('/api/batch_matrix/job?id='+group['id'])
        self.assertEqual(done['status'],'completed',done)

    def test_eval_prepare_real_cli_graphs_and_target_freezing(self):
        group=self.generate(clients=['Q3cm'],counselors=['P3rs'])
        self.assertEqual(group['status'],'completed')
        bid=group['batches'][0]
        options=self.get('/api/evaluation/options')
        self.assertTrue(next(r for r in options['batches'] if r['id']==bid)['eligible'])
        job=self.send('/api/evaluation/create',{'selection':'latest','clients':['Q3cm'],'counselors':['P3rs'],
                                               'prepare_only':True,'workers':3,'repeats':2})
        done=self.wait('/api/evaluation/job?id='+job['id'])
        self.assertEqual(done['status'],'completed',done.get('log'))
        self.assertEqual(done['estimated_tasks'],8)
        self.assertEqual(done['config']['workers'],3)
        self.assertTrue(any(a['name'].endswith('.png') for a in done['artifacts']))
        self.assertEqual([b['id'] for b in done['batches']],[bid])
        raw=evaluation_jobs.json_read(evaluation_jobs.path(job['id'])/'job.json')
        self.assertIn('--storage-root',raw['command'])
        manifest=evaluation_jobs.json_read(evaluation_jobs.EVAL/'results'/raw['result_id']/'manifest.json')
        self.assertEqual(len(manifest['tasks']),8)
        self.assertFalse((evaluation_jobs.EVAL/'results'/raw['result_id']/'judgments').exists())
        with self.assertRaises(HTTPError): self.get('/api/evaluation/artifact?id='+job['id']+'&file=../config.json')
        with patch.object(evaluation_jobs,'launch',side_effect=lambda j:j):
            resumed=evaluation_jobs.resume(job['id'],{'workers':4})
        self.assertIn('--resume',resumed['command']);self.assertEqual(resumed['command'][-1],'4')

    def test_latest_is_per_pair_and_explicit_rejects_incomplete(self):
        rows=[{'id':'new','eligible':True,'client':'Q3cm','counselor':'P3rs'},
              {'id':'old','eligible':True,'client':'Q3cm','counselor':'P3rs'},
              {'id':'failed','eligible':False,'client':'Q3cm_split','counselor':'P3rs'}]
        self.assertEqual([r['id'] for r in evaluation_jobs.choose(rows,{'clients':['Q3cm'],'counselors':['P3rs']})],['new'])
        self.assertEqual(len(evaluation_jobs.choose(rows,{'selection':'explicit','batches':['new','old']})),2)
        with self.assertRaises(ValueError): evaluation_jobs.choose(rows,{'selection':'explicit','batches':['failed']})
        with self.assertRaises(ValueError): evaluation_jobs.choose(rows,{'clients':['Q3cm_split'],'counselors':['P3rs']})


class DurableWorkerTests(unittest.TestCase):
    def test_time_ids_are_reserved_and_keep_legacy_batch_access(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            with patch('runtime.job_ids.datetime') as clock:
                clock.now.return_value=datetime(2026,11,12,9,11,23)
                self.assertEqual(job_ids.reserve(p),'1112_091123')
                self.assertEqual(job_ids.reserve(p),'1112_091123_02')
            self.assertFalse(job_ids.valid('../1112_091123'))

    def test_stop_terminates_cli_and_descendant_and_records_cancelled(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td);pidfile=p/'child.pid'
            script="import subprocess,sys,time; from pathlib import Path; c=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)']); Path(sys.argv[1]).write_text(str(c.pid)); time.sleep(120)"
            (p/'job.json').write_text(json.dumps({'command':[sys.executable,'-c',script,str(pidfile)],'cwd':td}))
            proc=subprocess.Popen([sys.executable,str(ROOT/'eval/web_worker.py'),td])
            try:
                deadline=time.monotonic()+10
                while not pidfile.exists() and time.monotonic()<deadline: time.sleep(.05)
                self.assertTrue(pidfile.exists())
                child=int(pidfile.read_text());(p/'stop').touch();proc.wait(timeout=10)
                self.assertEqual(json.loads((p/'execution.json').read_text())['status'],'cancelled')
                deadline=time.monotonic()+2
                while evaluation_jobs.process_token(child) and time.monotonic()<deadline: time.sleep(.05)
                self.assertIsNone(evaluation_jobs.process_token(child))
            finally:
                if proc.poll() is None: (p/'stop').touch();proc.wait(timeout=10)


if __name__=='__main__': unittest.main()
