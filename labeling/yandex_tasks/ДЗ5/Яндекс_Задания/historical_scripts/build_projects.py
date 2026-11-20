"""Prepare and create closed project scaffolds for the butterfly assignment."""
import copy
import base64
import hashlib
import io
import json
import os
import re
import sys
from html.parser import HTMLParser
from html import escape
from pathlib import Path

WORK = Path(__file__).resolve().parent.parent
ROOT = WORK.parents[1]
sys.path.insert(0, str(WORK))
from portable_runtime import API, APIError, save_json, instruction_html, load_token
ACCOUNT_ID = '6cf08dabc1685121c8ce951da950a23b'
PREFIX = 'dz5-butterflies-2026-839'
REASONS = {
    'missing': 'Пропущенный объект',
    'extra': 'Лишняя рамка',
    'bounds': 'Неверные границы',
    'duplicate': 'Повторное выделение',
    'merged': 'Объединение объектов',
    'hidden_parts': 'Достроенные скрытые части',
}
BBOX_EXAM_NOTICE = ('<section id="bbox-exam-scoring"><h2>Экзамен и допуск</h2>'
    '<p>В экзамене 8 фотографий. Для допуска нужны минимум 7 правильных ответов. '
    'На фото без бабочек верен ответ «Взрослых бабочек нет» без рамок. '
    'На фото с бабочками нужны все объекты без лишних рамок: каждая рамка должна '
    'соответствовать отдельной эталонной рамке при IoU не ниже 0,75. '
    'IoU — площадь пересечения рамок, делённая на площадь их объединения. '
    'Пропуск объекта, лишняя или некорректная рамка делают ответ по фото неверным. '
    'Проверка выполняется отдельно после отправки; завершённая попытка оплачивается '
    'независимо от получения допуска.</p></section>')
QUALIFICATION_NOTICE = ('<p><strong>Обучение и экзамен.</strong> '
    'На этих этапах кнопки «Пропустить» нет: выполните все примеры текущей страницы '
    'и нажмите «Отправить». Чтобы прекратить выполнение, используйте «Выйти». '
    'Упоминания кнопки «Пропустить» и ограничений за пропуски ниже относятся '
    'только к основным заданиям.</p>')


def clean_bbox_instructions(text):
    for old in [
        'Ошибку загрузки отмечайте отдельно.',
        'Не отмечайте сомнение как ошибку загрузки.',
        'Только если изображение действительно не загрузилось, выберите «Изображение не загрузилось» и опишите проблему в появившемся поле.',
    ]:
        text = text.replace(old, '')
    text = text.replace(
        'При техническом сбое сначала обновите страницу и проверьте соединение. Техническая проблема не заменяет ответ на сложное задание.',
        'Если фотография не появилась, обновите страницу и проверьте соединение. '
        'Не отвечайте «Бабочек нет», пока не видите изображение. '
        'Если сбой сохраняется, выйдите из задания и сообщите заказчику.')
    return text.replace('Пропуск, лишняя рамка или техническая ошибка делают ответ по фото неверным.',
                        'Пропуск объекта, лишняя или некорректная рамка делают ответ по фото неверным.')


def clean_review_instructions(text):
    text = text.replace(
        'Если фотография не загрузилась или рамки не отображаются из-за технической проблемы, оценить ответ нельзя. Выберите «Техническая проблема — не могу проверить» и опишите проблему в появившемся поле.',
        'Если фотография не появилась или редактор рамок не работает, обновите страницу и проверьте соединение. '
        'Не оценивайте разметку, пока не видите фотографию. Если сбой сохраняется, выйдите из задания и сообщите заказчику.')
    text = text.replace('Не отмечайте сомнение как техническую проблему.', '')
    text = text.replace(
        'При техническом сбое сначала обновите страницу и проверьте соединение. Техническая проблема не заменяет ответ на сложное задание.',
        'При сбое загрузки обновите страницу и проверьте соединение; если это не помогает, выйдите из задания и сообщите заказчику.')
    return text


def output(path, default=None):
    data = {'type': 'data.output', 'path': path}
    if default is not None:
        data['default'] = default
    return data


def eq(path, value):
    return {'type': 'condition.equals', 'data': output(path), 'to': value}


def conditional(condition, then, otherwise=None):
    result = {'type': 'helper.if', 'condition': condition, 'then': then}
    if otherwise is not None:
        result['else'] = otherwise
    return result


