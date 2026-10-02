import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str((ROOT/'RaptorLink').resolve()))

from engine import DEFAULT, validate

class ClassicModeTests(unittest.TestCase):
    def test_experimental_rgb_is_off_by_default(self):
        cfg=validate(DEFAULT)
        self.assertFalse(cfg['settings']['experimental_rgb'])

    def test_experimental_rgb_can_be_enabled_explicitly(self):
        raw={'version':1,'settings':{'experimental_rgb':True},'targets':[]}
        cfg=validate(raw)
        self.assertTrue(cfg['settings']['experimental_rgb'])

    def test_feedback_rating_and_hidden_lab_remain_in_ui(self):
        html=(ROOT/'RaptorLink'/'web'/'index.html').read_text(encoding='utf-8')
        self.assertIn('id="page-feedback"',html)
        self.assertIn('id="manual-rating"',html)
        self.assertIn('id="rating-dialog"',html)
        self.assertIn('id="feedback-message"',html)
        self.assertIn('id="setting-experimental-rgb"',html)
        self.assertIn('id="experimental-rgb-panel" class="hidden"',html)

if __name__=='__main__':
    unittest.main()
