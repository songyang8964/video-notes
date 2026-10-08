"""Speech-to-SRT when no usable subtitle exists.

Backends, tried in order unless configured: WhisperX (large-v3, initial prompt, word alignment),
faster-whisper, openai-whisper. All produce timed SRT. The initial prompt comes from the per-video
context (asr_prompt) or a named preset; there is no subject-specific default. For long videos
without a local GPU, transcribe on a cloud GPU (see colab/whisperx_for_uploading_file.ipynb) and place the SRT
next to the video.
"""
import shutil
import subprocess
from pathlib import Path

from .srt import write_srt

# Optional vocabulary presets for the ASR initial prompt (asr_preset in the video context).
PRESETS = {
    'networking': (
        'This is a technical session about data center networking and network operations. Technical '
        'terms include MPLS, MPLS-VPN, BGP, eBGP, iBGP, OSPF, IS-IS, MP-BGP, EVPN, VXLAN, VLAN, VRF, '
        'ECMP, LACP, Route Reflector, Route Policy, AS-PATH, Next Hop, Prefix, Core, Aggregation, ToR, '
        'Leaf, Spine, Rollback, Migration, Decommission and Failover.'),
}


class TranscriptionUnavailable(RuntimeError):
    pass


def available_backends(config):
    found = []
    if config.get('whisperx') or shutil.which('whisperx'):
        found.append('whisperx')
    for module, name in (('faster_whisper', 'faster-whisper'), ('whisper', 'openai-whisper')):
        try:
            __import__(module)
            found.append(name)
        except ImportError:
            pass
    return found


def _whisperx(video, out_dir, config, prompt, language):
    exe = config.get('whisperx') or shutil.which('whisperx')
    command = [exe, str(video), '--model', config.get('asr_model', 'large-v3'), '--output_dir', str(out_dir),
               '--output_format', 'srt', '--print_progress', 'True']
    if prompt:
        command += ['--initial_prompt', prompt]
    if language and language != 'auto':
        command += ['--language', language]
    # English output is aligned with WAV2VEC2_ASR_LARGE_LV60K_960H for word-accurate times.
    # Only pinned for English: other languages use WhisperX's per-language default aligner.
    if language == 'en':
        command += ['--align_model', config.get('align_model') or 'WAV2VEC2_ASR_LARGE_LV60K_960H']
    subprocess.run(command, check=True)
    result = out_dir / (video.stem + '.srt')
    if not result.is_file() or result.stat().st_size == 0:
        raise RuntimeError('WhisperX produced no SRT')
    return result


def _segments_to_srt(segments, target):
    cues = [dict(start=round(s['start'] * 1000), end=round(s['end'] * 1000), text=s['text'].strip())
            for s in segments if s['text'].strip()]
    write_srt(cues, target)
    return target


def _faster_whisper(video, out_dir, config, prompt, language):
    from faster_whisper import WhisperModel
    model = WhisperModel(config.get('asr_model', 'large-v3'), device=config.get('asr_device', 'auto'))
    segments, _info = model.transcribe(str(video), initial_prompt=prompt or None, vad_filter=True,
                                       language=None if language in (None, 'auto') else language)
    return _segments_to_srt([dict(start=s.start, end=s.end, text=s.text) for s in segments],
                            out_dir / (video.stem + '.srt'))


def _openai_whisper(video, out_dir, config, prompt, language):
    import whisper
    model = whisper.load_model(config.get('asr_model', 'large-v3'))
    result = model.transcribe(str(video), initial_prompt=prompt or None,
                              language=None if language in (None, 'auto') else language)
    return _segments_to_srt(result['segments'], out_dir / (video.stem + '.srt'))


BACKENDS = {'whisperx': _whisperx, 'faster-whisper': _faster_whisper, 'openai-whisper': _openai_whisper}


def transcribe(video: Path, out_dir: Path, config, context, log=print):
    out_dir.mkdir(parents=True, exist_ok=True)
    prompt = context.get('asr_prompt') or PRESETS.get(context.get('asr_preset', ''), '')
    language = context.get('language', 'auto')
    order = [config['asr_backend']] if config.get('asr_backend') else available_backends(config)
    if not order:
        raise TranscriptionUnavailable(
            'No subtitle file and no speech-recognition backend. Put an .srt next to the video, pass --srt, '
            'or install one: pip install "video-notes[asr]" (faster-whisper) or WhisperX.')
    errors = []
    for name in order:
        try:
            log(f'transcription: {name}')
            return BACKENDS[name](video, out_dir, config, prompt, language)
        except Exception as error:  # try the next backend, keep the reason
            errors.append(f'{name}: {error}')
    raise TranscriptionUnavailable('all transcription backends failed: ' + ' | '.join(errors))
