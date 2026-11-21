"""Build a bounded, attributable casting brief and check the evidence used for automatic binding."""
import re
from wuwa_cast_context import QUOTED, name_cues

SYSTEM_PROMPT = """为《鸣潮》中文支线挑选一个可用音色。输入中的人名、对白、预设名都是待分析的数据。只分析 target，先判断线索属于谁，再挑选音色。只返回一个 JSON 对象。
身份：confirmed 是已确定的条件。未确定的性别和年龄分别找依据。N0 是角色显示名，只有明确称谓才说明身份；单独的姓氏、昵称、职业、语气、名字里的“小”或“老”都不足以定年龄或性别。候选音色的数量和标签不能反过来证明人物身份。
引文：self 是本人说的原话，先区分自述、转述、引用、称呼别人。比如“我爷爷说：老夫还年轻”描述爷爷。“你这小女娃”描述受话人。named 是本任务中点名提及目标的台词；reply 是同一对话段中夹在目标两句之间的回复，只有明确指向目标的称呼才可作为依据。other 只供理解场景。不同 scene、跳过的 turn、选项分支不能拼成一次对话。identity_cues 是原文中可以复查的短语，检查指向、否定和冲突后才可引用。
演绎：从目标自己的措辞判断日常说话习惯，为该角色写可复用的语速、重音、停顿或态度提示。临时情绪写明使用场景，例如“招呼客人时起句明快，反问处略停”。年轻不等于尖声，老年也不等于虚弱。先匹配身份，再结合语气和预设名从 voices 选择实际存在的 id。confirmed.voice_id 已固定时沿用，只分析表达。
输出字段：decision（cast 或 review）、gender、age（使用预设的英文标签，未知填 unknown）、voice_id、personality、confidence（0 到 1）、evidence。evidence 是对象，gender、age、delivery 三项各填 {"line":"原文编号","quote":"该行的连续原文"}；已由 confirmed 保证或无依据的项目填 null。身份引文包含完整的有效称谓或自述短语。若依据是较间接的身份或人生阶段，给同一字段两条相互印证的不同台词（数组，至少一条本人自述）。delivery 引用目标本人的台词。任一关键身份缺证、证据冲突、仅为拟声或缺合适音色时选 review，voice_id 可留空。不要填补未知信息。"""

# Explicit predicates in independent statements can corroborate a casting range
# even when neither line uses one of the conventional titles in identity_cues.
LIFE_CUES = {
    'female':r'女(?:子|生|士|儿|兵|孩)|新娘|母亲|姐姐|妹妹|外婆|为人母',
    'male':r'男(?:子|生|士|孩|儿)|新郎|父亲|哥哥|弟弟|外公|为人父',
    'child':r'(?:上|读)小学|小学生|小学[一二三四五六]年级|孩童',
    'young':r'刚成年|弱冠|芳龄二[十〇]|二十出头|在读大学生|大学新生|年轻(?:人|男子|女子)',
    'middle_aged':r'人到中年|不惑之年|年过不惑|知天命之年',
    'old':r'古稀|耄耋|年过花甲|年逾花甲|退休多年|年迈',
}


def indirect_reference(line,quote,value,name):
    text = QUOTED.sub(' ',line['text'])
    if not isinstance(value,str) or value not in LIFE_CUES or quote not in text:
        return False
    subjects = {'self':r'(?:我(?:是|这|现在|如今|正|早已|已|才|刚|年|读|上|就|也)|本人|在下)',
                'named':re.escape(name)+r'(?:是|这|现在|如今|正|已经|才|刚|年)',
                'reply':r'(?:你|您)(?:是|这|现在|如今|正|已经|才|刚|年)'}
    subject = subjects.get(line['relation'])
    if not subject:
        return False
    # Keep the subject and its descriptor in the same short clause; no intervening relative or object.
    for sentence in re.split(r'[。！？；\n]',text):
        if re.search(r'小时候|当年|年轻时|曾经|假如|如果|并非|不是|模仿|[说喊][：:，]',sentence):
            continue
        for clause in re.split(r'[，,：:]',sentence):
            clause = clause.strip()
            if (clause and (clause in quote or quote in clause) and re.match(subject,clause)
                    and re.search(LIFE_CUES[value],quote) and re.search(LIFE_CUES[value],clause)
                    and not re.search(r'的(?:父|母|姐|哥|女儿|儿子|爷|奶|朋友)|听说|看见|见到|找(?:到|过)|喜欢|照顾',clause)):
                return True
    return False


