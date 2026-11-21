"""Suggest a casting range from attributable Chinese dialogue cues."""
import re


TITLES = (
    (r'(?:婆婆|奶奶|老妇|老太太)$', 'female', 'old'),
    (r'(?:爷爷|老爷子|老伯|老翁)$', 'male', 'old'),
    (r'(?:女童|小女孩)$', 'female', 'child'),
    (r'(?:男童|小男孩)$', 'male', 'child'),
    (r'(?:少女|姑娘|年轻女子)$', 'female', 'young'),
    (r'(?:少年|小伙|年轻男子)$', 'male', 'young'),
    (r'(?:女士|姐姐|母亲|阿姨|女孩|娘)$', 'female', None),
    (r'(?:先生|哥哥|父亲|大叔|男孩)$', 'male', None),
    (r'(?:中年女子|中年女人)$', 'female', 'middle_aged'),
    (r'(?:中年男子|中年男人)$', 'male', 'middle_aged'),
    (r'(?:老人|老者)$', None, 'old'),
    (r'(?:小孩|儿童)$', None, 'child'),
    (r'青年$', None, 'young'),
)

# Match first-person forms at clause boundaries. A person addressing a child
# or describing a relative supplies no evidence about their own casting.
SELF = (
    (r'(?:可惜|想当年|要说|依|像)?老夫(?!人)', 'male', 'old'),
    (r'(?:可惜|想当年|要说|依)?老身|我这(?:个)?老太婆', 'female', 'old'),
    (r'我这(?:把|副)老骨头', None, 'old'),
    (r'我(?:是|这)(?:个|一名|一个)?(?:中年男人|中年男子)', 'male', 'middle_aged'),
    (r'我(?:是|这)(?:个|一名|一个)?(?:中年女人|中年女子)', 'female', 'middle_aged'),
    (r'(?:我是(?:个|一名|一个)?|身为)(?:女儿|女子|女人|妈妈|母亲|姐姐)', 'female', None),
    (r'(?:我是(?:个|一名|一个)?|身为)(?:儿子|男子|男人|爸爸|父亲|哥哥)', 'male', None),
    (r'我(?:只是|还是|是)(?:个|一个|一名)?小女孩', 'female', 'child'),
    (r'我(?:只是|还是|是)(?:个|一个|一名)?小男孩', 'male', 'child'),
    (r'我(?:是|这)(?:个|一名|一个)?年轻(?:女子|姑娘)', 'female', 'young'),
    (r'我(?:是|这)(?:个|一名|一个)?年轻(?:男子|小伙)', 'male', 'young'),
    (r'我(?:就)?是(?!来|去|替|找|想|要)[\u4e00-\u9fff]{1,5}的(?:奶奶|外婆|祖母)', 'female', 'old'),
    (r'我(?:就)?是(?!来|去|替|找|想|要)[\u4e00-\u9fff]{1,5}的(?:爷爷|外公|祖父)', 'male', 'old'),
)

DESCRIPTIONS = {
    '小女娃':('female','young'), '小姑娘':('female','young'), '年轻姑娘':('female','young'),
    '小伙子':('male','young'), '年轻男子':('male','young'), '年轻女子':('female','young'),
    '小女孩':('female','child'), '小男孩':('male','child'), '男童':('male','child'), '女童':('female','child'),
    '老伯':('male','old'), '老婆婆':('female','old'), '老先生':('male','old'), '老太太':('female','old'),
    '爷爷':('male','old'), '奶奶':('female','old'), '中年男子':('male','middle_aged'),
    '中年女子':('female','middle_aged'), '先生':('male',None), '女士':('female',None), '小姐':('female',None),
    '少女':('female','young'), '少年':('male','young'), '小学生':(None,'child'),
    '女大学生':('female','young'), '男大学生':('male','young'), '中年人':(None,'middle_aged'),
    '老奶奶':('female','old'), '老爷爷':('male','old'), '老朽':(None,'old'),
}
DESCRIPTOR = '(' + '|'.join(sorted(DESCRIPTIONS,key=len,reverse=True)) + ')'
SELF_DESCRIPTOR = re.compile(r'(?:我(?:就是|还是|是|这)(?:个|一名|一个|位|一位)?|身为(?:一名|一个)?)'+DESCRIPTOR+r'(?!的)')
VOCATIVE = re.compile(r'(?:^|[，。！？、…])\s*((?:你这(?:个|位)?|您这(?:个|位)?)'+DESCRIPTOR+r')(?!的)')
ADDRESSEE = re.compile(r'(?:^|[。！？；…])\s*([\u4e00-\u9fff·]{1,10})[，,！!、]\s*(?:你|您)')
INTERJECTIONS = {'哼','唉','哎','哎呀','嘿','呵','哈哈','啊','噢','喂','好','好吧','对了'}
QUOTED = re.compile(r'“[^”]*(?:”|$)|「[^」]*(?:」|$)|『[^』]*(?:』|$)|"[^"]*(?:"|$)')
REPORTED = re.compile(r'(?:说|喊|写道|念道|转述|学舌)[过道着]?\s*[：:,，].*$')
AGE = re.compile(r'(?:我(?:今年(?:才|刚满)?|才|已满|已经|刚满|年满)|今年(?:才|刚满)?)\s*'
                 r'([0-9]{1,3}|[零一二两三四五六七八九十]{1,3})\s*(?:周岁|岁|了$)')


