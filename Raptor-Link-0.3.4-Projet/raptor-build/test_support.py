import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path('RaptorLink').resolve()))
from support import Engagement, issue_url, version_tuple

class Support(unittest.TestCase):
    def test_version_parser(self):
        self.assertEqual(version_tuple('v0.3.11'), (0,3,11))
        self.assertEqual(version_tuple('0.4.0-beta'), (0,4,0))
        self.assertIsNone(version_tuple('latest'))

    def test_feedback_url_is_prefilled(self):
        url=issue_url('compatibility','Mystic Light','Test message',5,{'raptor_link':'0.3.11'},'MSI Mystic Light')
        self.assertEqual(urlparse(url).netloc,'github.com')
        q=parse_qs(urlparse(url).query)
        self.assertIn('[Compatibility]',q['title'][0])
        self.assertIn('MSI Mystic Light',q['body'][0])
        self.assertIn('5/5',q['body'][0])
        self.assertIn('0.3.11',q['body'][0])

    def test_rating_is_only_due_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'engagement.json'
            e=Engagement(path)
            e.data['seconds_used']=3601
            self.assertTrue(e.snapshot()['rating_due'])
            e.submit_rating(4)
            self.assertFalse(e.snapshot()['rating_due'])
            self.assertEqual(e.snapshot()['rating'],4)
            e.close()
            loaded=Engagement(path)
            self.assertTrue(loaded.snapshot()['rating_submitted'])
            self.assertEqual(loaded.snapshot()['rating'],4)

    def test_rating_later_snoozes(self):
        with tempfile.TemporaryDirectory() as tmp:
            e=Engagement(Path(tmp)/'engagement.json')
            e.data['seconds_used']=3601
            e.snooze_rating(7)
            self.assertFalse(e.snapshot()['rating_due'])

if __name__=='__main__':
    unittest.main()