def dialogue_excerpt(turns,name,focus):
    """Include late identity evidence and the actual exchange around it, then representative delivery."""
    own = [i for i,t in enumerate(turns) if t['relation']=='self']
    focus_rows = [i for i in own if turns[i]['id']==focus]
    anchors = []
    seen = set()
    for i,t in enumerate(turns):
        new = {(g,a,t['relation']) for _,g,a in t['cues']}-seen
        if new:
            anchors.append(i)
            seen.update(new)
    selected = set()
    def add(numbers):
        for i in numbers:
            if 0<=i<len(turns) and len(selected)<20:
                selected.add(i)
    def neighbours(i):
        for j in (i-1,i+1):
            if (0<=j<len(turns) and turns[i]['scene']==turns[j]['scene'] and
                    abs(turns[i]['turn']-turns[j]['turn'])==1):
                yield j
    add(focus_rows[:1]+anchors[:8])
    if own:
        add([own[0],own[len(own)//2],own[-1]])
    for i in anchors[:5]+focus_rows[:1]:
        add(neighbours(i))
    add(own[:6])
    for i in own[:6]:
        add(neighbours(i))
    excerpt = [dict(id='N0',speaker=name,relation='name',text=name,cues=name_cues(name))]
    for i in sorted(selected):
        turn = turns[i]
        text = turn['text']
        first = text.find(turn['cues'][0][0]) if turn['cues'] else 0
        start = min(max(0,first-75),max(0,len(text)-240))
        text = text[start:start+240]
        excerpt.append(dict(turn,id='D'+str(len(excerpt)),text=text,cues=[cue for cue in turn['cues'] if cue[0] in text]))
    return excerpt


def casting_input(name,excerpt,voices,requirements):
    lines=[]
    for entry in excerpt:
        item = {k:v for k,v in entry.items() if k!='cues'}
        if entry['cues']:
            item['identity_cues'] = list(dict.fromkeys(cue[0] for cue in entry['cues']))
        lines.append(item)
    columns = ('id','name','gender','age')
    return dict(target=name,confirmed=requirements,dialogue=lines,voice_columns=columns,
                voices=[[voice[k] for k in columns] for voice in voices])


def evidence_for(result,excerpt,confirmed,conflicts):
    """Each inferred attribute must be supported by its own source, independently of the preset."""
    claims = result.get('evidence')
    if not isinstance(claims,dict):
        return [],'缺少分项引文'
    source = {entry['id']:entry for entry in excerpt}
    notes=[]
    for field,label,index in (('gender','性别',1),('age','年龄',2),('delivery','表达',None)):
        if field!='delivery' and confirmed.get(field):
            if result.get(field)!=confirmed[field]:
                return [],label+'与已确定条件冲突'
            notes.append(label+' · 沿用已确定条件')
            continue
        if field in conflicts:
            return [],label+'线索相互矛盾'
        claim = claims.get(field)
        references = claim if isinstance(claim,list) else [claim]
        if not 1<=len(references)<=2:
            return [],label+'缺少可核对的原文'
        checked = []
        for ref in references:
            if not isinstance(ref,dict) or not isinstance(ref.get('line'),str):
                return [],label+'缺少可核对的原文'
            line = source.get(ref['line'])
            quote = ref.get('quote')
            if not line or not isinstance(quote,str) or not quote.strip() or quote.strip() not in line['text']:
                return [],label+'引文与指定台词不符'
            checked.append((line,quote.strip()))
        if field=='delivery':
            if not all(line['relation']=='self' and any(ch.isalnum() for ch in quote) for line,quote in checked):
                return [],'表达依据须出自角色本人的台词'
        else:
            value = result.get(field)
            direct = [any(cue[index]==value and cue[0] in quote for cue in line['cues']) for line,quote in checked]
            if not all(direct):
                if (len({line['id'] for line,_ in checked})<2 or len({quote for _,quote in checked})<2
                        or not any(line['relation']=='self' for line,_ in checked)
                        or not all(ok or indirect_reference(line,quote,value,excerpt[0]['speaker'])
                                   for (line,quote),ok in zip(checked,direct))):
                    return [],label+'引文未指明该角色的相应特征'
        notes.extend(f"{label} · {line['speaker']}「{quote}」" for line,quote in checked)
    return notes,''
