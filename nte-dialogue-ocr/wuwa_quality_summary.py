"""Combine reproducible ASR evidence; never approve subjective acting quality."""
import argparse
import json
from pathlib import Path
from dialogue_generation import GenerationProject, atomic_text, file_digest
from prepared_playback import text_key

# Spelling-equivalent Mandarin words and one ordinary erhua variant. Deliberately
# exclude uncertain personal names and differing syllables such as 运/狱.
EQUIVALENCES = {'她': '他', '它': '他', '茂财': '冒财', '茂才': '冒财',
    '貌财': '冒财', '帽财': '冒财', '贸材': '冒财', '帽材': '冒财',
    '哎': '唉', '哪儿': '哪', '华虚院': '华胥院'}


def spoken_key(text):
    for source in sorted(EQUIVALENCES, key=len, reverse=True):
        text = text.replace(source, EQUIVALENCES[source])
    return text_key(text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('project')
    parser.add_argument('reports', nargs='+')
    args = parser.parse_args()
    from dialogue_dubbing import voice_paths
    from game_profiles import WUWA_ROOT
    voice_paths(json.loads((WUWA_ROOT/'config.json').read_text(encoding='utf-8'))['dubbing'])
    project = GenerationProject.load(Path(args.project))
    reports = [(Path(p), json.loads(Path(p).read_text(encoding='utf-8'))) for p in args.reports]
    assert project.game_id == 'wuwa' and all(r['game_id'] == 'wuwa' for _,r in reports)
    output = dict(game_id='wuwa', human_listening_review=False,
        evaluation='Exact spoken text after explicit spelling/erhua equivalences in at least one complete transcription; no full expected sentence supplied to ASR',
        equivalences=EQUIVALENCES, source_reports=[dict(path=str(p),sha256=file_digest(p),
            model=r['model'],revision=r['revision'],glossary=r.get('glossary','')) for p,r in reports], items=[])
    for row in project.rows:
        audio = project.audio_path(row)
        assert audio
        candidates=[]
        for path, report in reports:
            for item in report['items']:
                if item['id'] == row.id:
                    assert item['expected'] == row.text and item['sha256'] == row.audio['sha256']
                    assert Path(item['audio']).resolve() == audio.resolve()
                    candidates.append(dict(item,source_report=str(path)))
        assert candidates
        matches=[c for c in candidates if spoken_key(c['transcript'])==spoken_key(row.text)
                 and c['peak'] > .005 and c['clipped_fraction'] < .001]
        chosen=dict((matches or candidates)[0])
        chosen['raw_content_check'] = chosen['content_check']
        chosen['content_check']='pass' if matches else 'review'
        chosen['basis']='exact_spoken_equivalence' if matches else 'unresolved_transcription_difference'
        chosen['transcription_evidence']=candidates
        output['items'].append(chosen)
    atomic_text(project.directory/'quality-report.json',json.dumps(output,ensure_ascii=False,indent=2))
    print(json.dumps(dict(passed=sum(i['content_check']=='pass' for i in output['items']),
                          review=sum(i['content_check']=='review' for i in output['items']))))


if __name__ == '__main__':
    main()