def age_band(text):
    if text.isdecimal():
        years = int(text)
    else:
        tens,sep,ones = text.replace('两','二').partition('十')
        digits = '零一二三四五六七八九'
        if len(tens)>1 or len(ones)>1 or (tens and tens not in digits) or (ones and ones not in digits):
            return None
        if sep:
            years = (digits.index(tens) if tens else 1)*10 + (digits.index(ones) if ones else 0)
        else:
            years = digits.index(tens)
    if 0<years<=12:
        return 'child'
    if 18<=years<=29:
        return 'young'
    if 40<=years<=55:
        return 'middle_aged'
    return 'old' if 65<=years<=120 else None


def name_cues(name):
    for pattern, gender, age in TITLES:
        match = re.search(pattern,name)
        if match:
            return [(match[0],gender,age)]
    return []


def identity_cues(text, *, own=False, name='', addressed=False):
    """Return literal, attributable cues. Quoted speech never supplies identity."""
    text = QUOTED.sub(' ',text)
    cues = []
    for sentence in re.split(r'[。！？；\n]',text):
        report = REPORTED.search(sentence)
        if report:
            sentence = sentence[:report.start()]
        if own:
            previous_self = False
            for clause in re.split(r'[，、…：:]',sentence):
                clause = clause.strip()
                describes_self = False
                label = SELF_DESCRIPTOR.match(clause)
                if label:
                    cues.append((label[0],*DESCRIPTIONS[label[1]]))
                    describes_self = True
                for pattern,gender,age in SELF:
                    match = re.match(pattern,clause)
                    if match and not clause[match.end():].startswith('的'):
                        cues.append((match[0],gender,age))
                        describes_self = True
                match = AGE.match(clause)
                if match and (match[0].startswith('我') or previous_self) and (age := age_band(match[1])):
                    cues.append((match[0],None,age))
                    describes_self = True
                previous_self = describes_self
        else:
            if name:
                # An explicit vocative or predicate, not a reference to the NPC's relative.
                for match in re.finditer(re.escape(name)+r'(?:是(?:个|一位|一个)?|这(?:位|个)|(?=先生|女士|小姐|爷爷|奶奶))'+DESCRIPTOR+r'(?!的)',sentence):
                    cues.append((match[0],*DESCRIPTIONS[match[1]]))
            if addressed:
                for match in VOCATIVE.finditer(sentence):
                    cues.append((match[1],*DESCRIPTIONS[match[2]]))
    return list(dict.fromkeys(cues))


def summarize(cues):
    def unique(index):
        values = {cue[index] for cue in cues if cue[index]}
        return values.pop() if len(values)==1 else None
    return unique(1),unique(2),list(dict.fromkeys(cue[0] for cue in cues))

def suggest(speaker, own_lines, scene_lines=(), addressed=()):
    cues = name_cues(speaker)
    for text in own_lines:
        cues.extend(identity_cues(text,own=True))
    if len(speaker) > 1 and '{' not in speaker:
        for text in scene_lines:
            cues.extend(identity_cues(text,name=speaker))
    for text in addressed:
        cues.extend(identity_cues(text,addressed=True))
    return summarize(cues)


def scene_turns(rows,sid,name):
    """Keep actual ShowTalk item order; never join two branches or speakers with the same display name."""
    rows = [dict(row) for row in rows]
    named = name if (len(name)>1 and '{' not in name and
                     not any(r['speaker_id']!=sid and r.get('speaker')==name for r in rows)) else ''
    scenes = {}
    for r in rows:
        branch = r.get('branch','')
        parent,sep,position = branch.rpartition('/TalkItems/')
        key = (r.get('flow_id',''),r['state_id'],parent if sep else branch)
        number = position if sep else str(r['line_id'])
        scenes.setdefault(key,{}).setdefault(int(number) if number.isdecimal() else -1,[]).append(r)
    turns = []
    for scene,(_,sequence) in enumerate(sorted(scenes.items()),1):
        others = {r.get('speaker','') for group in sequence.values() for r in group if r['speaker_id']!=sid}
        calls_other = tuple(re.compile(re.escape(n)+r'[，,！!、]\s*(?:你|您)') for n in others if n)
        for number,group in sorted(sequence.items()):
            before,after = sequence.get(number-1,[]),sequence.get(number+1,[])
            addressed = (number>=0 and len(group)==len(before)==len(after)==1
                         and before[0]['speaker_id']==after[0]['speaker_id']==sid)
            for r in group:
                own = r['speaker_id']==sid
                text = r['raw_text']
                directed = (addressed and not any(pattern.search(text) for pattern in calls_other)
                            and all(who==name or who in INTERJECTIONS for who in ADDRESSEE.findall(text)))
                cues = identity_cues(r['raw_text'],own=own,name=named,addressed=directed)
                relation = 'self' if own else 'named' if named and named in r['raw_text'] else 'reply' if directed else 'other'
                turns.append(dict(scene=scene,turn=number,id=r.get('id',''),speaker=r.get('speaker',''),
                                  relation=relation,text=r['raw_text'],cues=cues))
    return turns


def quest_suggestion(rows, sid, name):
    turns = scene_turns(rows,sid,name)
    return summarize(name_cues(name)+[cue for t in turns for cue in t['cues']])


def for_speaker(catalog, sid, name, quest=None):
    if quest:
        rows = catalog.lines([quest])
        return quest_suggestion(rows, sid, name)
    own = catalog.db.execute('SELECT DISTINCT raw_text FROM lines WHERE speaker_id=?', (sid,))
    return suggest(name, (r[0] for r in own))
