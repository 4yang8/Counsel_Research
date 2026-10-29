"""Plan operation schema/runtime parity and lossless model-only result deduplication."""
import copy
import json
import unittest
import test_lab  # add the application path
import jsonschema
from runtime.tool_result_view import model_result
from client.Q3a import turn_plan


class ContractTests(unittest.TestCase):
    def test_action_shapes_match_runtime(self):
        schema=turn_plan.TOOL['function']['parameters']
        jsonschema.Draft202012Validator.check_schema(schema)
        def request(op):return {'goal':'테스트 목표','reason':'현재 단계 확인','operations':[op]}
        add={'action':'add','step_id':'s1','task':'자료 확인','completion_criterion':'근거 확인'}
        initial=turn_plan.apply({},request(add))
        for action in ('add','revise','start','complete','skip'):
            with self.subTest(action=action):
                op={'action':action,'step_id':'s1'}
                if action in ('add','revise'):op.update(task='새 판단',completion_criterion='근거 검토')
                if action in ('complete','skip'):op['result']='판단 결과' if action=='complete' else '현재는 불필요'
                if action=='complete':op['sources']=['공개 대화의 질문']
                args=request(op);jsonschema.validate(args,schema)
                state={} if action=='add' else copy.deepcopy(initial)
                if action=='complete':state=turn_plan.apply(state,request({'action':'start','step_id':'s1'}))
                if action=='skip':
                    state=turn_plan.apply(state,request({**add,'step_id':'s2'}))
                turn_plan.apply(state,args)
                # Every required field is actually required in the published variant.
                for field in turn_plan.OP_FIELDS[action]:
                    bad=copy.deepcopy(args);del bad['operations'][0][field]
                    with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(bad,schema)
                    with self.assertRaises(ValueError):turn_plan.apply(state,bad)
        # The exact field mismatch observed in the live second turn is now invalid in both.
        bad=request({**add,'action':'start'})
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(bad,schema)
        with self.assertRaises(ValueError):turn_plan.apply(initial,bad)

    def test_duplicate_only_removed_without_mutation(self):
        payload={'plan':{'goal':'경계를 고려한다','steps':[{'id':'s1','status':'completed'}]},'accepted':True}
        text={'type':'text','text':json.dumps(payload,ensure_ascii=False)}
        raw={'content':[text],'structuredContent':payload,'isError':False,'_meta':{'turn':1}}
        before=copy.deepcopy(raw);view=model_result(raw)
        self.assertEqual(raw,before)
        self.assertEqual(view,{'structuredContent':payload,'isError':False,'_meta':{'turn':1}})
        self.assertEqual(json.dumps(view,ensure_ascii=False).count('경계를 고려한다'),1)

    def test_extra_text_errors_and_nontext_are_kept(self):
        extra={'type':'text','text':'별도 주의사항'}
        image={'type':'image','data':'example','mimeType':'image/png'}
        raw={'structuredContent':{'a':1},'content':[{'type':'text','text':'{ "a": 1 }'},extra,image],'isError':True}
        self.assertEqual(model_result(raw),{'structuredContent':{'a':1},'content':[extra,image],'isError':True})
        for content in ([extra],[{'type':'text','text':'{"a":2}'}],[{'type':'text','text':'{"a":1}','annotations':{'priority':1}}]):
            value={'structuredContent':{'a':1},'content':content}
            self.assertEqual(model_result(value),value)
        error={'isError':True,'content':[{'type':'text','text':'입력 오류'}]}
        self.assertEqual(model_result(error),error)

if __name__=='__main__':unittest.main()
