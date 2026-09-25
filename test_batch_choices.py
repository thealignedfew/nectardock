"""Self-contained decision tests, with no account access or migration helpers."""
import unittest
from batch_transition import decisions
from switcher import Hold


class BatchChoicesTests(unittest.TestCase):
    def setUp(self):
        self.rows=[{'uuid':'a','label':'Alpha','state':'CONFLICT','source_color':'GREEN'},
                   {'uuid':'b','label':'Beta','state':'READY','source_color':'YELLOW'},
                   {'uuid':'c','label':'Gamma','state':'ALREADY_DESTINATION','source_color':'ORANGE'}]

    def test_no_automatic_survivor(self):
        with self.assertRaises(Hold):decisions(self.rows,{})

    def test_explicit_source_keeps_ready_and_omits_already_destination(self):
        self.assertEqual(decisions(self.rows,{'a':'source'}),(['a','b'],{'a':'GREEN'}))

    def test_exclusions_never_enter_apply_scope(self):
        self.assertEqual(decisions(self.rows,{'a':'skip','b':'skip'}),([],{}))

    def test_held_requires_exclusion(self):
        self.rows[1]['state']='HELD'
        with self.assertRaises(Hold):decisions(self.rows,{'a':'source'})
        self.assertEqual(decisions(self.rows,{'a':'source','b':'skip'}),(['a'],{'a':'GREEN'}))

    def test_unknown_uuid_and_invalid_choice_rejected(self):
        for choices in ({'z':'source'},{'a':'newest'},{'a':'source','c':'move'}):
            with self.subTest(choices=choices),self.assertRaises(Hold):decisions(self.rows,choices)


if __name__=='__main__':unittest.main()
