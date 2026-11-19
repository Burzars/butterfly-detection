# Детекция бабочек: разметка данных и выбор модели

Финальный проект курса «Сбор и разметка данных», 2026.

**Задача.** На вход — фотография, на выходе — прямоугольные рамки всех взрослых бабочек. Бабочкой считаем «бабочку обывателя»: дневных бабочек, мотыльков и моль. Гусеницы, куколки, другие насекомые и тени не размечаются. Если бабочек нет — пустой список.

**Процесс:** фотографии → разметка рамок → независимая проверка → golden set → сравнение моделей → выбранное решение.



## Структура репозитория

```
data/
  sources_and_labels.jsonl       наш датасет: 400 фото с источниками и лицензиями
  exam/dataset.jsonl             экзаменационный набор из Контеста (разметки нет)
  exam/submission-template.jsonl шаблон ответа из Контеста
labeling/
  bbox/                          проект разметки рамок: инструкция, настройки, сырые ответы
  review/                        проект проверки: инструкция, настройки, вердикты
  golden_review/                 разбор спорных ответов и сборка golden set
  costs.md                       расходы по этапам
golden/
  golden.jsonl                   итоговая проверенная разметка
  corrections.jsonl              исправления с пояснениями (то же таблицей — corrections.md)
  golden_audit.jsonl             история каждого фото: разметка, вердикты, решение
models/                          код запуска моделей
predictions/                     предсказания всех моделей и конфигураций, журнал ошибок
eval/                            подсчёт метрик и лидерборд
submission/                      итоговый файл для Контеста
scripts/                         проверки
```

## Соглашения (для всех)

**Формат рамок — как в Контесте.** Координаты в долях от ширины и высоты изображения (от 0 до 1): `left`, `top` — левый верхний угол, `width`, `height` — размеры рамки.

```json
{"left": 0.1, "top": 0.25, "width": 0.3, "height": 0.5}
```

Модели и Яндекс Задания отдают рамки в своих форматах (пиксели, два угла и т. п.). Каждый переводит свой вывод в этот формат сам, до того как класть файл в репозиторий.

**Поворот фото.** Перед обработкой картинку открываем через `ImageOps.exif_transpose(Image.open(...))`, иначе фото с поворотом в EXIF модель увидит не так, как его видел исполнитель в браузере.

**Пустой список и ошибка — разные вещи.** `"boxes": []` — бабочек нет. Если картинка не загрузилась или модель упала, пишем `"boxes": null, "error": "причина"` и заносим случай в журнал ошибок.

## Форматы файлов

`data/sources_and_labels.jsonl` — одна строка на фото. Основные поля (полный список — в `data/README.md`):

```json
{"photo_id": 764266940, "photo_url": "https://inaturalist-open-data.s3.amazonaws.com/photos/764266940/original.jpg", "observation_url": "https://www.inaturalist.org/observations/415089031", "observer_login": "galpinmd", "license_code": "cc0", "source_group": "butterflies", "preliminary_label": "бабочка", "tags": ["маскировка", "земля или камень"]}
```

`source_group` — группа, из которой фото попало в выборку: `butterflies`, `moths`, `beetles`, `bees_wasps_ants`, `flies`, `caterpillars`, `plants`. По ней считаются метрики по группам. `preliminary_label` и `tags` — предварительный просмотр при сборе: их ставила модель GPT-6 Astra (см. `data/README.md`), это не эталон. Итоговая разметка — только в `golden/golden.jsonl`.

`golden/golden.jsonl` — итоговая разметка:

```json
{"photo_id": 123, "boxes": [{"left": 0.1, "top": 0.25, "width": 0.3, "height": 0.5}]}
{"photo_id": 124, "boxes": []}
```

`predictions/<модель>__<конфигурация>.jsonl` — предсказания, например `gdino_tiny__butterfly_t0.35.jsonl`:

```json
{"photo_id": 123, "boxes": [{"left": 0.1, "top": 0.25, "width": 0.3, "height": 0.5, "score": 0.83}], "error": null, "time_sec": 0.42}
```

`score` внутри рамки сохраняем: по нему можно подобрать порог без повторного запуска модели. В файл для Контеста `score` и `time_sec` не попадают.

## Воспроизведение

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

_TODO: команды запуска каждой модели, подсчёта метрик и сборки посылки._

## Лидерборд (на нашем golden set, 400 фото)

| Модель | Конфигурация | F1@0.5 | Precision | Recall | F1@0.75 | FP на фото без бабочек | Ошибок | Время, с/фото |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| GroundingDINO tiny | | | | | | | | |
| OWLv2 base | | | | | | | | |
| Qwen3-VL-2B-Instruct | | | | | | | | |

## Итоговая конфигурация

_TODO: модель и версия весов, текстовый запрос, порог, преобразования изображения, обработка рамок (NMS и т. п.)._

## Проверки

```bash
# не пересекается ли наш датасет с экзаменационным
python scripts/check_overlap.py data/sources_and_labels.jsonl data/exam/dataset.jsonl

# рамки поверх фото: эталон зелёным, предсказания красным
python scripts/draw_boxes.py data/sources_and_labels.jsonl --golden golden/golden.jsonl --pred predictions/<файл>.jsonl --sample 20

# корректен ли файл для Контеста
python scripts/check_submission.py submission/submission.jsonl data/exam/dataset.jsonl
```

## Ссылки

- Проект bbox в Яндекс Заданиях: _TODO_
- Проект review в Яндекс Заданиях: _TODO_
- Презентация: _TODO_

## Лицензии

Все фотографии — CC0 из iNaturalist. Авторы и ссылки на наблюдения — в `data/sources_and_labels.jsonl`.
