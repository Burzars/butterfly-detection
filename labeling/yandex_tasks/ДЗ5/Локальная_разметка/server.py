"""Local-only annotation app. No third-party services or Python dependencies."""
import argparse
import copy
import json
import math
import os
import threading
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DATASET = json.loads((ROOT / 'dataset.json').read_text())
ITEMS = {item['id']: item for item in DATASET['items']}
STATE = ROOT / 'saved' / 'annotations.json'
LOCK = threading.Lock()
REASONS = {'missing', 'extra', 'bounds', 'duplicate', 'merged', 'hidden_parts'}


def fresh_state():
    return {'schema_version': 1, 'dataset_fingerprint': DATASET['fingerprint'], 'annotations': {}}


def read_state():
    return json.loads(STATE.read_text()) if STATE.exists() else fresh_state()


def regions(value):
    if not isinstance(value, list) or len(value) > 200:
        raise ValueError('Неверный список рамок.')
    for box in value:
        if not isinstance(box, dict) or box.get('type') != 'rectangle':
            raise ValueError('Поддерживаются только прямоугольные рамки.')
        p1, p2 = box['data']['p1'], box['data']['p2']
        for point in [p1, p2]:
            if not isinstance(point, dict):
                raise ValueError('Неверные координаты рамки.')
            for axis in ['x', 'y']:
                v = point[axis]
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1:
                    raise ValueError('Координаты рамок должны быть числами от 0 до 1.')
        if p1['x'] >= p2['x'] or p1['y'] >= p2['y']:
            raise ValueError('У рамки должны быть положительные ширина и высота.')


def validate_annotation(photo_id, a):
    if photo_id not in ITEMS or not isinstance(a, dict):
        raise ValueError('Неизвестная фотография.')
    regions(a.get('gold_regions', []))
    regions(a.get('review_regions', []))
    for key in ['complete', 'review_initialized']:
        if key in a and not isinstance(a[key], bool):
            raise ValueError('Неверный признак готовности.')
    if a.get('gold_status') not in [None, 'objects', 'empty']:
        raise ValueError('Неверное наличие объектов.')
    if a.get('review_verdict') not in [None, 'accept', 'reject']:
        raise ValueError('Неверный вердикт review.')
    reasons = a.get('review_reasons', [])
    if not isinstance(reasons, list) or not set(reasons) <= REASONS:
        raise ValueError('Неверные причины ошибки.')
    for key in ['note', 'hint']:
        if not isinstance(a.get(key, ''), str) or len(a.get(key, '')) > 10000:
            raise ValueError('Комментарий слишком длинный.')
    if a.get('complete'):
        gold = a.get('gold_regions', [])
        status = a.get('gold_status')
        if status is None or (status == 'objects') != bool(gold):
            raise ValueError('Подтвердите наличие объектов и проверьте эталонные рамки.')
        item = ITEMS[photo_id]
        if 'training' in item['section'] and not a.get('hint', '').strip():
            raise ValueError('Для обучения добавьте объяснение правильного ответа.')
        if 'review' in item['section']:
            if not a.get('review_initialized') or a.get('review_verdict') is None:
                raise ValueError('Подготовьте предъявляемый вариант и вердикт review.')
            same = a.get('review_regions', []) == gold
            if a['review_verdict'] == 'accept' and (reasons or not same):
                raise ValueError('Верный вариант должен совпадать с эталоном и не иметь причин ошибки.')
            if a['review_verdict'] == 'reject' and (not reasons or same):
                raise ValueError('Для ошибки измените предъявляемые рамки и выберите причину.')


def save_state(value):
    STATE.parent.mkdir(exist_ok=True)
    value['saved_at'] = datetime.now(timezone.utc).isoformat()
    # A previous valid disk copy is retained as recovery, writes are atomic.
    if STATE.exists():
        previous = STATE.with_suffix('.previous.json')
        tmp_previous = previous.with_suffix('.tmp')
        tmp_previous.write_bytes(STATE.read_bytes())
        tmp_previous.replace(previous)
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(STATE)