def schema(data, value, hint):
    return {'type': 'condition.schema', 'data': data, 'schema': value, 'hint': hint}


def required(hint):
    return {'type': 'condition.required', 'hint': hint}


def collect_components(value):
    result = set()
    if isinstance(value, dict):
        name = value.get('type', '')
        if name.startswith(('view.', 'field.', 'helper.', 'condition.', 'plugin.', 'action.')):
            result.add(name)
        for child in value.values():
            result.update(collect_components(child))
    elif isinstance(value, list):
        for child in value:
            result.update(collect_components(child))
    return result


def view_spec(kind):
    if kind == 'bbox':
        annotation = {
            'type': 'field.image-annotation', 'image': {'type': 'data.input', 'path': 'image'},
            'data': output('regions', []), 'fullHeight': True,
            'shapes': {'rectangle': True},
            'label': 'Выделите каждую взрослую бабочку отдельной рамкой',
            'hint': 'Учитывайте видимые усики и лапки. Скрытые части не достраивайте.',
            'validation': {'type': 'condition.any', 'conditions': [
                {'type': 'condition.all', 'conditions': [
                    eq('status', 'objects'),
                    schema(output('regions', []), {'type': 'array', 'minItems': 1}, 'Нарисуйте хотя бы одну рамку.'),
                ]},
                {'type': 'condition.all', 'conditions': [
                    eq('status', 'empty'),
                    # The annotation widget clears its binding when the last
                    # rectangle is deleted. An empty binding is valid here too.
                    {'type': 'condition.empty', 'data': output('regions'),
                     'hint': 'Для выбранного ответа удалите рамки.'},
                ]},
            ], 'hint': 'При наличии бабочек нужны рамки; при отсутствии бабочек рамок быть не должно.'},
        }
        statuses = [
            ('objects', 'Бабочки есть — все выделены рамками'),
            ('empty', 'Взрослых бабочек нет'),
        ]
        items = [
            {'type': 'view.text', 'content': 'Размечайте взрослых дневных и ночных бабочек, включая моль. Проверьте весь кадр, края и мелкие объекты.'},
            annotation,
        ]
        plugins = [{'type': 'plugin.field.image-annotation.hotkeys', 'modes': {'select': 'q', 'rectangle': 'w'}, 'confirm': 'Enter', 'cancel': 'Escape'}]
    else:
        # The annotation component needs a writable binding even in read-only mode.
        annotation = {
            'type': 'field.image-annotation', 'image': {'type': 'data.input', 'path': 'image'},
            'data': output('shown_regions', {'type': 'data.input', 'path': 'regions'}), 'disabled': True,
            'fullHeight': True, 'shapes': {'rectangle': True},
            'label': 'Проверьте всю фотографию и все предложенные рамки',
        }
        statuses = [
            ('accept', 'Разметка верна'),
            ('reject', 'Требует исправления'),
        ]
        reasons = {
            'type': 'field.checkbox-group', 'data': output('reasons', {}), 'preserveFalse': False,
            'label': 'Причины исправления (по желанию, на балл контроля не влияют)',
            'options': [{'value': key, 'label': label} for key, label in REASONS.items()],
        }
        items = [
            {'type': 'view.text', 'content': 'Сначала самостоятельно найдите бабочек, затем сравните с рамками. Одна ошибка означает, что ответ требует исправления. Пустой ответ верен, если бабочек действительно нет.'},
            annotation,
        ]
        plugins = [{
            'type': 'plugin.trigger', 'onChangeOf': output('status'), 'fireImmediately': False,
            'condition': {'type': 'condition.not', 'condition': eq('status', 'reject')},
            'action': {'type': 'action.set', 'data': output('reasons'), 'payload': {}},
        }]
    items.append({
        'type': 'field.radio-group', 'data': output('status'), 'label': 'Результат по всей фотографии',
        'options': [{'value': value, 'label': label} for value, label in statuses],
        'validation': required('Выберите результат по фотографии.'),
    })
    # Optional examples and explanations will be supplied with the future training tasks.
    items.append(conditional({'type': 'condition.not', 'condition': {'type': 'condition.empty', 'data': {'type': 'data.input', 'path': 'training_hint'}}},
                             {'type': 'view.text', 'label': 'Подсказка для обучения', 'content': {'type': 'data.input', 'path': 'training_hint'}}))
    if kind == 'bbox':
        items.append(conditional({
            'type': 'condition.equals',
            'data': {'type': 'data.input', 'path': 'show_reference', 'default': False}, 'to': True,
        }, {
            'type': 'field.image-annotation', 'image': {'type': 'data.input', 'path': 'image'},
            'data': output('reference_view', {'type': 'data.input', 'path': 'reference_regions'}),
            'disabled': True, 'fullHeight': True, 'shapes': {'rectangle': True},
            'label': 'Эталон для обучения — сравните со своими рамками',
        }))
    class Examples(HTMLParser):
        def __init__(self):
            super().__init__()
            self.images = []

        def handle_starttag(self, tag, attrs):
            values = dict(attrs)
            if tag == 'img':
                self.images.append({'type': 'view.image', 'url': values['src'], 'label': values.get('alt', '')})

    examples = Examples()
    examples.feed((WORK / 'instructions' / (kind + '.html')).read_text(encoding='utf-8'))
    items.append({'type': 'view.collapse', 'label': 'Примеры из инструкции',
                  'content': {'type': 'view.list', 'items': examples.images}})
    plugins.append({'type': 'plugin.toloka', 'layout': {'kind': 'pager'}})
    # Root checks do not depend on a conditional field having mounted already.
    root_conditions = [
        required('Выберите результат по фотографии.') | {'data': output('status')},
    ]
    if kind == 'review':
        root_conditions.append({'type': 'condition.any', 'conditions': [
            eq('status', 'accept'), eq('status', 'reject'),
        ], 'hint': 'Выберите: разметка верна или требует исправления.'})
    if kind == 'bbox':
        root_conditions.append({'type': 'condition.any', 'conditions': [eq('status', 'objects'), eq('status', 'empty')],
                                'hint': 'Укажите, есть ли взрослые бабочки на фотографии.'})
        root_conditions.append(annotation['validation'])
    config = {'view': {'type': 'view.list', 'items': items,
                       'validation': {'type': 'condition.all', 'conditions': root_conditions}}, 'plugins': plugins}
    locks = {name: '1.0.0' for name in collect_components(config)}
    locks.update({'core': '1.7.0', 'field.radio-group': '1.1.10', 'condition.required': '1.1.5', 'view.list': '1.0.5', 'view.image': '1.2.0', 'plugin.toloka': '1.1.8'})
    return {'type': 'tb', 'config': json.dumps(config, ensure_ascii=False), 'lock': locks,
            'settings': {key: key != 'showSkip' for key in ['showSkip', 'showTimer', 'showTitle', 'showFinish', 'showSubmit', 'showMessage', 'showFullscreen', 'showInstructions']}}


