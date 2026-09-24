import importlib.util
import tkinter as tk
import unittest
from unittest.mock import patch


class SurvivorDialogTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('survivor_dialog'), 'Survivor dialog missing')
        from survivor_dialog import SurvivorDialog
        self.root=tk.Tk();self.root.withdraw()
        def cleanup():self.root.update_idletasks();self.root.destroy()
        self.addCleanup(cleanup)
        self.commands=[];self.pending=[];self.completed=[]
        def command(script,args,callback):
            self.commands.append((script,args));self.pending.append(callback)
        self.dialog=SurvivorDialog(self.root,'FPA','ORANGE','fixture',command,self.completed.append)
        summary={'color':'YELLOW','email':'source@example.invalid','registered':True,'last_prompt':None,
                 'last_output':None,'records':2,'branch_records':1,'user_text_records':1,
                 'assistant_text_records':1,'tool_calls':0,'tool_results':0,'previews':[],
                 'preview_total':0,'path':'fixture/source','sha256':'a'*64}
        self.pending.pop(0)({'state':'BRANCH_COMPARISON_READY','source_color':'YELLOW','destination':'ORANGE',
                            'uuid':'fixture','label':'Example','token':'token','source':summary,
                            'target':dict(summary,color='ORANGE',registered=False),
                            'common_records':1,'companions':[]})

    def ready(self):
        d=self.dialog;d.choice.set('source');d.prepare_button.invoke()
        self.pending.pop(0)({'state':'PREPARED_REVIEW_REQUIRED','manifest':'fixture/manifest.json',
                            'sha256':'a'*64,'review':'fixture/REVIEW.txt','backups':'fixture/objects',
                            'companion_variants':0})
        with patch('survivor_dialog.os.startfile'):
            d.review_button.invoke()
        d.reviewed.set(True)

    def test_no_default_winner_and_source_choice_binds_exact_comparison(self):
        d=self.dialog
        self.assertEqual(d.choice.get(),'')
        self.assertTrue(d.prepare_button.instate(['disabled']))
        self.assertTrue(d.apply_button.instate(['disabled']))
        d.choice.set('source');d.prepare_button.invoke()
        self.assertEqual(self.commands[-1],('survivor_review.py',[
            'prepare','--group','FPA','--destination','ORANGE','--uuid','fixture','--token','token']))
        self.assertFalse(d.close())
        self.assertTrue(d.window.winfo_exists())

    def test_changing_choice_discards_review_and_cancel_has_no_apply(self):
        self.ready();d=self.dialog
        self.assertFalse(d.apply_button.instate(['disabled']))
        d.choice.set('target')
        self.assertIsNone(d.prepared)
        self.assertTrue(d.apply_button.instate(['disabled']))
        d.close()
        self.assertFalse(any(script=='switcher.py' for script,_ in self.commands))

    def test_apply_is_explicit_and_does_not_reopen_workspace(self):
        self.ready();d=self.dialog
        with patch('survivor_dialog.messagebox.askyesno',return_value=True):
            d.apply_button.invoke()
        script,args=self.commands[-1]
        self.assertEqual(script,'switcher.py')
        self.assertIn('--accept-source-main-survivor',args)
        self.assertNotIn('--open',args)
        self.pending.pop(0)({'state':'SWITCH_COMPLETE_SAVED_HISTORIES_READY'})
        self.assertEqual(len(self.completed),1)
        self.assertIsNone(d.prepared)

    def test_failed_operation_discards_choice_evidence_and_review(self):
        self.ready();d=self.dialog
        with patch('survivor_dialog.messagebox.askyesno',return_value=True):d.apply_button.invoke()
        self.pending.pop(0)({'state':'HELD','error':'History changed; compare again'})
        self.assertIsNone(d.data);self.assertIsNone(d.prepared)
        self.assertTrue(d.apply_button.instate(['disabled']))
        self.assertFalse(self.completed)
        self.assertIn('History changed',d.status.get())

    def test_command_start_failure_releases_busy_guard(self):
        d=self.dialog
        def fail(*args):raise OSError('Cannot start local command')
        d.run_command=fail
        d.compare_button.invoke()
        self.assertFalse(d.busy)
        self.assertIsNone(d.data)
        self.assertIn('Cannot start',d.status.get())

    def test_parent_worker_start_failure_releases_auxiliary_guard(self):
        import switcher_ui
        jobs=[0]
        with patch('switcher_ui.threading.Thread.start',side_effect=RuntimeError('cannot start thread')):
            with self.assertRaisesRegex(RuntimeError,'cannot start'):
                switcher_ui.start_tracked_worker(jobs,lambda:None)
        self.assertEqual(jobs,[0])


if __name__=='__main__':unittest.main()
