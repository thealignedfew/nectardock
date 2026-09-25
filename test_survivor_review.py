import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


class SurvivorReviewTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('survivor_review'), 'Survivor comparison implementation missing')
        import survivor_review
        self.r = survivor_review
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def history(self, name, records):
        path = self.root / name
        path.write_text(''.join(json.dumps(r)+'\n' for r in records), encoding='utf-8')
        return path

    def record(self, kind, uid, content, timestamp='2026-09-24T12:30:00Z'):
        return dict(type=kind, uuid=uid, sessionId='fixture', timestamp=timestamp,
                    message=dict(content=content))

    def test_compare_distinguishes_tools_from_user_text_and_omits_thinking(self):
        common = self.record('user', 'base', 'Shared question')
        a = self.history('a', [common, self.record('assistant','a',[
            {'type':'thinking','thinking':'PRIVATE REASONING'},
            {'type':'text','text':'Source answer'}, {'type':'tool_use','id':'t','name':'Read'}])])
        b = self.history('b', [common, self.record('user','b',[
            {'type':'tool_result','tool_use_id':'t','content':'SECRET TOOL OUTPUT'}])])
        result = self.r.compare_histories(a,b,'fixture')
        self.assertEqual(result['common_records'],1)
        self.assertEqual(result['source']['branch_records'],1)
        self.assertEqual(result['target']['branch_records'],1)
        self.assertEqual(result['target']['user_text_records'],1)
        self.assertEqual(result['target']['tool_results'],1)
        self.assertEqual(result['source']['tool_calls'],1)
        rendered=json.dumps(result)
        self.assertIn('Source answer', rendered)
        self.assertNotIn('PRIVATE REASONING', rendered)
        self.assertNotIn('SECRET TOOL OUTPUT', rendered)

    def test_timestamps_are_recorded_not_file_mtime_and_missing_stays_unknown(self):
        a=self.history('a',[self.record('assistant','a','Answer',None)])
        b=self.history('b',[self.record('user','b','Question','2026-09-23T16:20:00Z')])
        result=self.r.compare_histories(a,b,'fixture')
        self.assertIsNone(result['source']['last_output'])
        self.assertEqual(result['target']['last_prompt'],'2026-09-23T16:20:00Z')

    def test_comparison_rejects_foreign_uuid_and_incomplete_record(self):
        good=self.history('good',[self.record('user','a','question')])
        bad=self.history('bad',[dict(self.record('user','b','question'),sessionId='other')])
        with self.assertRaisesRegex(self.r.s.Hold,'identity'):
            self.r.compare_histories(good,bad,'fixture')
        bad.write_text('{"type":"user"}',encoding='utf-8')
        with self.assertRaises(self.r.s.Hold):self.r.compare_histories(good,bad,'fixture')

    def test_companion_changes_and_registry_changes_invalidate_choice(self):
        evidence={'source':{'main':'aa','sidecars/tool':'bb'},'target':{'main':'cc'},'index':'dd'}
        token=self.r.evidence_token(evidence)
        self.r.require_fresh(token,evidence)
        for changed in [dict(evidence,index='new'),dict(evidence,source={'main':'aa','sidecars/tool':'new'})]:
            with self.assertRaisesRegex(self.r.s.Hold,'changed'):
                self.r.require_fresh(token,changed)

    def test_apply_requires_source_choice_review_and_companion_acceptance(self):
        item={'manifest':'fixture/manifest.json','sha256':'a'*64,'companion_variants':1}
        for choice,reviewed,companions in [('',True,True),('target',True,True),('source',False,True),('source',True,False)]:
            with self.assertRaises(self.r.s.Hold):
                self.r.apply_arguments(item,choice,reviewed,companions)
        args=self.r.apply_arguments(item,'source',True,True)
        self.assertIn('--accept-source-main-survivor',args)
        self.assertIn('--accept-companion-variants',args)
        self.assertNotIn('--open',args)

    def test_record_equality_not_timestamp_or_file_size_drives_common_prefix(self):
        a=self.history('a',[self.record('user','a','Different')])
        b=self.history('b',[self.record('user','b','Different')])
        result=self.r.compare_histories(a,b,'fixture')
        self.assertEqual(result['common_records'],0)
        self.assertEqual(result['source']['branch_records'],1)

    def test_conflict_link_only_accepts_registered_exact_main_history(self):
        sid='11111111-1111-4111-8111-111111111111'
        result={'state':'HELD','error':f'Distinct saved branches or companion versions require review: X:/fixture/{sid}.jsonl'}
        self.assertEqual(self.r.conflict_uuid(result,[sid]),sid)
        self.assertIsNone(self.r.conflict_uuid(result,[]))
        self.assertIsNone(self.r.conflict_uuid(dict(result,state='READY'),[sid]))
        self.assertIsNone(self.r.conflict_uuid(dict(result,error='Other error: '+sid+'.jsonl'),[sid]))


if __name__=='__main__':unittest.main()
