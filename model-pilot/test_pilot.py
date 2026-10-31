import copy
import json
import tempfile
import ssl
import unittest
from pathlib import Path
from unittest.mock import patch
import run_pilot as pilot


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.case = {'review_id':'T1','main_text':'味道好，但封口破了。','followup_text':''}
        self.item = {'dimension':'包装','attitude':'负向','summary':'用户报告封口破损。','evidence':'封口破了','stage':'main','needs_review':False,'review_reason':''}
        self.output = {'review_id':'T1','feedback':[self.item],'scope_notes':[]}
        self.config = {'provider':'mock-only','base_url':'https://example.invalid/v1','model':'mock-model','api_key':'test-only-not-a-real-key','max_tokens':1200,'timeout_seconds':10,'json_mode':True}

    def test_valid_evidence(self):
        self.assertEqual(pilot.validate_output(self.output,self.case),[])

    def test_wrong_source_stage_and_invented_evidence(self):
        for change in [{'stage':'followup'},{'evidence':'有异物'}]:
            out=copy.deepcopy(self.output);out['feedback'][0].update(change)
            self.assertTrue(pilot.validate_output(out,self.case))

    def test_wrong_id_dimension_and_review_flag(self):
        out=copy.deepcopy(self.output);out['review_id']='OTHER';out['feedback'][0].update(dimension='物流',needs_review='false')
        self.assertEqual(len(pilot.validate_output(out,self.case)),3)

    def test_review_requires_reason(self):
        out=copy.deepcopy(self.output);out['feedback'][0]['needs_review']=True
        self.assertTrue(pilot.validate_output(out,self.case))

    def test_config_endpoint_and_no_redirect(self):
        valid=pilot.validate_config(copy.deepcopy(self.config))
        self.assertEqual(valid['endpoint'],'https://example.invalid/v1/chat/completions')
        self.assertIsNone(pilot.NoRedirect().redirect_request(None,None,302,'',{},'https://other.invalid'))

    def test_invoke_keeps_tls_verification_and_no_redirect(self):
        cfg=pilot.validate_config(copy.deepcopy(self.config))
        with patch.object(pilot.request,'build_opener') as build:
            build.return_value.open.return_value.__enter__.return_value.read.return_value=b'{}'
            build.return_value.open.return_value.__enter__.return_value.headers={'Content-Type':'application/json'}
            pilot.invoke(cfg,{})
        handlers=build.call_args.args
        self.assertTrue(any(isinstance(h,pilot.NoRedirect) for h in handlers))
        tls=next(h._context for h in handlers if isinstance(h,pilot.request.HTTPSHandler))
        self.assertEqual(tls.verify_mode,ssl.CERT_REQUIRED)
        self.assertTrue(tls.check_hostname)

    def test_reject_unsafe_or_masked_config(self):
        for fields in [{'base_url':'http://example.invalid/v1'},{'base_url':'https://example.invalid/v1?key=secret'},{'api_key':'sk-****'}]:
            data=dict(self.config);data.update(fields)
            with self.assertRaises(pilot.ConfigError):pilot.validate_config(data)

    def test_no_evaluation_focus_in_model_input(self):
        case=dict(self.case,focus='human-only answer')
        result=pilot.payload_for(self.config,case,'Return JSON')
        self.assertNotIn('human-only answer',json.dumps(result))
        self.assertNotIn('api_key',result)

    def test_optional_json_parameter(self):
        cfg=dict(self.config,json_mode=False)
        self.assertNotIn('response_format',pilot.payload_for(cfg,self.case,'Return JSON'))

    def test_secret_redaction(self):
        self.assertNotIn(self.config['api_key'],pilot.safe_message('error '+self.config['api_key'],self.config['api_key']))

    def test_duplicate_cases_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'cases.json';path.write_text(json.dumps({'cases':[self.case,self.case]}),encoding='utf-8')
            with self.assertRaises(pilot.ConfigError):pilot.read_cases(path,2)

    def test_responses_endpoint_and_payload(self):
        cfg=pilot.validate_config(dict(self.config,api_format='responses',stream=True,json_mode=False))
        self.assertEqual(cfg['endpoint'],'https://example.invalid/v1/responses')
        body=pilot.payload_for(cfg,dict(self.case,focus='human-only'),'Return JSON')
        self.assertEqual(body['max_output_tokens'],1200)
        self.assertFalse(body['store'])
        self.assertTrue(body['stream'])
        self.assertNotIn('messages',body)
        self.assertNotIn('human-only',json.dumps(body))

    def test_protocol_path_mismatch_rejected(self):
        with self.assertRaises(pilot.ConfigError):
            pilot.validate_config(dict(self.config,base_url='https://example.invalid/v1/responses'))

    def test_stream_uses_final_answer_not_reasoning(self):
        final={'status':'completed','output':[{'type':'reasoning','content':'do not save me'},
              {'type':'message','content':[{'type':'output_text','text':json.dumps(self.output,ensure_ascii=False)}]}]}
        events=[{'type':'response.reasoning_text.delta','delta':'intermediate'},
                {'type':'response.completed','response':final}]
        raw='\n\n'.join('data: '+json.dumps(e,ensure_ascii=False) for e in events)+'\n\n'
        parsed=pilot.parse_api_body(raw.encode(),'text/event-stream')
        text,finish=pilot.extract_answer(parsed,'responses')
        self.assertEqual(finish,'stop')
        self.assertNotIn('do not save me',text)
        self.assertEqual(json.loads(text)['review_id'],'T1')

    def test_truncated_stream_not_success(self):
        raw=b'data: {"type":"response.output_text.delta","delta":"partial"}\n\n'
        with self.assertRaises(ValueError):pilot.parse_api_body(raw,'text/event-stream')

    def test_ximu_root_normalized_once(self):
        for base in ['https://codex.ximuai.com','https://codex.ximuai.com/v1']:
            cfg=pilot.validate_config(dict(self.config,base_url=base,api_format='responses'))
            self.assertEqual(cfg['endpoint'],'https://codex.ximuai.com/v1/responses')

    def test_explicit_model_overrides_old_example(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json';path.write_text(json.dumps(dict(self.config,model='old-example')),encoding='utf-8')
            cfg=pilot.read_config(path,{'model':'gpt-6.1-sol'})
            self.assertEqual(cfg['model'],'gpt-6.1-sol')


if __name__=='__main__':
    unittest.main()
