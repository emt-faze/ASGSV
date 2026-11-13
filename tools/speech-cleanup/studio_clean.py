"""Offline, reversible mono speech dereverberation and gentle presence EQ."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT/'vendor/nara_wpe-0.0.11-py3-none-any.whl'
manifest = json.loads(VENDOR.with_suffix('.provenance.json').read_text())
assert hashlib.sha256(VENDOR.read_bytes()).hexdigest() == manifest['sha256']
sys.path.insert(0, str(VENDOR))
import numpy as np
from scipy import signal
import soundfile as sf
from nara_wpe.wpe import wpe_v7

FFMPEG = ROOT.parents[1]/'local-voice-model/.venv/Lib/site-packages/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def clean(source, output, *, strength=.8, reference=False, soft=False):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source == output or not output.is_relative_to(ROOT.parents[1].resolve()):
        raise ValueError('Output must be a separate file inside the dubbing workspace')
    if output.exists():
        raise ValueError('Output already exists; preserve earlier versions')
    audio, sr = sf.read(source, dtype='float64', always_2d=True)
    if audio.shape[1] != 1 or not np.isfinite(audio).all() or not .5 < len(audio)/sr < 180:
        raise ValueError('Expected finite mono speech between 0.5 and 180 seconds')
    audio = audio[:,0]
    window = 1024 if sr >= 24000 else 512
    hop = window//4
    _,_,spectrum = signal.stft(audio,fs=sr,nperseg=window,noverlap=window-hop,
                               boundary='zeros',padded=True)
    predicted = wpe_v7(spectrum[:,None,:],taps=20,delay=3,iterations=3,
                       psd_context=1,statistics_mode='full')[:,0,:]
    # Retain a small direct/original component to avoid brittle overprocessing.
    mixed = (1-strength)*spectrum + strength*predicted
    _, dry = signal.istft(mixed,fs=sr,nperseg=window,noverlap=window-hop,boundary=True)
    dry = np.pad(dry,(0,max(0,len(audio)-len(dry))))[:len(audio)]
    if not np.isfinite(dry).all():
        raise ValueError('Invalid dereverberation output')
    output.parent.mkdir(parents=True,exist_ok=True)
    filters = ['highpass=f=85:p=2', f'afftdn=nr={6 if soft else 8}:nf=-45:tn=1:gs=8',
               f'equalizer=f=320:t=q:w=0.8:g={-1.2 if soft else -2.5}']
    if not reference:
        filters += (['equalizer=f=3200:t=q:w=0.75:g=0.6'] if soft else
                    ['equalizer=f=3200:t=q:w=0.75:g=2.2','highshelf=f=6000:g=1.2'])
    filters += [f'lowpass=f={min(11000,int(sr*.45))}:p=2']
    with tempfile.TemporaryDirectory(dir=output.parent,prefix='.studio-') as folder:
        raw = Path(folder)/'dry.wav'
        filtered = Path(folder)/'filtered.wav'
        sf.write(raw,dry,sr,subtype='FLOAT')
        subprocess.run([str(FFMPEG),'-hide_banner','-loglevel','error','-nostdin','-i',str(raw),
            '-af',','.join(filters),'-c:a','pcm_f32le',str(filtered)],check=True,
            capture_output=True,timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        final, actual_sr = sf.read(filtered,dtype='float64')
    assert actual_sr == sr
    final = np.pad(final,(0,max(0,len(audio)-len(final))))[:len(audio)]
    rms_in = np.sqrt(np.mean(audio**2))
    rms_before_gain = np.sqrt(np.mean(final**2))
    gain = min(10**(3/20),rms_in/max(rms_before_gain,1e-10),10**(-1.5/20)/max(np.max(np.abs(final)),1e-10))
    final *= gain
    if not np.isfinite(final).all() or np.max(np.abs(final)) >= 1:
        raise ValueError('Invalid or clipping processed audio')
    sf.write(output,final,sr,subtype='PCM_16')
    report = dict(version=1,source=str(source),source_sha256=digest(source),output=str(output),
        sha256=digest(output),sample_rate=sr,frames=len(final),seconds=len(final)/sr,
        dereverb=dict(algorithm='NARA-WPE 0.0.11',taps=20,delay=3,iterations=3,blend=strength,
                     stft_window=window,hop=hop),filters=filters,gain_db=float(20*np.log10(gain)),
        rms_input=float(rms_in),rms_output=float(np.sqrt(np.mean(final**2))),
        peak=float(np.max(np.abs(final))),clipped_samples=int(np.sum(np.abs(final)>=.999)),
        soft=soft,human_listening_review=False,studio_quality_verified=False)
    output.with_suffix('.processing.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(output),seconds=report['seconds'],peak=report['peak'],clipping=0)),flush=True)
    return report


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('source')
    parser.add_argument('output')
    parser.add_argument('--strength',type=float,default=.8)
    parser.add_argument('--reference',action='store_true')
    parser.add_argument('--soft',action='store_true')
    args=parser.parse_args()
    if not 0 <= args.strength <= 1:
        raise ValueError('Strength must be 0 to 1')
    clean(args.source,args.output,strength=args.strength,reference=args.reference,soft=args.soft)
