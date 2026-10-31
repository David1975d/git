import copy
import unittest
from domain import Problem,check_comment_metadata,comment_type,statistics,export_csv


def record(**fields):
    r={'review_id':'T','main_text':'味道不错，客服耐心','followup_text':'','source_ref':'test',
       'execution':'completed','reviewed':False,'feedback':[]}
    r.update(fields);return r


class CommentTypeTests(unittest.TestCase):
    def test_empty_feedback_never_implies_positive(self):
        self.assertEqual(comment_type(record()),'other')
        self.assertEqual(comment_type(record(main_text='临期未注明')),'other')
        self.assertEqual(comment_type(record(comment_type='general_positive')),'general_positive')

    def test_feedback_is_experience_and_status_is_separate(self):
        r=record(feedback=[{'dimension':'口味','attitude':'正向','needs_review':False}],reviewed=False)
        self.assertEqual(comment_type(r),'experience')
        self.assertEqual(statistics([r])['needs_review_reviews'],0)
        r['feedback'][0]['needs_review']=True
        self.assertEqual(statistics([r])['needs_review_reviews'],1)
        self.assertEqual(comment_type(r),'experience')

    def test_service_note_requires_exact_evidence_and_does_not_trigger_review(self):
        r=record(comment_type='general_positive')
        note={'summary':'用户认可客服','evidence':'客服耐心','stage':'main'}
        _,notes=check_comment_metadata('general_positive',[note],r,[])
        r['service_notes']=notes
        self.assertEqual(statistics([r])['needs_review_reviews'],0)
        for changes in [{'evidence':'客服差'},{'stage':'followup'}]:
            with self.assertRaises(Problem):check_comment_metadata('general_positive',[{**note,**changes}],r,[])

    def test_invalid_kind_and_kind_feedback_conflicts_are_rejected(self):
        for kind,feedback in [('nice',[]),('experience',[]),('general_positive',[{}])]:
            with self.assertRaises(Problem):check_comment_metadata(kind,[],record(),feedback)

    def test_statistics_and_export_preserve_both_axes_and_baseline(self):
        rows=[record(comment_type='general_positive'),record(service_notes=[{'summary':'客服说明','evidence':'客服耐心','stage':'main'}]),
              record(feedback=[{'dimension':'口味','attitude':'不明确','needs_review':True}]),record(execution='pending')]
        original=copy.deepcopy(rows);stats=statistics(rows)
        self.assertEqual(stats['comment_types'],{'experience':1,'general_positive':1,'other':1})
        self.assertEqual(stats['supplemental_reviews'],1)
        out=export_csv({'records':rows,'dataset_kind':'synthetic_development'})
        for text in ['一般好评','其他评价','具体体验反馈','客服说明','客服耐心','待复核','已整理']:self.assertIn(text,out)
        self.assertEqual(rows,original)