def prepare_instruction(kind):
    name = 'инструкция.html' if kind == 'bbox' else 'инструкция 2.html'
    original = (ROOT.parent / name).read_text(encoding='utf-8')
    (WORK / 'instructions' / (kind + '_original.html')).write_text(original, encoding='utf-8')
    body = instruction_html(original)
    if kind == 'review':
        body = body.replace(
            'Если интерфейс предусматривает указание причин или комментарий, перечислите найденные ошибки. Например: «Справа внизу пропущена бабочка» или «Рамка отрезает усики». Если ошибок несколько, укажите все, которые удалось найти.',
            'Выберите «Требует исправления». Причины и комментарий можно указать по желанию: они помогают разобрать ошибку, но на балл экзамена и контроля влияет только правильный вердикт. Например: «Справа внизу пропущена бабочка» или «Рамка отрезает усики».')
        body = body.replace('Сообщите о технической проблеме; не считайте такой кадр изображением без бабочек.',
                            'Выберите «Техническая проблема — не могу проверить» и опишите проблему в появившемся поле.')
        body = body.replace('Если объект нельзя уверенно распознать, сообщите заказчику, какая фотография и какой объект вызывают сомнение. Не угадывайте и не подменяйте сомнение выводом об отсутствии бабочек.',
                            'Если объект нельзя уверенно распознать по инструкции, воспользуйтесь кнопкой «Пропустить» и сообщите заказчику, какой объект вызывает сомнение. Не отмечайте сомнение как техническую проблему.')
    else:
        body = body.replace('Источники всех фото приведены в конце.', '')
        body = body.replace('Если объект нельзя уверенно распознать, передайте фотографию на дополнительный разбор.',
                            'Если объект нельзя уверенно распознать по инструкции, воспользуйтесь кнопкой «Пропустить» и сообщите заказчику, какой объект вызывает сомнение. Не отмечайте сомнение как ошибку загрузки.')
        body += '<h2>Как отправить ответ</h2><p>Если бабочки есть, нарисуйте все рамки и выберите «Бабочки есть — все выделены рамками». Если бабочек нет, выберите «Взрослых бабочек нет» и оставьте изображение без рамок. Только если изображение действительно не загрузилось, выберите «Изображение не загрузилось» и опишите проблему в появившемся поле. При сомнении в объекте используйте «Пропустить».</p>'
    body += '<p>При техническом сбое сначала обновите страницу и проверьте соединение. Техническая проблема не заменяет ответ на сложное задание. После 10 пропущенных подряд страниц доступ к рабочему пулу приостанавливается на 30 минут.</p>'
    body += '<h2>Допустимая точность границ</h2><p>Небольшой технический зазор и незначительная неточность в несколько пикселей допустимы, если все различимые видимые части объекта охвачены и нет большого запаса фона. Точное совпадение координат с рамкой составителя не требуется. Существенной ошибкой считаются заметно отрезанные видимые части, пропущенный объект, лишняя рамка или очевидно чрезмерный запас вокруг объекта.</p>'
    body = body.replace('Если вам попалась собственная разметка, сообщите об этом заказчику и не оценивайте её.',
                        'Если вам попалась собственная разметка, воспользуйтесь кнопкой «Пропустить» и сообщите заказчику об этом случае.')
    # Preserve the step numbers that were originally supplied by a CSS counter.
    count = 0
    def number_step(match):
        nonlocal count
        count += 1
        return '<p>' + str(count) + '. '
    body = re.sub(r'<p\s+class="step">', number_step, body)
    body = body.replace('<p class="bullet">', '<p>• ')
    from PIL import Image

    def compact_image(match):
        raw = base64.b64decode(match.group(1))
        with Image.open(io.BytesIO(raw)) as original_image:
            image = original_image.convert('RGB')
        image.thumbnail((1800, 1400))
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=90, optimize=True)
        return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')

    body = re.sub(r'data:image/png;base64,([A-Za-z0-9+/=]+)', compact_image, body)
    if kind == 'bbox':
        body = clean_bbox_instructions(body)
    else:
        body = clean_review_instructions(body)
    if len(body) > 2_097_152:
        raise RuntimeError('Инструкция превышает ограничение API в 2 МиБ.')
    (WORK / 'instructions' / (kind + '.html')).write_text(body, encoding='utf-8')
    return body


