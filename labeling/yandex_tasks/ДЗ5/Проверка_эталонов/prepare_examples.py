"""Preserve the author's submission and apply reviewed, explicit corrections."""
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parent / 'Локальная_разметка'
sys.path.insert(0, str(APP))
import server

HINTS = {
    '764083569': 'На фото одна взрослая дневная бабочка. Нужна одна рамка вокруг всех её видимых частей, включая различимые усики и лапки. Рамка должна быть близка к силуэту, без большого запаса фона.',
    '763490149': 'На фото один взрослый мотылёк. Мотыльки и моль входят в целевой класс так же, как дневные бабочки. Выделите его одной рамкой, включая длинные видимые усики.',
    '763916061': 'На фото одна бабочка со сложенными крыльями. Обведите фактический видимый силуэт, включая усики и лапки. Не увеличивайте рамку до воображаемых расправленных крыльев.',
    '761031680': 'На фото шесть взрослых бабочек: пять у плодов и одна вверху у края кадра. Нужны шесть отдельных рамок. Перекрытие рамок допустимо; для каждой особи учитываются только её собственные видимые части. Части за границей фотографии не достраиваются.',
    '763530743': 'На фото одна бабочка, частично закрытая веткой. Выделите все её различимые видимые части одной рамкой. Ветку и фон между этими частями включать в прямоугольник допустимо, а скрытое продолжение бабочки достраивать не нужно.',
    '762642743': 'На фото один взрослый мотылёк крупным планом. Часть его тела или крыльев выходит за границу фотографии. Рамка должна охватить видимые части и закончиться на краю кадра. Скрытое продолжение не дорисовывайте.',
    '764240440': 'Взрослых бабочек на фото нет: показано другое насекомое. Правильный ответ — «Взрослых бабочек нет», без рамок. Не выделяйте любое крылатое насекомое только потому, что у него есть крылья.',
    '764381301': 'На фотографии растения и плоды; взрослых бабочек нет. Правильный ответ — «Взрослых бабочек нет», список рамок пустой. Не выделяйте листья, цветы, плоды и тени.',
    '763974284': 'Правильный вердикт — «Требует исправления». Рамка заметно отрезает переднюю часть бабочки и видимые усики; это существенная ошибка границ. Небольшая неточность в несколько пикселей сама по себе не повод отклонять ответ, если все различимые части охвачены и нет большого запаса фона.',
    '763489544': 'Правильный вердикт — «Разметка верна». Взрослый мотылёк выделен одной рамкой вместе с видимыми усиками. Небольшой технический зазор и незначительная неточность границы допустимы: от ревьюера не требуется точное совпадение с координатами составителя.',
    '764372011': 'Правильный вердикт — «Требует исправления». На фото жук, а не взрослая бабочка. Рамка вокруг него лишняя. Для этой фотографии правильная разметка должна быть пустой.',
    '763915339': 'Правильный вердикт — «Требует исправления». Помимо рамки вокруг бабочки добавлена лишняя рамка в левом верхнем углу, где находится деталь фона. Проверяйте каждую рамку и всю фотографию: наличие одной правильно выделенной бабочки не делает весь ответ верным.',
    '764367118': 'Правильный вердикт — «Разметка верна». На фото другое насекомое, взрослых бабочек нет. Пустой список рамок здесь является полноценным правильным ответом.',
    '763905764': 'Правильный вердикт — «Требует исправления». На фото есть взрослая бабочка, но в предъявленном ответе нет ни одной рамки. Объект пропущен. Пустой ответ допустим только на фотографиях без взрослых бабочек.',
    '294852448': 'Правильный вердикт — «Требует исправления». На фотографии две взрослые бабочки, а выделена только левая. Правая бабочка пропущена. Осматривайте весь кадр, включая области за пределами уже нарисованных рамок.',
    '763890362': 'Правильный вердикт — «Разметка верна». Бабочка, её видимые усики и лапки охвачены рамкой. Небольшой запас фона вокруг силуэта допустим. Отклонять нужно за существенную обрезку видимых частей или очевидно чрезмерный запас, а не за небольшое отличие координат.',
}


def rectangle(x1, y1, x2, y2):
    return {'type': 'rectangle', 'data': {'p1': {'x': x1, 'y': y1}, 'p2': {'x': x2, 'y': y2}}}


