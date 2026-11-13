import unittest

from wuwa_cast_context import quest_suggestion, suggest


class ContextTests(unittest.TestCase):
    def test_addressee_is_not_the_speaker(self):
        rows = [dict(state_id='s', line_id=str(i), speaker_id=sid, raw_text=text)
                for i, sid, text in [(1,'A','我就是掌勺。'),(2,'B','你这小女娃说什么大话？'),
                                     (3,'A','客人莫要以貌取人。'),(4,'B','老夫提点你两句。')]]
        self.assertEqual(quest_suggestion(rows,'A','掌勺')[:2], ('female','young'))
        self.assertEqual(quest_suggestion(rows,'B','客人')[:2], ('male','old'))
        rows[2]['state_id'] = 'other'
        self.assertEqual(quest_suggestion(rows,'A','掌勺')[:2], (None,None))

    def test_quoted_and_conflicting_evidence(self):
        self.assertEqual(suggest('甲',['他说“老夫不信”，我也不信。','我爷爷来了。'])[:2], (None,None))
        self.assertEqual(suggest('甲',['老夫还行。','老身在此。'])[:2], (None,'old'))
