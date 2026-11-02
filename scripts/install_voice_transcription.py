"""Optional local CPU ASR setup. Never uploads audio or replaces existing settings."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'Systran/faster-whisper-small'
REVISION = '536b0662742c02347bc0e980a01041f333bce120'
MODEL_FILES = ['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.txt']


def configure(root, module_path, model_path):
    config_path = root/'nte-dialogue-ocr/voice-library-runtime.json'
    if config_path.exists():
        return False
    if not all((model_path/name).is_file() for name in MODEL_FILES):
        raise ValueError('转写模型不完整；请重新运行安装或选择完整的 faster-whisper 模型目录')
    # Relative paths survive moving the extracted installation as a whole.
    def portable(path):
        try:
            return Path(os.path.relpath(path, config_path.parent)).as_posix()
        except ValueError:
            return str(path.resolve())  # A model on another Windows drive.
    value = dict(module_path=portable(module_path), model_path=portable(model_path),
                 model_source=MODEL, model_revision=REVISION)
    temp = config_path.with_name('.voice-library-'+uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        # Windows rename refuses an existing destination, including one created
        # by another installer/user while the model was downloading.
        try:
            os.rename(temp,config_path)
        except FileExistsError:
            return False
    finally:
        temp.unlink(missing_ok=True)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-path',type=Path,help='Reuse an existing complete faster-whisper small model')
    args = parser.parse_args()
    if (ROOT/'nte-dialogue-ocr/voice-library-runtime.json').exists():
        print('已有自动转写配置，已保留。可以打开工具，在批量声线窗口重试转写。')
        return
    if sys.version_info[:2] != (3,12) or sys.maxsize <= 2**32:
        raise ValueError('请使用安装台词生成工具时创建的 Python 3.12（64 位）环境')
    module_path = ROOT/'local-voice-model/asr-runtime'
    print('正在安装独立的转写组件；不会修改台词工具的依赖环境…',flush=True)
    subprocess.run([sys.executable,'-m','pip','install','--only-binary=:all:','--upgrade','--target',str(module_path),
                    'faster-whisper==1.2.1','av==16.1.0'],check=True)
    sys.path.insert(0,str(module_path))
    model_path = args.model_path.resolve() if args.model_path else ROOT/'local-voice-model/models/faster-whisper-small'
    if args.model_path is None:
        from huggingface_hub import snapshot_download
        print('正在下载固定版本的中文转写模型（约 486 MB）；下载完成后可离线使用…',flush=True)
        snapshot_download(MODEL,revision=REVISION,local_dir=str(model_path),allow_patterns=MODEL_FILES)
    # Load successfully before publishing configuration; all later work is offline.
    from faster_whisper import WhisperModel
    model = WhisperModel(str(model_path),device='cpu',compute_type='int8',local_files_only=True)
    del model
    changed = configure(ROOT,module_path,model_path)
    print('自动转写已准备好。请重新打开台词工具；转写后仍须试听并逐字核对。' if changed else '已有配置已保留。')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('安装未完成：'+str(exc),file=sys.stderr)
        print('可以稍后重试；当前仍可试听片段后手动填写参考台词。',file=sys.stderr)
        raise SystemExit(1)
