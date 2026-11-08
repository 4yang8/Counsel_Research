"""Offline checks of evaluator boundaries and backend wiring; no paid model calls."""
import copy
from contextlib import redirect_stdout
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('eval_runner', HERE / 'run_eval.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class EvalTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((HERE / '입력예시_01_response_1.json').read_text())
        self.criterion = 'past_reason_fidelity'
        self.result = dict(status='scored', score=5, rationale='동의 당시 이유를 유지한다.')
        self.schema = runner.schema_for()

    def test_input_blocks_labels_future_and_duplicate_turns(self):
        runner.validate_input(self.data)
        for mutate in (lambda d:d.update(author_notes='answer'),
                       lambda d:d['history'][0].update(turn=7),
                       lambda d:d['history'][1].update(turn=1)):
            d=copy.deepcopy(self.data);mutate(d)
            with self.assertRaises(ValueError):runner.validate_input(d)

    def test_score_status_and_output_fields_are_validated(self):
        runner.validate_result(self.result)
        for fields in ({'status':'not_applicable','score':3},{'score':True},{'score':6},
                       {'score':0},{'score':None},{'status':'failed'},{'rationale':' '},
                       {'rationale':None},{'evidence':[]},{'response_id':'other'}):
            with self.assertRaises(ValueError):
                runner.validate_result({**self.result,**fields})
        for status in ('insufficient_evidence', 'not_applicable'):
            runner.validate_result({**self.result,'status':status,'score':None})
        for key in self.result:
            d=copy.deepcopy(self.result);del d[key]
            with self.assertRaises(ValueError):runner.validate_result(d)

    def test_natural_language_reason_needs_no_separate_evidence_or_identifiers(self):
        d={**self.result, 'rationale':'2턴 내면 기록의 거절 부담과 7턴 답변의 설명이 일치한다.'}
        self.assertEqual(runner.validate_result(d),d)
        self.assertEqual(set(self.schema['properties']),{'status','score','rationale'})
        self.assertEqual(set(self.schema['required']),{'status','score','rationale'})
        for stem, _ in runner.RUBRICS.values():
            prompt=(HERE/(stem+'_system.md')).read_text()
            example=json.loads(prompt.split('```json\n',1)[1].split('```',1)[0])
            runner.validate_result(example)

    def test_text_input_preserves_content_and_omits_structural_keys_and_ids(self):
        rendered=runner.format_evaluation_input(self.data)
        def leaves(value):
            if isinstance(value,dict):
                for item in value.values():yield from leaves(item)
            elif isinstance(value,list):
                for item in value:yield from leaves(item)
            elif isinstance(value,str):yield value
        for content in leaves(self.data['profile']):self.assertIn(content,rendered)
        for row in self.data['history']:
            self.assertIn(f"[{row['turn']}턴]",rendered)
            for content in leaves(row):self.assertIn(content,rendered)
        for content in leaves(self.data['target']):self.assertIn(content,rendered)
        self.assertIn('[평가 대상: 7턴]',rendered)
        self.assertIn('제공되지 않음',rendered)
        for token in ('history','target','private_inner','appraisal','relevant_evidence',
                      'generated_inner','reference_state_before_target','null',
                      self.data['case_id'],self.data['response_id']):
            self.assertNotIn(token,rendered)
        for stem,_ in runner.RUBRICS.values():
            system=(HERE/(stem+'_system.md')).read_text()
            template=(HERE/(stem+'_user.md')).read_text()
            self.assertEqual(template.count('{{evaluation_input_text}}'),1)
            for token in ('target.', 'history', 'private_inner','reference_state_before_target',
                          '{{evaluation_input_json}}'):
                self.assertNotIn(token,system+template)

    def test_text_input_handles_missing_string_and_nested_records(self):
        data=copy.deepcopy(self.data)
        data['profile']='프로필 원문'
        data['history'][0]['private_inner']=None
        data['history'][1]['private_inner']='이유를 보류함\n원문 두 번째 줄'
        data['reference_state_before_target']={'content':{'공개 범위':['업무만', '가족은 보류']},
                                               'source':'직전 확인 기록'}
        data['target']['generated_inner']={'response_plan':{'action':'업무만 말한다','reason':'가족은 아직 부담됨'}}
        rendered=runner.format_evaluation_input(data)
        for value in ('프로필 원문','당시 내면 기록:\n제공되지 않음',
                      '이유를 보류함\n원문 두 번째 줄','업무만','가족은 보류',
                      '출처: 직전 확인 기록','업무만 말한다','가족은 아직 부담됨'):
            self.assertIn(value,rendered)
        data['history']=[]
        self.assertIn('[과거 대화와 당시 내면 기록]\n제공되지 않음',runner.format_evaluation_input(data))
        data['profile']={'unmapped_field':'누락되면 안 되는 내용'}
        with self.assertRaisesRegex(ValueError,'매핑'):
            runner.format_evaluation_input(data)

    def test_report_quotes_are_readable_and_html_remains_escaped(self):
        self.assertEqual(runner.md_cell("'인용' \"설명\""),"'인용' \"설명\"")
        self.assertEqual(runner.md_cell('<script>|\n'), '&lt;script&gt;\\|<br>')

    def test_codex_uses_requested_model_effort_and_separate_rubric(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            def execute(command, **kwargs):
                self.assertEqual(command[command.index('-m')+1],'gpt-6-luna')
                self.assertIn('model_reasoning_effort="max"',command)
                self.assertIn('developer_instructions="RUBRIC"',command)
                self.assertIn('read-only',command)
                self.assertNotIn('OPENAI_API_KEY',kwargs['env'])
                self.assertNotIn('CODEX_API_KEY',kwargs['env'])
                self.assertIn('DATA',kwargs['input'])
                output=Path(command[command.index('--output-last-message')+1])
                output.write_text(json.dumps(self.result))
                kwargs['stdout'].write('{"type":"turn.completed"}\n')
                return subprocess.CompletedProcess(command,0)
            with patch.dict(os.environ,{'OPENAI_API_KEY':'dummy','CODEX_API_KEY':'dummy'}), patch.object(runner.subprocess,'run',side_effect=execute):
                raw=runner.codex_call({'codex_bin':'codex','model':'gpt-6-luna','effort':'max','timeout':10},
                                     'RUBRIC','DATA',self.schema,folder)
            self.assertEqual(json.loads(raw),self.result)

    def test_codex_tool_use_is_excluded(self):
        with tempfile.TemporaryDirectory() as directory:
            def execute(command, **kwargs):
                Path(command[command.index('--output-last-message')+1]).write_text(json.dumps(self.result))
                kwargs['stdout'].write('{"type":"item.completed","item":{"type":"mcp_tool_call"}}\n')
                return subprocess.CompletedProcess(command,0)
            with patch.object(runner.subprocess,'run',side_effect=execute):
                with self.assertRaisesRegex(ValueError,'도구 사용'):
                    runner.codex_call({'codex_bin':'codex','model':'gpt-6-luna','effort':'max','timeout':10},
                                      'RUBRIC','DATA',self.schema,Path(directory))

    def test_vllm_http_messages_schema_and_truncation(self):
        outer=self
        class Model(BaseHTTPRequestHandler):
            finish_reason='stop'
            def log_message(self,*args):pass
            def do_POST(self):
                outer.assertEqual(self.path,'/v1/chat/completions')
                payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                outer.assertEqual([m['role'] for m in payload['messages']],['system','user'])
                outer.assertEqual(payload['structured_outputs']['json'],outer.schema)
                outer.assertEqual(payload['model'],'local-model')
                outer.assertEqual(self.headers['Authorization'],'Bearer dummy-test-key')
                self.send_response(200);self.end_headers()
                self.wfile.write(json.dumps({'choices':[{'finish_reason':self.finish_reason,'message':{'content':json.dumps(outer.result)}}]}).encode())
        server=ThreadingHTTPServer(('127.0.0.1',0),Model)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'TEST_EVAL_KEY':'dummy-test-key'}):
                config={'url':f'http://127.0.0.1:{server.server_port}/v1','model':'local-model',
                        'temperature':0,'max_tokens':8192,'api_key_env':'TEST_EVAL_KEY','timeout':10}
                folder=Path(directory)
                raw=runner.vllm_call(config,'RUBRIC','DATA',self.schema,folder)
                runner.validate_result(json.loads(raw))
                self.assertNotIn('dummy-test-key',(folder/'request.json').read_text())
                Model.finish_reason='length'
                with self.assertRaisesRegex(ValueError,'미완료'):runner.vllm_call(config,'RUBRIC','DATA',self.schema,folder)
        finally:server.shutdown();server.server_close();thread.join()


    def test_main_keeps_only_case_report_and_cleans_success_and_failure_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);input_path=root/'input.json'
            input_path.write_text(json.dumps(self.data))
            output=root/'results';old=output/'previous';old.mkdir(parents=True)
            (old/'unchanged.json').write_text('keep')
            workdirs=[]
            def model(config, system, user, schema, folder):
                workdirs.append(folder)
                (folder/'raw_response.json').write_text('temporary')
                self.assertEqual(schema,self.schema)
                self.assertIn(runner.format_evaluation_input(self.data),user)
                self.assertNotIn('target.',user+system)
                cid=list(runner.RUBRICS.values())[(len(workdirs)-1)%4][1]
                if cid=='intervention_response_validity':
                    raise ValueError('모의 실패 | 다음 줄\n설명')
                result=copy.deepcopy(self.result)
                if cid=='past_present_distinction':
                    result.update(status='not_applicable',score=None,rationale='현재 변화 주장이 없다.')
                elif cid=='disclosure_appropriateness':
                    result.update(status='insufficient_evidence',score=None,rationale='현재 준비도 자료가 없다.')
                return json.dumps(result)
            env={'EVAL_MODE':'vllm','VLLM_MODEL':'mock','CRITERIA':'all','DRY_RUN':'false',
                 'INPUT_FILE':str(input_path),'OUTPUT_DIR':str(output)}
            with patch.dict(os.environ,env),patch.object(runner,'vllm_call',side_effect=model), \
                 patch.object(runner,'write_report',wraps=runner.write_report) as reports,redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(),1)
                self.assertEqual(runner.main(),1)
                for row in reports.call_args.args[3]:
                    if row['execution']=='success':
                        self.assertEqual(row['result']['criterion_id'],row['criterion_id'])
                        self.assertEqual(row['result']['case_id'],self.data['case_id'])
                        self.assertEqual(row['result']['response_id'],self.data['response_id'])
            self.assertEqual((old/'unchanged.json').read_text(),'keep')
            runs=[p for p in output.iterdir() if p!=old]
            self.assertEqual(len(runs),2)
            for run in runs:
                files=list(run.rglob('*'))
                self.assertEqual(len(files),1)
                self.assertEqual(files[0].name,'case_01_past_reason.md')
                report=files[0].read_text()
                for value in ('5점','해당 없음','판정 불가','실행 실패','동의 당시 이유를 유지한다.',
                              '현재 변화 주장이 없다.','response_1','7턴','mock'):
                    self.assertIn(value,report)
                self.assertNotIn('evidence',report)
                self.assertIn('모의 실패 \| 다음 줄<br>설명',report)
            self.assertTrue(all(not folder.exists() for folder in workdirs))

    def test_dry_run_and_safe_case_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);data=copy.deepcopy(self.data)
            data['case_id']='../../사례|하나'
            input_path=root/'input.json';input_path.write_text(json.dumps(data))
            output=root/'results'
            with patch.dict(os.environ,{'EVAL_MODE':'codex','DRY_RUN':'true','CRITERIA':'01',
                                        'INPUT_FILE':str(input_path),'OUTPUT_DIR':str(output)}), \
                 patch.object(runner.subprocess,'run') as model,redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(),0)
                model.assert_not_called()
            runs=list(output.iterdir());self.assertEqual(len(runs),1)
            files=list(runs[0].iterdir());self.assertEqual(len(files),1)
            self.assertEqual(files[0].suffix,'.md')
            self.assertIn('호출 안 함',files[0].read_text())
            self.assertNotIn('5점',files[0].read_text())
            self.assertNotIn('/',runner.report_filename(data['case_id']))

    def test_timeout_reason_does_not_embed_full_command_and_prompts(self):
        error=subprocess.TimeoutExpired(['codex','PRIVATE_PROMPT'],timeout=12)
        reason=runner.error_reason(error,{'timeout':12})
        self.assertIn('12초',reason)
        self.assertNotIn('PRIVATE_PROMPT',reason)


if __name__=='__main__':unittest.main()