def project_payload(kind):
    learning_project = WORK / 'configs' / ('bbox_learning_project.json' if kind == 'bbox' else 'review_learning_project.json')
    if learning_project.exists():
        payload = json.loads(learning_project.read_text())
        ui = WORK / ('qualification_ui' if kind == 'bbox' else 'review_learning_ui')
        stem = 'training' if kind == 'bbox' else 'lesson'
        payload['task_spec']['view_spec'].update(markup=(ui/(stem+'.html')).read_text(),
            script=(ui/(stem+'.js')).read_text(),styles=(ui/(stem+'.css')).read_text())
        return payload
    common = {
        'interface_variant': {'type': 'string', 'required': True, 'hidden': True, 'allowed_values': ['qualification']},
        'photo_id': {'type': 'string', 'required': True, 'hidden': True},
        'image': {'type': 'url', 'required': True, 'hidden': False},
        'training_hint': {'type': 'string', 'required': False, 'hidden': False},
    }
    # Preserve the original wire schema for existing pools; the removed option
    # is absent from the interface and cannot be chosen by a worker.
    result = {'status': {'type': 'string', 'required': True, 'allowed_values': ['objects', 'empty', 'image_error', 'uncertain'] if kind == 'bbox' else ['accept', 'reject', 'image_error', 'uncertain']},
              'comment': {'type': 'string', 'required': False}}
    if kind == 'bbox':
        # The interface requires a consistent region list. Optional API schema
        # lets hidden controls compare status alone, without exact coordinates.
        result['regions'] = {'type': 'json', 'required': False}
        result['reference_view'] = {'type': 'json', 'required': False}
        common['reference_regions'] = {'type': 'json', 'required': False, 'hidden': False}
        common['show_reference'] = {'type': 'boolean', 'required': False, 'hidden': False}
        title = 'Выделите взрослых бабочек рамками'
        description = 'Найдите всех взрослых дневных и ночных бабочек на фотографии. Каждую выделите отдельной рамкой по инструкции.'
    else:
        common.update({
            'regions': {'type': 'json', 'required': True, 'hidden': False},
            'source_assignment_id': {'type': 'string', 'required': False, 'hidden': True},
            'source_worker_id': {'type': 'string', 'required': False, 'hidden': True},
            'annotation_id': {'type': 'string', 'required': True, 'hidden': True},
        })
        result['reasons'] = {'type': 'json', 'required': False}
        result['shown_regions'] = {'type': 'json', 'required': False}
        title = 'Проверьте рамки вокруг взрослых бабочек'
        description = 'Проверьте всю фотографию: все ли взрослые бабочки выделены, нет ли лишних рамок и правильно ли проведены границы.'
    illustrated_instruction = prepare_instruction(kind)
    image_map_path = WORK / 'instructions/image_urls.json'
    if image_map_path.exists():
        hosted = json.loads(image_map_path.read_text())
        filenames = (['bbox_1.jpg', 'bbox_2.jpg', 'bbox_3.jpg'] if kind == 'bbox' else
                     ['bbox_1.jpg', 'review_2.jpg', 'bbox_2.jpg', 'bbox_3.jpg'])
        picture_index = 0

        def hosted_source(match):
            nonlocal picture_index
            metadata = hosted[filenames[picture_index]]
            assert metadata['verified'] is True and metadata['url'].startswith('https://i.ibb.co/')
            picture_index += 1
            return 'src="' + escape(metadata['url'], quote=True) + '"'

        text_instruction = re.sub(r'src="data:image/jpeg;base64,[^"]+"', hosted_source, illustrated_instruction)
        assert picture_index == len(filenames) and 'data:image' not in text_instruction
        text_instruction = text_instruction.replace('<img ', '<img width="100%" style="max-width:100%;height:auto;display:block;margin:16px auto;" ')
        (WORK / 'instructions' / (kind + '_published.html')).write_text(text_instruction, encoding='utf-8')
    else:
        # Keep the interface examples until a verified image host is available.
        text_instruction = re.sub(r'<figure\b[^>]*>.*?</figure>', '', illustrated_instruction, flags=re.S)
        text_instruction = '<p><strong>Иллюстрации доступны в блоке «Примеры из инструкции» в интерфейсе задания.</strong></p>' + text_instruction
    if kind == 'bbox':
        text_instruction += BBOX_EXAM_NOTICE
        (WORK / 'instructions/bbox_published.html').write_text(text_instruction, encoding='utf-8')
    text_instruction = QUALIFICATION_NOTICE + text_instruction
    (WORK / 'instructions' / (kind + '_published.html')).write_text(text_instruction, encoding='utf-8')
    return {'public_name': title, 'public_description': description,
            'private_comment': PREFIX + ':' + kind,
            'public_instructions': text_instruction,
            'task_spec': {'input_spec': common, 'output_spec': result, 'view_spec': view_spec(kind)},
            'assignments_issuing_type': 'AUTOMATED', 'assignments_automerge_enabled': False}


