"""Suggest a casting range from attributable Chinese dialogue cues."""
import re


TITLES = (
    (r'(?:婆婆|奶奶|老妇|老太太)$', 'female', 'old'),
    (r'(?:爷爷|老爷子|老伯|老翁)$', 'male', 'old'),
    (r'(?:女童|女孩)$', 'female', 'child'),
    (r'(?:男童|男孩)$', 'male', 'child'),
    (r'(?:少女|姑娘|年轻女子)$', 'female', 'young'),
    (r'(?:少年|小伙|年轻男子)$', 'male', 'young'),
    (r'(?:女士|姐姐|母亲|阿姨|娘)$', 'female', None),
    (r'(?:先生|哥哥|父亲|大叔)$', 'male', None),
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
    (r'我(?:只是|还是|是)(?:个|一个|一名)?小女孩', 'female', 'child'),
    (r'我(?:只是|还是|是)(?:个|一个|一名)?小男孩', 'male', 'child'),
    (r'我(?:是|这个)(?:个|一名|一个)?年轻(?:女子|姑娘)', 'female', 'young'),
    (r'我(?:是|这个)(?:个|一名|一个)?年轻(?:男子|小伙)', 'male', 'young'),
)


def suggest(speaker, own_lines, scene_lines=(), addressed=()):
    evidence = []
    for pattern, gender, age in TITLES:
        if re.search(pattern, speaker):
            evidence.append((gender, age, speaker))
            break
    for text in own_lines:
        text = re.sub(r'[“「『].*?[”」』]', '', text)
        for clause in re.split(r'[。！？；，、…\n]', text):
            for pattern, gender, age in SELF:
                match = re.match(pattern, clause.strip())
                if match:
                    evidence.append((gender, age, match[0]))
    # Descriptions from other characters must explicitly name this NPC.
    if len(speaker) > 1 and '{' not in speaker:
        for text in scene_lines:
            for desc, gender, age in (('小姑娘','female','young'),('小伙子','male','young'),
                                      ('小女孩','female','child'),('小男孩','male','child'),
                                      ('老伯','male','old'),('老婆婆','female','old')):
                match = re.search(re.escape(speaker) + r'(?:是(?:一[个位])?|这[个位])' + desc, text)
                if match:
                    evidence.append((gender, age, match[0]))
    for text in addressed:
        match = re.match(r'(?:你这(?:个)?|你这个)(小女娃|小姑娘|小伙子|老伯|老婆婆)', text)
        if match:
            term = match[1]
            evidence.append(('female' if term in ('小女娃','小姑娘','老婆婆') else 'male',
                             'old' if term in ('老伯','老婆婆') else 'young', match[0]))
    def unique(index):
        values = {entry[index] for entry in evidence if entry[index]}
        return values.pop() if len(values) == 1 else None
    return unique(0), unique(1), list(dict.fromkeys(e[2] for e in evidence))


def quest_suggestion(rows, sid, name):
    # Require A → B → A in consecutive, unambiguous lines of one state.
    turns = {}
    for r in rows:
        if str(r['line_id']).isdigit():
            turns.setdefault((r['state_id'], int(r['line_id'])), []).append(r)
    addressed = []
    for (state, number), middle in turns.items():
        before = turns.get((state, number-1), [])
        after = turns.get((state, number+1), [])
        if len(middle)==len(before)==len(after)==1 and before[0]['speaker_id']==after[0]['speaker_id']==sid and middle[0]['speaker_id']!=sid:
            addressed.append(middle[0]['raw_text'])
    return suggest(name, (r['raw_text'] for r in rows if r['speaker_id']==sid),
                   (r['raw_text'] for r in rows if r['speaker_id']!=sid), addressed)


def for_speaker(catalog, sid, name, quest=None):
    if quest:
        rows = catalog.lines([quest])
        return quest_suggestion(rows, sid, name)
    own = catalog.db.execute('SELECT DISTINCT raw_text FROM lines WHERE speaker_id=?', (sid,))
    return suggest(name, (r[0] for r in own))
