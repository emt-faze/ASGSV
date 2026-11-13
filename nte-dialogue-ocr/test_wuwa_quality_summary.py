import unittest
from wuwa_quality_summary import spoken_key


class SpokenEquivalenceTests(unittest.TestCase):
    def test_unambiguous_spelling_and_erhua(self):
        for left,right in [('他记得它','她记得他'),('冒财','茂才'),('这里是……唉……','这里是，哎。'),('跑哪去了啊','跑哪儿去了啊')]:
            self.assertEqual(spoken_key(left),spoken_key(right))

    def test_missing_words_distinct_syllables_and_uncertain_name_stay_distinct(self):
        for left,right in [('你又跑哪去了啊','你又跑哪去了'),('冒财，冒财','冒财'),
                           ('监运使','监狱使'),('敬桧','静慧'),('不见影','不见眼')]:
            self.assertNotEqual(spoken_key(left),spoken_key(right))


if __name__ == '__main__':
    unittest.main()
