import copy
import tempfile
import unittest
from pathlib import Path
from store import Store
from domain import parse_csv, Problem
from batches import consolidate_completed


class ConsolidationTest(unittest.TestCase):
    def test_merge_preserves_results_audit_and_owner_then_archives_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            s=Store(Path(folder)/'test.sqlite3')
            _,owner,_=s.session()
            ids=[]; originals={}
            for i in range(2):
                imp=s.save_import(owner,parse_csv(f'review_id,main_text\nR{i},太甜{i}'))
                t,_=s.create_task(owner,imp,f'request-{i}','开发测试','蛋白粉','user_supplied',{'model':'stub'})
                t['status']='completed'
                t['records'][0].update(execution='completed',raw_model_content=f'raw-{i}',reviewed=True,feedback=[])
                originals[f'R{i}']=copy.deepcopy(t['records'][0])
                with s.transaction() as db:
                    s.put_task(db,t);s.event(db,t['task_id'],'model_call_completed',{'review_id':f'R{i}'})
                ids.append(t['task_id'])
            merged=consolidate_completed(s,owner,ids,'京东','康比特蛋白粉','2026-10-30')
            self.assertEqual(merged['name'],'京东｜康比特蛋白粉｜2026-10-30｜2条')
            self.assertEqual(len(s.list_tasks(owner)),1)
            self.assertEqual(len(s.list_tasks(owner,deleted=True)),2)
            for r in merged['records']:
                self.assertEqual({k:v for k,v in r.items() if k!='source_task_id'}, originals[r['review_id']])
            events=s.events(owner,merged['task_id'])
            self.assertEqual(sum(e['kind']=='model_call_completed' for e in events),2)
            self.assertEqual(events[-1]['payload']['new_model_calls'],0)
            self.assertEqual(consolidate_completed(s,owner,ids,'京东','康比特蛋白粉')['task_id'],merged['task_id'])
            _,other,_=s.session()
            with self.assertRaises(Problem):consolidate_completed(s,other,ids,'京东','康比特蛋白粉')


if __name__=='__main__':unittest.main()