class Drafts:
    def __init__(self):
        token = load_token()
        self.api = API(token)
        self.state_path = WORK / 'state/objects.json'
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {'account_id': ACCOUNT_ID, 'objects': {}, 'pending': {}}
        account = self.api.get('requester')
        if str(account['id']) != self.state['account_id']:
            raise RuntimeError('Заказчик отличается от проверенного командного аккаунта.')
        print('Заказчик', account['public_name'], 'баланс', account['balance'], flush=True)
        save_json(WORK / 'state/account.json', account)

    def ensure(self, key, collection, payload, marker_field='private_comment'):
        target = self.state['objects'].get(key)
        if target:
            result = self.api.get(collection + '/' + str(target))
        else:
            marker = payload[marker_field]
            matches = [value for value in self.api.all(collection) if value.get(marker_field) == marker]
            if len(matches) > 1:
                raise RuntimeError('Несколько объектов с маркером ' + key)
            if matches:
                result = matches[0]
            else:
                if key in self.state['pending']:
                    raise RuntimeError('Результат прошлого создания ещё не подтверждён для ' + key)
                self.state['pending'][key] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                save_json(self.state_path, self.state)
                try:
                    result = self.api.post(collection, payload)
                except APIError as error:
                    if error.status < 500:
                        self.state['pending'].pop(key, None)
                        save_json(self.state_path, self.state)
                    raise
            self.state['objects'][key] = str(result['id'])
            self.state['pending'].pop(key, None)
            save_json(self.state_path, self.state)
        if result.get('owner', {}).get('myself') is not True:
            raise RuntimeError('Принадлежность объекта не подтверждена для ' + key)
        save_json(WORK / 'state' / (key + '.json'), result)
        print(key, result['id'], result.get('status', ''), flush=True)
        return result


