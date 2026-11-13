"""Exercise the real caption loop with a silent probe player; no game input."""
import json
import threading
import time
from pathlib import Path
from PySide6 import QtCore
from app import _run_loop
from capture_windows import enable_dpi_awareness,list_game_windows
from dialogue_generation import atomic_text
from game_profiles import WUWA_ROOT
from generator_ocr import PlaybackFrameWatch
from prepared_playback import PreparedPlayback
from test_prepared_playback import Player
from wuwa_catalog import Catalog
from wuwa_live import load_prepared,TaskEvidence
from wuwa_scene import dialogue_controls

def main():
    enable_dpi_awareness()
    app=QtCore.QCoreApplication([])
    root=WUWA_ROOT
    config=json.loads((root/'config.json').read_text(encoding='utf-8'))
    config.update(live_fresh_text=True,capture_backend='wgc',wgc_background=True)
    catalog=Catalog(root)
    library,_,_,_=load_prepared(root,config,catalog)
    tasks=TaskEvidence(catalog.db)
    player=Player()
    events=[]
    observations=[]
    controls=[]
    controller=PreparedPlayback(library,player,events.append,allow_prefix=False,preserve_on_unavailable=True)
    window=next(w for w in list_game_windows(game_id='wuwa',install_root=config['install_root']) if not w.is_launcher)
    def changed(event):
        if event.get('capture_state') in {'blank','unavailable'}:
            controller.invalidate(unavailable=True)
        if 'frame_changed' in event:
            controller.invalidate(captured_at=event['frame_changed'])
    def capture_started(capture):
        watch=PlaybackFrameWatch(capture,config,changed)
        controls.append(watch)
        watch.start()
    def observed(value):
        value=dict(value,game_id='wuwa')
        observations.append(value)
        controller.observe(value)
    began=time.monotonic()
    try:
        _run_loop(window,config,threading.Event(),lambda e:None,seconds=12,
            records_root=root/'reports/caption-fix-live-records',on_capture=capture_started,
            on_full_frame=lambda p,e:dialogue_controls(p.image,root),on_observation=observed,
            on_visual_observation=lambda *a:controls[0].arm(*a),on_state=lambda s:changed(dict(capture_state=s)))
    finally:
        for watch in controls:
            watch.close()
        controller.close(5)
        catalog.close()
    played=[e for e in events if e['state']=='playing']
    result=dict(real_game=True,game_input_sent=False,audio_output=False,elapsed=time.monotonic()-began,
        observations=len(observations),texts=list(dict.fromkeys(o['body'] for o in observations)),
        cached_observations=sum(o['body_pixels_unchanged'] for o in observations),
        played=[dict(row_id=e['row_id'],text=e['text']) for e in played],
        maximum_active_players=player.max_active,errors=[e['message'] for e in events if e['state']=='error'])
    atomic_text(root/'reports/caption-fix-live-check.json',json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False))
    assert observations and not result['cached_observations'] and not result['errors'],result
    assert len(played)==1 and played[0]['row_id']=='b4bd53bebce04b6699a8d4420fce95c0',result

if __name__=='__main__':
    main()
