# Детекция бабочек: разметка данных и выбор модели

Финальный проект курса «Сбор и разметка данных», 2026.

**Задача.** На вход — фотография, на выходе — прямоугольные рамки всех взрослых бабочек. Бабочкой считаем «бабочку обывателя»: дневных бабочек, мотыльков и моль. Гусеницы, куколки, другие насекомые и тени не размечаются. Если бабочек нет — пустой список.

**Процесс:**
1. фотографии (`data/`);
2. разметка рамок и независимая проверка в Яндекс Заданиях (`labeling/`);
3. golden set (`golden/`);
4. сравнение моделей (`models/`, `predictions/`, `eval/`);
5. выбранное решение (`submission/`).

## Структура репозитория

```
data/
  sources_and_labels.jsonl       наш датасет: 400 фото с источниками и лицензиями
  exam/dataset.jsonl             экзаменационный набор из Контеста (разметки нет)
  exam/submission-template.jsonl шаблон ответа из Контеста
labeling/
  bbox/                          проект разметки рамок: инструкция, настройки (settings.md), сырые ответы (raw/)
  review/                        проект проверки: инструкция, настройки (settings.md), вердикты (raw/)
  yandex_tasks/                  код создания проектов, загрузки заданий и проверки допуска, эталоны обучений и экзаменов
  golden_review/                 разбор спорных ответов и сборка golden set
  costs.md                       расходы по этапам
golden/
  golden.jsonl                   итоговая проверенная разметка
  corrections.jsonl              исправления с пояснениями (то же таблицей — corrections.md)
  golden_audit.jsonl             история каждого фото: разметка, вердикты, решение
  qa_check.md                    проверка качества: найденные ошибки и как их нашли
models/                          код запуска моделей
predictions/                     предсказания всех моделей и конфигураций, журнал ошибок
eval/                            подсчёт метрик и лидерборд
submission/                      итоговый файл для Контеста
scripts/                         скачивание фото и проверки
```

Рамки везде в долях кадра, как в Контесте: `{"left", "top", "width", "height"}`. Форматы файлов описаны в README соответствующих папок.

## Запуск

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1. Фото (в репозитории их нет). Наши 400 сверяются с датасетом по SHA-256.
python scripts/download_images.py                                                 # → data/images/
python scripts/download_images.py data/exam/dataset.jsonl --out data/exam/images  # экзамен Контеста
python scripts/check_overlap.py data/sources_and_labels.jsonl data/exam/dataset.jsonl  # наборы не пересекаются

# 2. Golden set из сырых ответов Яндекс Заданий и решений команды.
python labeling/golden_review/build_golden.py \
  --decisions labeling/golden_review/decisions/misha.json labeling/golden_review/decisions/nastya.json \
  --override labeling/golden_review/decisions/arsenii.json

# 3. Модели → predictions/  (команды — после переноса кода моделей, см. models/README.md)

# 4. Метрики и лидерборд; рамки поверх фото для разбора ошибок.
python eval/evaluate.py golden/golden.jsonl predictions/*.jsonl --by source_group --leaderboard eval/leaderboard.md
python scripts/draw_boxes.py data/sources_and_labels.jsonl --golden golden/golden.jsonl --pred predictions/<файл>.jsonl --sample 20

# 5. Посылка в Контест (команда сборки — после переноса кода моделей) и проверка формата.
python scripts/check_submission.py submission/submission.jsonl data/exam/dataset.jsonl
```

Подробнее:
- настройки разметки, допуска и контроля — `labeling/bbox/settings.md`, `labeling/review/settings.md`;
- пересчёт допуска исполнителей без сети и токена — `labeling/yandex_tasks/NOTES.md`;
- разбор спорных ответов и сборка эталона — `labeling/golden_review/README.md`.

## Результаты

- **Golden set:** 400 фото, 248 рамок, 160 фото без бабочек (`golden/README.md`).
- **Расходы на разметку:** 700 ₽ из 850 (`labeling/costs.md`).
- **Посылка в Контест:** 10 баллов из 10.

**Лидерборд** на нашем golden set, 400 фото. Заполняется после переноса предсказаний моделей:

| Модель | Конфигурация | F1@0.5 | Precision | Recall | F1@0.75 | FP на фото без бабочек | Ошибок | Время, с/фото |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| GroundingDINO tiny | | | | | | | | |
| OWLv2 base | | | | | | | | |
| Qwen3-VL-2B-Instruct | | | | | | | | |

**Итоговая конфигурация** (заполняется после переноса кода моделей): модель и версия весов, текстовый запрос, порог, преобразования изображения, обработка рамок.

## Ссылки

- Проект bbox в Яндекс Заданиях: [11893](https://tasks.yandex.ru/requester/project/11893).
- Проект review в Яндекс Заданиях: [11894](https://tasks.yandex.ru/requester/project/11894).
- Ссылки на проекты открываются только из аккаунта команды. Сами настройки, инструкции и все ответы сохранены в репозитории.
- Презентация: будет добавлена.

## Команда

- **Кирилл** — собрал датасет из 400 фото и проверил, что каждое фото подходит; собрал 66 дополнительных фото для обучений, экзаменов и контроля и придумал сценарии ошибок для ревью.
- **Максим** — проекты bbox и review в Яндекс Заданиях: обучения, экзамены, контроль качества, скрипт допуска по IoU, перенос ответов bbox в review; нарисовал эталонные рамки для 66 фото; запустил разметку и ревью.
- **Анастасия** — инструкции исполнителям для bbox и review; разбор отклонённых и спорных ответов.
- **Михаил** — разбор отклонённых и спорных ответов; итоговая модель и посылка в Контест.
- **Арсений** — репозиторий, проверка проектов, эталонов и результатов разметки, метрики, итоговая сборка golden set.

Где работали ИИ-модели:
- GPT-6 Astra — сбор и предварительный просмотр датасета (`data/README.md`);
- ChatGPT — первоначальный план экзаменов и контроля;
- Codex — правка координат 11 эталонов (`labeling/yandex_tasks/AI_ASSISTANCE.md`);
- Claude — поиск ошибок при проверке качества (`golden/qa_check.md`).

Итоговые рамки golden set нарисованы и утверждены людьми.

## Лицензия

Все фотографии — CC0 из iNaturalist. Авторы и ссылки на наблюдения — в `data/sources_and_labels.jsonl`.
