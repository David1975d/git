"""Review queue is an exception queue, not every unconfirmed model result."""
import copy
import unittest
from domain import review_state, statistics, export_csv


def record(**changes):
    item={'review_id':'r1','main_text':'奶茶味好喝','followup_text':'','source_ref':'test',
          'feedback':[], 'execution':'completed','reviewed':False}
    item.update(changes)
    return item


class ReviewStateTests(unittest.TestCase):
    def test_clear_positive_and_negative_are_organized_not_human_reviewed(self):
        for attitude in ['正向','负向']:
            r=record(feedback=[{'dimension':'口味','attitude':attitude,'needs_review':False}])
            self.assertEqual(review_state(r),'已整理')
            self.assertFalse(r['reviewed'])

    def test_empty_feedback_and_scope_notes_do_not_force_review(self):
        self.assertEqual(review_state(record(scope_notes=['未提及四维'])),'已整理')

    def test_explicit_ambiguity_note_and_changes_need_review(self):
        for changes in [
            {'review_required':True}, {'review_note':'需核对受损对象'},
            {'feedback':[{'attitude':'不明确'}]},
            {'feedback':[{'attitude':'正向','needs_review':True}]},
            {'model_feedback':[], 'feedback':[{'attitude':'正向'}]},
        ]:
            self.assertEqual(review_state(record(**changes)),'待复核')

    def test_human_confirmation_and_incomplete_results_remain_distinct(self):
        self.assertEqual(review_state(record(reviewed=True,review_required=True)),'已人工复核')
        for execution in ['pending','running','failed','interrupted']:
            self.assertEqual(review_state(record(execution=execution)),'未完成分析')

    def test_stats_partition_and_csv_use_the_same_status_without_mutation(self):
        rows=[record(),record(review_note='待核对'),record(reviewed=True),record(execution='failed')]
        before=copy.deepcopy(rows)
        stats=statistics(rows)
        self.assertEqual([stats[k] for k in ['organized_reviews','needs_review_reviews','reviewed_reviews','completed_reviews']],[1,1,1,3])
        result=export_csv({'records':rows,'dataset_kind':'synthetic_development'})
        for status in ['已整理','待复核','已人工复核','未完成分析']:
            self.assertIn(status,result)
        self.assertEqual(rows,before)


if __name__=='__main__':
    unittest.main()