def build():
    for kind in ['bbox', 'review']:
        payload = project_payload(kind)
        save_json(WORK / 'configs' / (kind + '_project.json'), payload)
    print('Локальные конфигурации подготовлены.', flush=True)


def create_projects():
    drafts = Drafts()
    for kind in ['bbox', 'review']:
        payload = json.loads((WORK / 'configs' / (kind + '_project.json')).read_text())
        drafts.ensure(kind + '_project', 'projects', payload)


def update_projects():
    drafts = Drafts()
    for kind in ['bbox', 'review']:
        project_id = drafts.state['objects'][kind + '_project']
        payload = json.loads((WORK / 'configs' / (kind + '_project.json')).read_text())
        result = drafts.api.request('PUT', '/api/v1/projects/' + project_id, body=payload)
        save_json(WORK / 'state' / (kind + '_project.json'), result)
        print(kind, project_id, 'обновлён', flush=True)


def skip_rule():
    return {
        'collector_config': {'type': 'SKIPPED_IN_ROW_ASSIGNMENTS'},
        'rules': [{
            'conditions': [{'key': 'skipped_in_row_count', 'operator': 'GTE', 'value': 10}],
            'action': {'type': 'RESTRICTION_V2', 'parameters': {
                'scope': 'POOL', 'duration_unit': 'MINUTES', 'duration': 30,
                'private_comment': '10 пропущенных подряд страниц; пауза для проверки инструкции и соединения.',
            }},
        }],
    }


def update_main_quality():
    drafts = Drafts()
    for kind in ['bbox', 'review']:
        key = kind + '_main'
        pool_id = drafts.state['objects'][key]
        actual = drafts.api.get('pools/' + pool_id)
        if actual['status'] != 'CLOSED' or drafts.api.all('assignments', pool_id=pool_id):
            raise RuntimeError('Обновление рассчитано только на закрытые пулы без выдач.')
        path = WORK / 'configs' / (key + '.json')
        payload = json.loads(path.read_text())
        configs = payload.setdefault('quality_control', {}).setdefault('configs', [])
        configs[:] = [c for c in configs if c['collector_config']['type'] != 'SKIPPED_IN_ROW_ASSIGNMENTS']
        configs.append(skip_rule())
        save_json(path, payload)
        drafts.api.request('PUT', '/api/v1/pools/' + pool_id, body=payload)
        verified = drafts.api.get('pools/' + pool_id)
        assert verified['status'] == 'CLOSED'
        for expected in configs:
            matches = [c for c in verified['quality_control']['configs']
                       if c['collector_config']['type'] == expected['collector_config']['type']]
            assert len(matches) == 1 and matches[0]['rules'] == expected['rules']
        save_json(WORK / 'state' / (key + '.json'), verified)
        print(key, 'правила подтверждены, CLOSED', flush=True)


