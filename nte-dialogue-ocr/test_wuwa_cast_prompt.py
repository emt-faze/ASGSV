"""Casting regressions: attribute the literal evidence to the right person before authorizing TTS."""
import io
import json
import unittest
from unittest.mock import patch

from wuwa_cast_context import quest_suggestion, scene_turns, identity_cues
from wuwa_cast_prompt import casting_input, dialogue_excerpt, evidence_for, SYSTEM_PROMPT
from wuwa_daily_cast import infer


def row(n,sid,text,action='/0',number=None,name=None):
    return dict(id=f'{action}/{n}',speaker_id=sid,speaker=name or sid,raw_text=text,
                state_id='scene',flow_id='flow',branch=f'{action}/TalkItems/{n}',line_id=str(n+1 if number is None else number))


def excerpt(rows,sid):
    name=next(r['speaker'] for r in rows if r['speaker_id']==sid)
    return dialogue_excerpt(scene_turns(rows,sid,name),name,rows[0]['id'])


def ref(entry,quote=None):
    return dict(line=entry['id'],quote=quote or entry['text'])


class CastPromptTests(unittest.TestCase):
    def test_keeps_late_exchange_and_turn_order_with_bounded_dialogue(self):
        rows=[row(n,'掌勺','今日菜单已经备好。') for n in range(30)]
        rows += [row(30,'来客','你这小女娃说什么大话？'),row(31,'掌勺','客官，请尝一口再下结论。')]
        brief=excerpt(rows[::-1],'掌勺')
        middle=next(i for i,t in enumerate(brief) if '小女娃' in t['text'])
        self.assertEqual([t['turn'] for t in brief[middle-1:middle+2]],[29,30,31])
        self.assertEqual(brief[middle]['relation'],'reply')
        self.assertIn(('你这小女娃','female','young'),brief[middle]['cues'])
        self.assertLessEqual(len(brief),21)
        self.assertTrue(all(len(t['text'])<=240 for t in brief))

    def test_separate_branches_and_duplicate_names_do_not_supply_identity(self):
        rows=[row(0,'A','货到了。',name='巡夜人'),row(1,'B','你这小女娃。','/3'),
              row(2,'A','多谢。',name='巡夜人'),row(5,'B','巡夜人是个年轻女子。','/3'),
              row(6,'C','收到了。','/3',name='巡夜人')]
        self.assertEqual(quest_suggestion(rows,'A','巡夜人')[:2],(None,None))
        turn=next(t for t in scene_turns(rows,'A','巡夜人') if t['text']=='你这小女娃。')
        self.assertFalse(turn['cues'])

    def test_independent_gender_age_and_delivery_sources(self):
        rows=[row(0,'A','我是小霖的奶奶，今年七十二了。慢点，汤烫手。'),row(1,'B','奶奶，知道啦。')]
        brief=excerpt(rows,'A');own=brief[1]
        answer=dict(gender='female',age='old',evidence=dict(gender=ref(own,'我是小霖的奶奶'),
                    age=ref(own,'今年七十二了'),delivery=ref(own,'慢点，汤烫手。')))
        citations,issue=evidence_for(answer,brief,{},[])
        self.assertFalse(issue)
        self.assertEqual(len(citations),3)
        self.assertIn('七十二',citations[1])
        answer['evidence']['age']=ref(own,'汤烫手')
        self.assertIn('年龄',evidence_for(answer,brief,{},[])[1])

    def test_explicit_third_addressee_overrides_consecutive_turns(self):
        rows=[row(0,'甲','货送来了。'),row(1,'乙','小岚，你这小女娃怎么还在这里？'),
              row(2,'甲','那我先走了。'),row(3,'小岚','好。')]
        for turns in (rows,rows[:3]):
            self.assertEqual(quest_suggestion(turns,'甲','甲')[:2],(None,None))
        interjection=[rows[0],row(1,'乙','哼，你这小女娃！'),rows[2]]
        self.assertEqual(quest_suggestion(interjection,'甲','甲')[:2],('female','young'))

    def test_named_description_belongs_to_someone_elses_relative(self):
        self.assertEqual(identity_cues('小霖是个年轻女子的弟弟。',name='小霖'),[])
        self.assertTrue(identity_cues('小霖是个年轻女子。',name='小霖'))

    def test_quotes_relatives_and_reported_speech_cannot_ground_identity(self):
        for said in ('我爷爷说：“老夫还年轻呢。”','阿姨说道：老身这把年纪。',
                     '我不是一个年轻姑娘。','我是小女孩的爸爸。','我女儿在读书，今年十八岁。'):
            with self.subTest(said=said):
                cues=identity_cues(said,own=True)
                self.assertFalse(cues, cues)

    def test_high_confidence_cannot_use_another_speakers_real_words(self):
        rows=[row(0,'送货人','货送来了。'),row(1,'来客','老夫来帮你。'),row(2,'送货人','好，多谢！')]
        brief=excerpt(rows,'送货人');other=next(t for t in brief if t['speaker']=='来客')
        answer=dict(confidence=.99,gender='male',age='old',evidence=dict(gender=ref(other),age=ref(other),delivery=ref(brief[1])))
        self.assertIn('性别',evidence_for(answer,brief,{},[])[1])
        answer['evidence']['delivery']=ref(other)
        self.assertIn('角色本人',evidence_for(answer,brief,dict(gender='male',age='old'),[])[1])
        self.assertIn('相互矛盾',evidence_for(answer,brief,{},['gender'])[1])

    def test_two_indirect_age_statements_cannot_be_one_line_twice(self):
        rows=[row(0,'A','我已经退休多年。'),row(1,'A','我如今已过古稀。')]
        brief=excerpt(rows,'A')
        answer=dict(gender='female',age='old',evidence=dict(age=[ref(brief[1]),ref(brief[2])],delivery=ref(brief[1])))
        self.assertFalse(evidence_for(answer,brief,dict(gender='female'),[])[1])
        answer['evidence']['age']=[ref(brief[1]),ref(brief[1])]
        self.assertIn('年龄',evidence_for(answer,brief,dict(gender='female'),[])[1])

    def test_wire_brief_uses_stable_system_rules_and_keeps_dialogue_in_user_data(self):
        brief=excerpt([row(0,'A','我这把老骨头，明早再来。')],'A')
        voices=[dict(id='voc_qa',name='守艺',gender='male',age='old')]
        payloads=[]
        def capture(req,timeout):
            payloads.append(json.loads(req.data))
            return io.BytesIO(b'{"choices":[{"message":{"content":"{}"}}]}')
        with patch('wuwa_daily_cast.credential',return_value='test'),patch('wuwa_daily_cast.build_opener') as opener:
            opener.return_value.open.side_effect=capture
            for _ in range(2):infer('https://example.com','example','A',brief,voices,{})
        for body in payloads:
            system,user=body['messages']
            self.assertEqual(system,dict(role='system',content=SYSTEM_PROMPT))
            self.assertNotIn('明早再来',system['content'])
            content=json.loads(user['content'])
            self.assertEqual(content['dialogue'][1]['speaker'],'A')
            self.assertEqual(content['voices'],[['voc_qa','守艺','male','old']])
            self.assertEqual(content['confirmed'],{})
            self.assertNotIn('cues',content['dialogue'][1])
        self.assertEqual(len(payloads),2)

    def test_legacy_unattributed_citations_are_not_a_valid_new_profile(self):
        brief=excerpt([row(0,'向导','我们出发吧。')],'向导')
        self.assertTrue(evidence_for(dict(evidence=['我们出发吧。']),brief,{},[])[1])
        self.assertEqual(casting_input('向导',brief,[],{})['target'],'向导')
