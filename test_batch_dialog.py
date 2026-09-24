import importlib.util
import json
import tkinter as tk
import unittest
from unittest.mock import patch


class BatchDialogTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('batch_dialog'),'Batch dialog missing')
        from batch_dialog import BatchDialog
        self.root=tk.Tk();self.root.withdraw()
        def cleanup():self.root.update_idletasks();self.root.destroy()
        self.addCleanup(cleanup)
        self.commands=[];self.pending=[];self.completed=[]
        def run(script,args,callback):self.commands.append((script,args));self.pending.append(callback)
        self.dialog=BatchDialog(self.root,'FPA','ORANGE',['a','b','c','d'],run,self.completed.append)
        self.rows=[{'uuid':'a','label':'Alpha','source_color':'YELLOW','state':'CONFLICT'},
                   {'uuid':'b','label':'Beta','source_color':'YELLOW','state':'CONFLICT'},
                   {'uuid':'c','label':'Gamma','source_color':'YELLOW','state':'READY'},
                   {'uuid':'d','label':'Delta','source_color':'ORANGE','state':'ALREADY_DESTINATION'}]
        self.pending.pop(0)({'state':'BATCH_SCAN_READY','review':'fixture/scan.json','sha256':'a'*64,'rows':self.rows})

    def test_bulk_choice_and_exclusion_send_one_exact_batch(self):
        d=self.dialog
        self.assertTrue(d.prepare_button.instate(['disabled']))
        d.choose_color('YELLOW')
        d.set_choice('b','skip')
        d.prepare_button.invoke()
        script,args=self.commands[-1]
        self.assertEqual(script,'batch_transition.py');self.assertEqual(args[0],'prepare')
        self.assertEqual(json.loads(args[args.index('--choices')+1]),{'a':'source','b':'skip'})
        self.assertNotIn('--open',args)

    def prepared(self):
        d=self.dialog;d.choose_color('YELLOW');d.prepare_button.invoke()
        self.pending.pop(0)({'state':'PREPARED_REVIEW_REQUIRED','manifest':'fixture/manifest.json',
                            'sha256':'b'*64,'review':'fixture/REVIEW.txt','sessions':3,'survivor_main_count':2,'companion_variants':1})
        return d

    def test_bulk_choice_preserves_prior_exclusion(self):
        d=self.dialog;d.set_choice('b','skip');d.choose_color('YELLOW')
        self.assertEqual(d.choices,{'a':'source','b':'skip'})
        d.set_choice('b','source')
        self.assertEqual(d.choices['b'],'source')

    def test_combined_apply_requires_review_and_companion_ack(self):
        d=self.prepared()
        self.assertTrue(d.apply_button.instate(['disabled']))
        with patch('batch_dialog.os.startfile'):d.open_review()
        d.reviewed.set(True)
        self.assertTrue(d.apply_button.instate(['disabled']))
        d.companions.set(True)
        with patch('batch_dialog.messagebox.askyesno',return_value=True):d.apply_button.invoke()
        script,args=self.commands[-1]
        self.assertEqual(script,'switcher.py');self.assertEqual(args[0],'apply')
        self.assertIn('--accept-source-main-survivor',args);self.assertIn('--accept-companion-variants',args)
        self.assertNotIn('--open',args)
        self.pending.pop(0)({'state':'SWITCH_COMPLETE_SAVED_HISTORIES_READY','sessions':3})
        self.assertEqual(len(self.completed),1)

    def test_changing_choice_invalidates_prepared_batch(self):
        d=self.prepared();d.set_choice('a','skip')
        self.assertIsNone(d.prepared);self.assertTrue(d.apply_button.instate(['disabled']))

    def test_displaying_row_does_not_run_another_command(self):
        d=self.dialog;n=len(self.commands)
        d.tree.selection_set('a');d.display_selected()
        d.tree.selection_set('b');d.display_selected()
        self.assertEqual(len(self.commands),n)


if __name__=='__main__':unittest.main()
