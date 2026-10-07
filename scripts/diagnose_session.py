"""Read-only session audit. Preserves capture data and writes evidence under reports/."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import statistics
import subprocess
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from rapidfuzz.fuzz import ratio

ROOT = Path(__file__).resolve().parents[1]


def api(path):
    with urllib.request.urlopen('http://127.0.0.1:8766/api/' + path, timeout=20) as response:
        return json.load(response)


def percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * percent / 100)]


def audit(session_id, output):
    session = api('sessions/' + session_id)
    events = api('events?session_id=' + session_id)['events']
    source = ROOT / '.data' / 'sessions' / session_id
    output.mkdir(parents=True, exist_ok=True)
    all_rows, segments, failures = [], [], []
    for manifest in sorted((source / 'segments').glob('*.jsonl')):
        rows = [json.loads(line) for line in manifest.read_text(encoding='utf-8').splitlines() if line.strip()]
        clip = manifest.with_suffix('.mp4')
        probe = subprocess.run(['ffprobe', '-v', 'error', '-count_frames', '-show_entries',
                                'stream=codec_type,codec_name,width,height,nb_read_frames,duration',
                                '-of', 'json', str(clip)], capture_output=True, text=True)
        streams = json.loads(probe.stdout).get('streams', []) if probe.returncode == 0 else []
        if probe.returncode:
            failures.append({'file': clip.name, 'error': probe.stderr.strip()})
        diffs = [b['source_time'] - a['source_time'] for a, b in zip(rows, rows[1:])]
        wall = (rows[-1]['monotonic_ns'] - rows[0]['monotonic_ns']) / 1e9 if len(rows) > 1 else 0
        entry = {'file': clip.name, 'frames': len(rows), 'streams': streams,
                 'first_source_time': rows[0]['source_time'] if rows else None,
                 'last_source_time': rows[-1]['source_time'] if rows else None,
                 'capture_wall_seconds': wall, 'effective_fps': (len(rows)-1)/wall if wall else None,
                 'max_source_gap_ms': max(diffs, default=0)*1000,
                 'source_gaps_over_200ms': sum(d > .2 for d in diffs),
                 'nonincreasing_times': sum(d <= 0 for d in diffs)}
        segments.append(entry)
        all_rows.extend((clip, row) for row in rows)
    missing, mismatched = [], []
    for event in events:
        for key in ('crop_path', 'start_frame_path', 'before_frame_path', 'best_frame_path', 'end_frame_path'):
            path = event.get(key)
            if path and not (source / path).is_file():
                missing.append({'event': event['sequence'], 'path': path})
        for asset in event.get('assets_json') or []:
            path = source / asset['path']
            if not path.is_file():
                missing.append({'event': event['sequence'], 'path': asset['path']})
            elif asset.get('sha256') and hashlib.sha256(path.read_bytes()).hexdigest() != asset['sha256']:
                mismatched.append({'event': event['sequence'], 'path': asset['path']})
    duplicate_candidates = []
    for a, b in zip(events, events[1:]):
        similarity = ratio(a['normalized_text'], b['normalized_text'])
        gap = b['start_time'] - (a['end_time'] or a['start_time'])
        if similarity >= 85 and gap < 2:
            duplicate_candidates.append({'first': a['sequence'], 'second': b['sequence'],
                                         'similarity': similarity, 'gap_seconds': gap})
    rows = [row for _, row in all_rows]
    boundaries = []
    for a, b in zip(segments, segments[1:]):
        if a['last_source_time'] is not None and b['first_source_time'] is not None:
            boundaries.append({'before': a['file'], 'after': b['file'],
                               'gap_ms': (b['first_source_time']-a['last_source_time'])*1000})
    frames_in_streams = sum(int(s.get('nb_read_frames', 0)) for part in segments for s in part['streams']
                           if s.get('codec_type') == 'video' and str(s.get('nb_read_frames', '')).isdigit())
    summary = {'session': session, 'segments': segments, 'boundaries': boundaries,
               'total_manifest_frames': len(rows), 'decoded_video_frames': frames_in_streams,
               'first_source_time': rows[0]['source_time'] if rows else None,
               'last_source_time': rows[-1]['source_time'] if rows else None,
               'native_resolutions': dict(Counter(f"{r.get('video_native_width')}x{r.get('video_native_height')}" for r in rows)),
               'timing_quality': dict(Counter(r['timing_quality'] for r in rows)),
               'self_estimated_timing_error_p95_ms': percentile([r['timing_error_ms'] for r in rows], 95),
               'audio_streams': sum(s.get('codec_type') == 'audio' for p in segments for s in p['streams']),
               'events': len(events), 'low_confidence_events': [e['sequence'] for e in events if e['confidence'] < .68],
               'truncated_events': [e['sequence'] for e in events if e['truncated']],
               'invalid_event_intervals': [e['sequence'] for e in events if e['end_time'] is None or e['end_time'] < e['start_time']],
               'duplicate_candidates': duplicate_candidates, 'missing_assets': missing,
               'hash_mismatches': mismatched, 'probe_failures': failures,
               'session_bytes': sum(p.stat().st_size for p in source.rglob('*') if p.is_file()),
               'code_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                               for folder in ('capture', 'events', 'ocr', 'detector') for p in (ROOT/folder).glob('*.py')}}
    (output / 'audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (output / 'events.json').write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding='utf-8')
    columns = ['sequence', 'start_time', 'end_time', 'original_text', 'confidence', 'truncated', 'review_status', 'event_id']
    with (output/'events.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
        writer.writeheader(); writer.writerows(events)
    sample_dir = output / 'samples'
    sample_dir.mkdir(exist_ok=True)
    sampled = []
    if rows:
        at = session['source_start']
        end = min(session['source_start']+session['requested_duration'], rows[-1]['source_time'])
        while at <= end:
            clip, record = min(all_rows, key=lambda item: abs(item[1]['source_time']-at))
            cap = cv2.VideoCapture(str(clip)); cap.set(cv2.CAP_PROP_POS_FRAMES, record['index'])
            ok, frame = cap.read(); cap.release()
            if ok:
                name = f"{at:07.1f}.jpg"
                cv2.imwrite(str(sample_dir/name), frame)
                sampled.append((at, frame, name))
            at += 10
    for offset in range(0, len(sampled), 12):
        page = np.full((3*278, 4*480, 3), 24, np.uint8)
        for index, (at, frame, _) in enumerate(sampled[offset:offset+12]):
            tile = cv2.resize(frame, (480, 250))
            for roi in session['config_json'].get('regions', []):
                x,y,w,h = (roi[k] for k in ('x','y','width','height'))
                cv2.rectangle(tile, (int(x*480),int(y*250)), (int((x+w)*480),int((y+h)*250)), (0,0,255), 1)
            x,y = (index%4)*480, (index//4)*278
            page[y:y+250,x:x+480] = tile
            cv2.putText(page, f'{int(at)//60:02}:{int(at)%60:02} ({at:.1f}s)', (x+8,y+269),
                        cv2.FONT_HERSHEY_SIMPLEX, .48, (240,240,240), 1)
        cv2.imwrite(str(output/f'contact-{offset//12+1:02}.jpg'), page)
    cards = []
    for event in events:
        crop = source / (event.get('crop_path') or '')
        if crop.is_file():
            import shutil
            shutil.copy2(crop, output/f"event-{event['sequence']:04}.png")
        cards.append(f"<article><b>#{event['sequence']} · {event['start_time']:.3f}–{event['end_time']} s · confiança {event['confidence']:.3f}</b>"
                     f"<p>{html.escape(event['original_text'])}</p><img src='event-{event['sequence']:04}.png'></article>")
    (output/'events.html').write_text('<!doctype html><meta charset="utf-8"><title>DubSync: diagnóstico de eventos</title>'
        '<style>body{background:#101824;color:#eee;font:15px Segoe UI;margin:24px}article{border:1px solid #35455a;padding:14px;margin:12px 0}img{max-width:100%;image-rendering:auto;min-height:45px}</style>'
        f'<h1>Eventos da sessão {session_id}</h1>' + ''.join(cards), encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('session_id')
    parser.add_argument('--watch', action='store_true')
    args = parser.parse_args()
    output = ROOT / 'reports' / ('2026-10-03-' + args.session_id[:8])
    output.mkdir(parents=True, exist_ok=True)
    if args.watch:
        with (output/'status.jsonl').open('a', encoding='utf-8') as log:
            while True:
                status = api('sessions/' + args.session_id)
                status['observed_at'] = datetime.now(timezone.utc).isoformat()
                log.write(json.dumps(status, ensure_ascii=False)+'\n'); log.flush()
                print(json.dumps({k:status.get(k) for k in ('state','current_source_time','frames_captured','progress','error')}, ensure_ascii=False), flush=True)
                if status['state'] in ('complete', 'failed', 'stopped'):
                    break
                time.sleep(10)
    summary = audit(args.session_id, output)
    print(json.dumps({'report': str(output), 'state': summary['session']['state'], 'events':summary['events'],
                      'frames':summary['total_manifest_frames'], 'audio_streams':summary['audio_streams']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
