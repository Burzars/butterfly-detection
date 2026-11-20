# Посылка в Контест

`submission.jsonl` — предсказания итоговой конфигурации на `data/exam/dataset.jsonl`.

Формат — как у предсказаний (`predictions/README.md`), но для Контеста:

- в каждой строке `boxes` — список рамок `{"left", "top", "width", "height"}` без `score`; поле `error` — `null` или отсутствует;
- все 400 `photo_id` экзаменационного набора, каждый ровно один раз;
- файл JSONL до 5 МиБ; в Контесте выбрать компилятор `(make) python-handbook`.

Перед отправкой: `python scripts/check_submission.py submission/submission.jsonl data/exam/dataset.jsonl`