def main():
    state_path = APP / 'saved/annotations.json'
    source = state_path.read_bytes()
    source_hash = hashlib.sha256(source).hexdigest()
    backup = HERE / 'submission_original.json'
    if not backup.exists():
        backup.write_bytes(source)
        (HERE / 'dataset_original.json').write_bytes((APP / 'dataset.json').read_bytes())
    state = json.loads(source)
    dataset = json.loads((APP / 'dataset.json').read_text())
    assert len(state['annotations']) == 66 and all(a.get('complete') for a in state['annotations'].values())
    changes = []
    for photo_id, text in HINTS.items():
        a = state['annotations'][photo_id]
        if a['hint'] != text:
            changes.append({'photo_id': photo_id, 'field': 'hint', 'before': a['hint'], 'after': text})
            a['hint'] = text
    for photo_id in ['763905764', '762665197']:
        a = state['annotations'][photo_id]
        before = copy.deepcopy(a)
        a.update(review_regions=[], review_initialized=True, review_verdict='reject', review_reasons=['missing'])
        if photo_id == '762665197':
            a['hint'] = ''  # Author's request is kept in the audit, not exposed in control data.
        changes.append({'photo_id': photo_id, 'field': 'review_variant', 'before': before,
                        'after': copy.deepcopy(a), 'reason': 'Explicit request in author comment: remove presented frame and mark missing object.'})
    for photo_id, box in {
        '763530254': rectangle(.14, .288, .67, .593),
        '763058682': rectangle(.302, .265, .59, .86),
        '763734745': rectangle(.308, .380, .725, .667),
        '763478388': rectangle(.047, .205, .915, .900),
        '764083569': rectangle(.1989451144, .2000799724, .720, .8467666060),
        '762680274': rectangle(.392, .287, .763, .550),
        '763489544': rectangle(.230, .080, .725, .887),
        '667490055': [rectangle(.112, .214, .792, .901), rectangle(.290, 0, .792, .598)],
        '763490149': rectangle(.313, .0839788622, .5982778645, .9145314558),
        '763530743': rectangle(.272, .235, .6272867925, .4364062598),
        '761031680': [
            rectangle(.7507899388, 0, .907, .1864768237),
            rectangle(.518, .404, .747, .682),
            rectangle(.138, .3508201091, .3703859214, .5668161576),
            rectangle(.277, .5974095214, .536, .7580286019),
            rectangle(.3396671106, .3723342533, .581, .681),
            rectangle(.3680808345, .247, .581, .586),
        ],
    }.items():
        a = state['annotations'][photo_id]
        before = copy.deepcopy(a['gold_regions'])
        a['gold_regions'] = box if isinstance(box, list) else [box]
        if a['review_verdict'] == 'accept':
            a['review_regions'] = copy.deepcopy(a['gold_regions'])
        changes.append({'photo_id': photo_id, 'field': 'gold_and_correct_variant', 'before': before,
                        'after': a['gold_regions'], 'reason': 'Include visible antennae/legs; verdict remains accept.'})
    touched = {c['photo_id'] for c in changes}
    now = datetime.now(timezone.utc).isoformat()
    for photo_id in touched:
        state['annotations'][photo_id]['updated_at'] = now
    state['saved_at'] = now
    labels = {'missing': 'Пропущенный объект', 'extra': 'Лишняя рамка', 'bounds': 'Существенная ошибка границ',
              'duplicate': 'Повторное выделение', 'merged': 'Объединение объектов', 'hidden_parts': 'Достроенные скрытые части'}
    for item in dataset['items']:
        a = state['annotations'][item['id']]
        if item['id'] == '761031680':
            item.update(count='количество: 6, подтверждено при просмотре',
                        prepare_annotation='Шесть взрослых бабочек: пять у плодов и одна вверху. Каждую выделить отдельной рамкой.')
        if 'review' in item['section']:
            item.setdefault('original_scenario', item['scenario'])
            item.setdefault('original_prepare_annotation', item['prepare_annotation'])
            item['scenario'] = 'Корректный пример' if a['review_verdict'] == 'accept' else '; '.join(labels[r] for r in a['review_reasons'])
            item['prepare_annotation'] = ('Предъявляемый вариант проверен. Ожидаемый вердикт: «Разметка верна».' if a['review_verdict'] == 'accept'
                                        else 'Предъявляемый вариант содержит ошибку. Ожидаемый вердикт: «Требует исправления». Причины служат пояснением и не влияют на балл контроля.')
        server.validate_annotation(item['id'], a)
    assert hashlib.sha256(state_path.read_bytes()).hexdigest() == source_hash, 'Source changed during preparation; review new edits before writing.'
    tmp = state_path.with_suffix('.corrected.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    tmp.replace(state_path)
    (APP / 'dataset.json').write_text(json.dumps(dataset, ensure_ascii=False, indent=2))
    server.DATASET = dataset
    server.ITEMS = {i['id']: i for i in dataset['items']}
    result = server.export_bundle(state)
    (HERE / 'annotations_reviewed.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))
    (HERE / 'annotations_reviewed.jsonl').write_text(''.join(json.dumps(i, ensure_ascii=False) + '\n' for i in result['items']))
    (HERE / 'changes.json').write_text(json.dumps(changes, ensure_ascii=False, indent=2))
    print('Reviewed:', result['completed'], '/', result['total'], '| changes:', len(changes))


if __name__ == '__main__':
    main()