def create_closed_pools():
    drafts = Drafts()
    for kind in ['bbox', 'review']:
        skill_payload = {
            'name': 'Бабочки — допуск ' + kind + ' (ШАД-839)',
            'private_comment': PREFIX + ':' + kind + '_skill',
            'hidden': True,
        }
        skill = drafts.ensure(kind + '_skill', 'skills', skill_payload)
        project_id = drafts.state['objects'][kind + '_project']
        training_payload = {
            'project_id': project_id,
            'private_name': 'Обучение ' + kind + ' — 8 примеров [подготовка]',
            'inherited_instructions': True,
            'may_contain_adult_content': False,
            'assignment_max_duration_seconds': 1800,
            'mix_tasks_in_creation_order': True,
            'shuffle_tasks_in_task_suite': False,
            'training_tasks_in_task_suite_count': 1,
            'task_suites_required_to_pass': 8,
            'retry_training_after_days': 1,
        }
        save_json(WORK / 'configs' / (kind + '_training.json'), training_payload)
        training = drafts.ensure(kind + '_training', 'trainings', training_payload, 'private_name')
        if training['status'] != 'CLOSED':
            raise RuntimeError('Обучение должно оставаться закрытым.')
        for stage in ['exam', 'main']:
            exam = stage == 'exam'
            language_filter = {'category': 'profile', 'key': 'languages', 'operator': 'IN', 'value': 'RU'}
            skill_filter = {'category': 'skill', 'key': str(skill['id']), 'operator': 'EQ', 'value': None if exam else 100}
            payload = {
                'project_id': project_id,
                'private_name': ('Экзамен ' if exam else 'Основной пул ') + kind + ' [подготовка]',
                'private_comment': PREFIX + ':' + kind + '_' + stage,
                'may_contain_adult_content': False,
                'will_expire': '2026-12-01T00:00:00Z',
                'reward_per_assignment': (6 if kind == 'bbox' else 2) if exam else (3.2 if kind == 'bbox' else 0.5),
                'assignment_max_duration_seconds': 3600 if exam else 1800,
                'auto_accept_solutions': not (exam and kind == 'bbox'),
                'defaults': {'default_overlap_for_new_task_suites': 10 if exam else (1 if kind == 'bbox' else 2)},
                'filter': {'and': [language_filter, skill_filter]},
            }
            if exam:
                count = 8 if kind == 'bbox' else 12
                minimum = 7 if kind == 'bbox' else 11
                payload['public_description'] = (
                    f'Экзамен: {count} фотографий на одной странице, допуск от {minimum} правильных ответов. '
                    f'Оплата завершённой страницы — {payload["reward_per_assignment"]} ₽. '
                    'Критерии разметки и проверки указаны в инструкции проекта.'
                )
                if kind == 'bbox':
                    payload['auto_accept_period_day'] = 1
                    payload['public_description'] += ' Ответы проверяются в течение 1 дня. Завершённая попытка оплачивается независимо от получения допуска.'
                else:
                    # With exactly 12 control answers, >=90% is equivalent to at least 11 correct.
                    payload['quality_control'] = {'configs': [{
                        'collector_config': {'type': 'GOLDEN_SET', 'parameters': {'history_size': 12}},
                        'rules': [{
                            'conditions': [
                                {'key': 'golden_set_answers_count', 'operator': 'EQ', 'value': 12},
                                {'key': 'golden_set_correct_answers_rate', 'operator': 'GTE', 'value': 90},
                            ],
                            'action': {'type': 'SET_SKILL', 'parameters': {'skill_id': str(skill['id']), 'skill_value': 100}},
                        }],
                    }]}
            else:
                payload['mixer_config'] = {'real_tasks_count': 4, 'golden_tasks_count': 1, 'training_tasks_count': 0}
                payload['quality_control'] = {'configs': [{
                    'collector_config': {'type': 'GOLDEN_SET', 'parameters': {'history_size': 10}},
                    'rules': [{
                        'conditions': [
                            {'key': 'golden_set_answers_count', 'operator': 'GTE', 'value': 10},
                            {'key': 'golden_set_correct_answers_rate', 'operator': 'LT', 'value': 80},
                        ],
                        'action': {'type': 'RESTRICTION_V2', 'parameters': {
                            'scope': 'POOL', 'duration_unit': 'DAYS', 'duration': 1,
                            'private_comment': 'Менее 8 правильных ответов на последние 10 контрольных примеров; требуется разбор качества.',
                        }},
                    }],
                }]}
            if not exam:
                payload.setdefault('quality_control', {}).setdefault('configs', []).append(skip_rule())
            save_json(WORK / 'configs' / (kind + '_' + stage + '.json'), payload)
            result = drafts.ensure(kind + '_' + stage, 'pools', payload)
            if result['status'] != 'CLOSED':
                raise RuntimeError('Пул должен оставаться закрытым.')


if __name__ == '__main__':
    if sys.argv[1] == 'build':
        build()
    elif sys.argv[1] == 'create-projects':
        create_projects()
    elif sys.argv[1] == 'create-pools':
        create_closed_pools()
    elif sys.argv[1] == 'update-projects':
        update_projects()
    elif sys.argv[1] == 'update-main-quality':
        update_main_quality()
