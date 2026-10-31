"""Reply preparation must produce useful text without inventing business facts."""
import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from app import create_app, COOKIE
from domain import review_state, statistics
from replies import reply_plan


def review(**changes):
    r={'review_id':'R1','main_text':'喝一杯还能练咀嚼能力','followup_text':'','source_ref':'synthetic://reply-test',
       'comment_type':'experience','feedback':[{'feedback_id':'f1','dimension':'冲调','attitude':'不明确',
       'summary':'疑似冲调结块','evidence':'练咀嚼能力','stage':'main','needs_review':True,'review_reason':'可能是反讽'}],
       'execution':'completed','reviewed':False,'model_feedback':None}
    r.update(changes)
    return r


class ReplyTests(unittest.TestCase):
    def test_save_confirm_and_invalidation_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(Path(directory),start_worker=False)
            with TestClient(app) as client:
                client.headers['X-CSRF-Token']=client.get('/api/session').json()['csrf_token']
                store=app.state.service.store;owner=store.owner(client.cookies.get(COOKIE))
                record=review(main_text='好评',comment_type='general_positive',feedback=[],model_feedback=[])
                task={'task_id':'confirm-test','name':'回复确认','status':'completed','version':1,'records':[record],'dataset_kind':'synthetic_development','created_at':time.time()}
                with store.transaction() as db:db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',('confirm-test',owner,'confirm-test','fixture',json.dumps(task),time.time()))
                route='/api/tasks/confirm-test/reviews/R1'
                body={'version':1,'feedback':[],'reviewed':False,'change_reason':'保存','reply_text':'感谢您的支持','reply_confirmed':False}
                saved=client.patch(route,json=body);self.assertEqual(saved.status_code,200)
                def result():return client.get('/api/tasks/confirm-test/export?format=json').json()
                r=result()['records'][0];self.assertEqual(r['reply']['status'],'已保存');self.assertFalse(r['reply']['confirmed'])
                self.assertEqual(len(r['reply']['options']),3)
                body.update(version=saved.json()['version'],change_reason='确认回复',reply_confirmed=True)
                confirmed=client.patch(route,json=body);self.assertEqual(confirmed.status_code,200)
                r=result()['records'][0];self.assertTrue(r['reply']['confirmed']);self.assertFalse(r['reviewed'])
                self.assertIn('已确认回复',client.get('/api/tasks/confirm-test/export?format=csv').text)
                body.update(version=confirmed.json()['version'],reply_text='谢谢您的好评',reply_confirmed=False)
                edited=client.patch(route,json=body);self.assertEqual(edited.status_code,200)
                self.assertFalse(result()['records'][0]['reply']['confirmed'])
                body.update(version=edited.json()['version'],reply_text='',reply_confirmed=True)
                self.assertEqual(client.patch(route,json=body).status_code,422)

    def test_organized_reply_respects_selected_scope_and_skips_review_states(self):
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(Path(directory),start_worker=False)
            with TestClient(app) as client:
                client.headers['X-CSRF-Token']=client.get('/api/session').json()['csrf_token']
                store=app.state.service.store;owner=store.owner(client.cookies.get(COOKIE))
                records=[review(review_id='P',comment_type='general_positive',feedback=[],model_feedback=[]),
                    review(review_id='E',feedback=[dict(dimension='口味',attitude='负向',summary='太甜',evidence='太甜',stage='main',needs_review=False)],model_feedback=None),
                    review(review_id='O',comment_type='other',feedback=[],model_feedback=[]),
                    review(review_id='WAIT'),review(review_id='REVIEWED',reviewed=True),
                    review(review_id='SAVED',comment_type='other',feedback=[],model_feedback=[],reply_text='已有回复')]
                task={'task_id':'organized-test','name':'已整理','status':'completed','version':1,'records':records,'dataset_kind':'synthetic_development','created_at':time.time()}
                with store.transaction() as db:db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',('organized-test',owner,'organized-test','fixture',json.dumps(task),time.time()))
                route='/api/tasks/organized-test/replies/adopt-organized'
                self.assertEqual(client.post(route,json={'version':1,'review_ids':['NOT-IN-TASK']}).status_code,422)
                chosen=['P','E','WAIT','REVIEWED','SAVED']
                result=client.post(route,json={'version':1,'review_ids':chosen})
                self.assertEqual(result.status_code,200,result.text)
                self.assertEqual(result.json()['adoption'],{'saved':2,'existing':1,'pending':2})
                exported=client.get('/api/tasks/organized-test/export?format=json').json()
                by_id={r['review_id']:r for r in exported['records']}
                self.assertTrue(by_id['E']['reply_text']);self.assertTrue(by_id['P']['reply_text'])
                for identifier in ['O','WAIT','REVIEWED']:self.assertFalse(by_id[identifier].get('reply_text'))
                self.assertTrue(by_id['REVIEWED']['reviewed']);self.assertFalse(by_id['E']['reviewed'])
                self.assertEqual(by_id['SAVED']['reply_text'],'已有回复')
                self.assertEqual(client.post(route,json={'version':1,'review_ids':chosen}).status_code,409)

    def test_bulk_praise_includes_notes_preserves_existing_and_is_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(Path(directory),start_worker=False)
            with TestClient(app) as client:
                csrf=client.get('/api/session').json()['csrf_token']
                client.headers['X-CSRF-Token']=csrf
                store=app.state.service.store;owner=store.owner(client.cookies.get(COOKIE))
                records=[review(review_id=f'P{i}',main_text='很好，客服耐心',comment_type='general_positive',
                    feedback=[],model_feedback=[],service_notes=[]) for i in range(12)]
                records[1]['service_notes']=[{'summary':'客服有耐心','evidence':'客服耐心','stage':'main'}]
                records[2]['reply_text']='原有人工最终回复'
                records.append(review(review_id='WAIT',main_text='好评但需核查',comment_type='general_positive',feedback=[],review_required=True))
                records.append(review(review_id='AMB'))
                task={'task_id':'bulk-test','name':'一般好评','status':'completed','version':1,
                    'records':records,'dataset_kind':'synthetic_development','created_at':time.time()}
                with store.transaction() as db:
                    db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',('bulk-test',owner,'bulk-test','fixture',json.dumps(task),time.time()))
                route='/api/tasks/bulk-test/replies/adopt-praise'
                with TestClient(app) as other:
                    token=other.get('/api/session').json()['csrf_token']
                    self.assertEqual(other.post(route,json={'version':1},headers={'X-CSRF-Token':token}).status_code,404)
                self.assertEqual(client.post(route,json={'version':999}).status_code,409)
                response=client.post(route,json={'version':1})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['adoption'],{'saved':11,'existing':1,'pending':1})
                exported=client.get('/api/tasks/bulk-test/export?format=json').json()
                for r in exported['records'][:12]:
                    self.assertTrue(r['reply_text']);self.assertFalse(r['reviewed'])
                self.assertEqual(exported['records'][1]['reply_text'],exported['records'][1]['reply']['recommendation'])
                self.assertEqual(exported['records'][2]['reply_text'],'原有人工最终回复')
                self.assertFalse(exported['records'][12].get('reply_text'))
                self.assertFalse(exported['records'][13].get('reply_text'))
                events=len(exported['audit'])
                repeated=client.post(route,json={'version':response.json()['version']})
                self.assertEqual(repeated.json()['adoption']['saved'],0)
                self.assertEqual(len(client.get('/api/tasks/bulk-test/export?format=json').json()['audit']),events)
                self.assertEqual(client.post(route,json={'version':1}).status_code,409)
                self.assertIn('原有人工最终回复',client.get('/api/tasks/bulk-test/export?format=csv').text)

    def test_recommendation_and_actions_precede_and_remain_separate_from_human_reply(self):
        r=review(main_text='用户说临期未告知',comment_type='other',feedback=[],
                 specification='临期款',review_note='需核对下单时页面',review_required=True,
                 service_notes=[{'summary':'用户对临期告知不满','evidence':'临期未告知'}])
        plan=reply_plan(r)
        self.assertGreaterEqual(len(plan['actions']),2)
        self.assertIn('下单时',plan['actions'][0]['detail'])
        self.assertIn('包装日期',plan['recommendation'])
        original_recommendation=plan['recommendation']
        r.update(review_resolution='人工核实结果测试',reviewed=True,reply_text='人工最终回复测试')
        updated=reply_plan(r)
        self.assertEqual(updated['recommendation'],original_recommendation)
        self.assertEqual(updated['text'],'人工最终回复测试')
        self.assertNotIn('人工核实结果测试',updated['recommendation'])

    def test_simple_praise_is_organized_even_with_legacy_note(self):
        r=review(main_text='好评',comment_type='general_positive',feedback=[],review_note='未涉及四维，可致谢')
        before=copy.deepcopy(r)
        self.assertEqual(review_state(r),'已整理')
        self.assertTrue(reply_plan(r)['can_copy'])
        self.assertIn('感谢',reply_plan(r)['text'])
        self.assertFalse(r['reviewed'])
        self.assertEqual(r,before)
        self.assertEqual(statistics([r])['needs_review_reviews'],0)

    def test_praise_does_not_hide_explicit_issue_or_service_notes(self):
        r=review(comment_type='general_positive',feedback=[],review_required=True)
        self.assertFalse(reply_plan(r)['can_copy'])
        self.assertEqual(review_state(r),'待复核')
        r=review(comment_type='general_positive',feedback=[],service_notes=[{'summary':'退款未处理'}],review_note='需核查')
        self.assertEqual(review_state(r),'待复核')

    def test_incomplete_and_ambiguity_do_not_become_ready_replies(self):
        self.assertFalse(reply_plan(review(execution='failed'))['text'])
        self.assertFalse(reply_plan(review())['can_copy'])
        r=review(feedback=[{'dimension':'口味','attitude':'负向'}],model_feedback=None)
        plan=reply_plan(r)
        self.assertTrue(plan['can_copy'])
        self.assertIn('口味',plan['text'])
        for claim in ('保证退款','已经补发','过敏','40℃'):
            self.assertNotIn(claim,plan['text'])

    def test_api_requires_answer_preserves_history_exports_and_stales_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            app=create_app(Path(directory),start_worker=False)
            with TestClient(app) as client:
                client.headers['X-CSRF-Token']=client.get('/api/session').json()['csrf_token']
                store=app.state.service.store
                owner=store.owner(client.cookies.get(COOKIE))
                task={'task_id':'reply-test','name':'回复测试','status':'completed','version':1,
                      'records':[review()],'dataset_kind':'synthetic_development','created_at':time.time()}
                with store.transaction() as db:
                    db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?)',('reply-test',owner,'reply-test','fixture',json.dumps(task),time.time()))
                route='/api/tasks/reply-test/reviews/R1'
                body={'version':1,'feedback':task['records'][0]['feedback'],'reviewed':True,'change_reason':'确认'}
                rejected=client.patch(route,json=body)
                self.assertEqual(rejected.status_code,422)
                self.assertEqual(rejected.json()['error']['code'],'review_resolution_required')
                body.update(review_resolution='已向客户确认，所指为冲调后的颗粒感，请补充冲调方式以便进一步核查。')
                saved=client.patch(route,json=body)
                self.assertEqual(saved.status_code,200,saved.text)
                data=client.get('/api/tasks/reply-test/export?format=json').json()
                self.assertEqual(data['records'][0]['feedback'],task['records'][0]['feedback'])
                self.assertTrue(data['records'][0]['reply']['can_copy'])
                self.assertIn('所指为冲调后的颗粒感',data['records'][0]['reply']['text'])
                body.update(version=2,reply_text='您好，请通过店铺客服补充实际冲调方法，便于核查颗粒情况。')
                self.assertEqual(client.patch(route,json=body).status_code,200)
                self.assertEqual(client.patch(route,json=body).status_code,409)
                data=client.get('/api/tasks/reply-test/export?format=json').json()
                self.assertEqual(data['records'][0]['reply']['source'],'人工编辑')
                self.assertEqual(len(data['audit']),2)
                csv=client.get('/api/tasks/reply-test/export?format=csv').text
                self.assertIn('核查答复',csv); self.assertIn(body['reply_text'],csv)
                body.update(version=3,review_resolution='已核实为收到的产品规格问题。')
                del body['reply_text']
                self.assertEqual(client.patch(route,json=body).status_code,200)
                stale=client.get('/api/tasks/reply-test/reviews').json()['records'][0]['reply']
                self.assertTrue(stale['stale']);self.assertFalse(stale['can_copy'])
                body.update(version=4,reply_text='')
                self.assertEqual(client.patch(route,json=body).status_code,200)
                fresh=client.get('/api/tasks/reply-test/reviews').json()['records'][0]['reply']
                self.assertTrue(fresh['can_copy']);self.assertIn('规格问题',fresh['text'])
                with TestClient(app) as other:
                    other.get('/api/session')
                    self.assertEqual(other.get('/api/tasks/reply-test').status_code,404)


if __name__=='__main__':
    unittest.main()