def export_bundle(state):
    entries = []
    for item in DATASET['items']:
        a = state['annotations'].get(item['id'], {})
        validate_annotation(item['id'], a)
        entry = {'photo_id': item['id'], 'section': item['section'], 'slot': item['slot'],
                 'image': item['photo_url'], 'local_file': item['image'],
                 'sha256': item['sha256'], 'width': item['width'], 'height': item['height'],
                 'scenario': item['scenario'], 'complete': bool(a.get('complete')),
                 'gold': {'status': a.get('gold_status'), 'regions': template_builder_regions(a.get('gold_regions', []))},
                 'training_hint': a.get('hint', ''), 'note': a.get('note', ''),
                 'source': {'observation_url': item['observation_url'],
                            'license_code': item['license_code'], 'observer_login': item['observer_login']}}
        if 'review' in item['section']:
            entry['review'] = {'initialized': bool(a.get('review_initialized')),
                               'regions': template_builder_regions(a.get('review_regions', [])),
                               'expected_status': a.get('review_verdict'),
                               'expected_reasons': {key: True for key in a.get('review_reasons', [])}}
        entries.append(entry)
    return {'schema_version': 1, 'dataset_fingerprint': DATASET['fingerprint'],
            'exported_at': datetime.now(timezone.utc).isoformat(),
            'completed': sum(e['complete'] for e in entries), 'total': len(entries),
            'coordinate_format': 'template_builder_rectangle_shape_left_top_width_height_normalized_0_1',
            'annotations': copy.deepcopy(state['annotations']), 'items': entries}


def template_builder_regions(boxes):
    """The native TB format was verified by drawing and submitting in its editor.

    Keep the editor's p1/p2 working representation inside `annotations` for import.
    The transferable `items` contain the native shape/left/top/width/height format.
    """
    return [{'shape': 'rectangle', 'left': b['data']['p1']['x'], 'top': b['data']['p1']['y'],
             'width': b['data']['p2']['x'] - b['data']['p1']['x'],
             'height': b['data']['p2']['y'] - b['data']['p1']['y']} for b in boxes]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt, *args):
        if args and str(args[0]).startswith(('POST', 'PUT')):
            super().log_message(fmt, *args)

    def send_json(self, status, value):
        raw = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        route = urlsplit(self.path).path
        if route == '/api/state':
            with LOCK:
                return self.send_json(200, read_state())
        if route.startswith('/api/'):
            return self.send_json(404, {'error': 'Неизвестный адрес.'})
        if route.endswith('/') and route != '/':
            return self.send_error(404)
        return super().do_GET()

    def do_POST(self):
        try:
            origin = self.headers.get('Origin')
            if origin and origin != 'http://' + self.headers.get('Host', ''):
                return self.send_json(403, {'error': 'Запрос разрешён только с локального сайта.'})
            size = int(self.headers.get('Content-Length', '0'))
            if size > 5_000_000 or not self.headers.get('Content-Type', '').startswith('application/json'):
                raise ValueError('Ожидается JSON до 5 МБ.')
            payload = json.loads(self.rfile.read(size))
            with LOCK:
                state = read_state()
                if self.path == '/api/annotation':
                    photo_id, annotation = payload['photo_id'], payload['annotation']
                    validate_annotation(photo_id, annotation)
                    state['annotations'][photo_id] = annotation
                    save_state(state)
                    return self.send_json(200, {'saved': True, 'saved_at': state['saved_at']})
                if self.path == '/api/import':
                    if payload.get('dataset_fingerprint') != DATASET['fingerprint']:
                        raise ValueError('Это разметка другого набора фотографий.')
                    for photo_id, annotation in payload['annotations'].items():
                        validate_annotation(photo_id, annotation)
                    backups = ROOT / 'saved' / 'backups'
                    backups.mkdir(exist_ok=True, parents=True)
                    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                    (backups / (stamp + '.json')).write_text(json.dumps(state, ensure_ascii=False, indent=2))
                    state['annotations'].update(payload['annotations'])
                    save_state(state)
                    return self.send_json(200, state)
                if self.path == '/api/export':
                    bundle = export_bundle(state)
                    folder = ROOT / 'exports'
                    folder.mkdir(exist_ok=True)
                    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
                    name = 'annotations_66_' + stamp
                    (folder / (name + '.json')).write_text(json.dumps(bundle, ensure_ascii=False, indent=2))
                    (folder / (name + '.jsonl')).write_text(''.join(json.dumps(item, ensure_ascii=False) + '\n' for item in bundle['items']))
                    return self.send_json(200, {'bundle': bundle, 'path': 'exports/' + name + '.json'})
                return self.send_json(404, {'error': 'Неизвестный адрес.'})
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            return self.send_json(400, {'error': str(error)})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    os.chdir(ROOT)
    print(f'Локальная разметка: http://127.0.0.1:{args.port}', flush=True)
    print('Фото и разметка остаются в этой папке. Для остановки — Ctrl+C.', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
