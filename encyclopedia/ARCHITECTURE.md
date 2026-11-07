# Архитектура «Энциклопедия» — текстовые файлы → база знаний с темами/подтемами, полнотекстовым поиском и вложениями

## 1. Цели

1. Импорт произвольных текстовых файлов (в одном файле может быть несколько тем).
2. Автоматическое выделение **тем** и **подтем** (например: `Климатическое оборудование` → `Кондиционер Haier X`, `Вентилятор бытовой`).
3. Полнотекстовый поиск (FTS) по всем статьям, включая русскую морфологию (регистр падежей, основы слов).
4. Прикрепление к темам **архивов и медиа**: бекапы, программы, код драйверов, фото устройства, видео работы с ошибками и т.п.
5. Всё на Python + SQLite (FTS5) — не требует сервера БД, единый файл-хранилище, переносимый.

## 2. Выбор технологий и обоснование

| Компонент        | Решение                                   | Почему |
|------------------|-------------------------------------------|--------|
| Хранение        | **SQLite 3 (WAL)**                        | Zero-admin, транзакции, один файл = вся энциклопедия, бэкап = копирование файла |
| Поиск           | **FTS5 virtual table** + триггеры         | Нативная поддержка в SQLite; `simple_porter`-подобный токенизатор для RU/EN; ранжирование bm25; snippet() для подсветки |
| Морфология RU    | Собственный стеммер (удаление окончаний)  | Без внешних зависимостей; FTS5 tokenizer `unicode61` + porter. Для качества можно подключить pymorphy2 (опционально, см. §8) |
| Файлы-вложения   | Диск: `data/files/<sha256[0:2]>/<sha256>` | Контент-хеш → дедупликация (одинаковые бекапы хранятся один раз), SHA-256 → целостность |
| Классификатор    | YAML-таксономия + ключевые слова + частотный скоринг | Детерминированно, расширяемо пользователем; LLM/rasa-nlu — опциональный апгрейд |
| API              | FastAPI (+ CLI-скрипты)                   | Async, автодокументация /docs, easy file upload |
| Клиент          | Jinja2 SSR (минимальный UI) + REST        | Можно работать без браузера через CLI |

## 3. Схема данных (ER)

```
sources ──1:N── articles ──N:M (article_topics)── topics ──self parent_id── (иерархия тем)
                         │                              │
                         └────── N:M (topic_attachments via article? нет) ── attachments
attachments ── N:M topic_attachments ── topics
tags ── N:M article_tags ── articles
articles_fts  (FTS5, синхронизируется триггерами AI/AD/AU)
```

Таблицы (см. `app/db.py` — точный DDL):

* **topics(id, parent_id, name, slug UNIQUE, description, kind)** — дерево тем. `kind`: `category` (климатическое оборудование), `device_class` (кондиционер, вентилятор), `device_model` (Haier DC1052). Подтема — просто `parent_id`.
* **sources(id, path, sha256, title, imported_at, meta JSON)** — исходные txt-файлы (для переимпорта/трассировки).
* **articles(id, source_id, title, body, topic_hint, created_at, updated_at)** — фрагмент текста (статья/раздел), привязанный к источнику.
* **article_topics(article_id, topic_id, weight, is_primary)** — M:N: статья может относиться к нескольким темам; `weight` — уверенность классификатора.
* **attachments(id, filename, sha256 UNIQUE, size, mime, kind, storage_path, note, created_at)** — физический файл в хранилище. `kind`: `archive|backup|software|driver|photo|video|doc|other`.
* **topic_attachments(topic_id, attachment_id, role)** — привязка вложений к темам (`role`: manual, firmware, photo, error_video…).
* **tags(id, name), article_tags** — свободные метки («ошибки», «E5», «инвертор»).
* **articles_fts** — FTS5(title, body, tokenize='porter unicode61') + триггеры.

### Инварианты
* Один и тот же файл (по SHA-256) хранится на диске ровно один раз.
* FTS обновляется триггерами — рассинхрон невозможен; есть команда `reindex` на случай ручных правок.
* Удаление темы не каскадирует удаление статей; вложения удаляются сборщиком мусора (`scripts/gc.py`), если на них нет ссылок.

## 4. Конвейер импорта (ETL)

```
txt-файл → декодирование (utf-8/cp1251/bom) → разбиение на разделы
   (заголовки: "Глава", "Тема:", "===" , Markdown #, пустые строки+смена темы)
→ извлечение кандидатов тем (частотные n-граммы + словарь таксономии)
→ классификация раздела → topic(+подтема) c confidence
→ low confidence → статус "на модерацию" (CLI: review)
→ запись source/article/article_topics → триггеры обновляют FTS
```

Правила разбиения настраиваются в `data/taxonomy.yaml`:

```yaml
topics:
  - name: Климатическое оборудование
    slug: climate
    children:
      - name: Кондиционеры
        keywords: [кондиционер, сплит-система, инвертор, фреон, r32, хладагент]
        children:
          - { name: "Haier DC1052", model_re: 'haier\s*dc?1052' }
      - name: Вентиляторы
        keywords: [вентилятор, крыльчатка, двигатель вентилятора]
```

## 5. Поиск

Запросы (см. `app/search.py`):

* `q="кондиционер haier"` → `SELECT rowid, title, snippet(articles_fts,1,'<b>','</b>','…',12), bm25(...) FROM articles_fts WHERE articles_fts MATCH ?` с OR-развёрткой токенов (`кондиц* o haier`) для частичного совпадения.
* Фильтры: только в поддереве темы (`topic_id` → рекурсивный CTE по descendant_ids), по kind вложений, по тегам.
* Ранжирование: bm25 с весами (title ×5, body ×1); дополнительно буст за `is_primary` тему.
* Подсветка: `snippet()`; навигация: переход по ссылке «тема › подтема».

## 6. Вложения и медиа

* Загрузка: multipart (`POST /api/topics/{id}/attachments`) или CLI `add_attachment.py --topic climate/hvac/haier-dc1052 --file ./firmware.zip --kind backup`.
* Поток: compute sha256 (стриминг) → определить mime (magic by extension+`mimetypes`) → положить в content-addressed хранилище → запись в `attachments` → связь с темой.
* Скачивание: `GET /api/attachments/{id}/download` (FileResponse, Content-Disposition).
* Просмотр медиа: HTML-страница темы рендерит `<img>` для photo, `<video controls>` для video, блок «Скачать» для архивов; предпросмотр кода драйверов — `<pre>` до 64 КБ.
* Антивирус/безопасность: расширения-исполняемые (.exe/.sh) помечаются `kind=driver/software` и отдаются только как файлы, не инлайнятся (`Content-Security-Policy`, `X-Content-Type-Options: nosniff`).

## 7. Интерфейсы

* **CLI (основа MVP):** `import_txt`, `search`, `tree`, `attach`, `review`, `gc`, `reindex`.
* **REST API (FastAPI):** `/api/tree`, `/api/articles/{id}`, `/api/search?q=&topic=`, `/api/topics/{id}/attachments (POST)`, `/api/attachments/{id}/download`, `/api/files/{storage_path}` (serve media).
* **Web UI:** минимальный SSR (поиск + дерево + страница темы со списком статей и вложений) — чтобы можно было смотреть фото/видео прямо в браузере.

## 8. Эволюция архитектуры (when > 10k статей)

| Проблема | Решение |
|---|---|
| Качество русского поиска | Опциональный плагин: предобработка стеммингом Snowball/pymorphy2 перед индексацией (колонка `body_stemmed` в FTS) |
| Автоклассификация лучше словаря | Модуль `classifier_llm.py` (тот же интерфейс `classify(section)->[(topic,conf)]`), вызов локальной модели/OpenAI |
| Масштаб FTS | Миграция на PostgreSQL `tsvector` + `pg_trgm` (DDL почти 1:1), либо Meilisearch/Typesense поверх той же модели |
| Полный доступ к файлам | S3-совместимое хранилище вместо локального (интерфейс `Storage` уже изолирован) |
| Права доступа | Таблица `users` + роли на темы (SQLite справится) |

## 9. Структура проекта

```
encyclopedia/
├── ARCHITECTURE.md          ← этот документ
├── requirements.txt
├── app/
│   ├── config.py            пути, настройки
│   ├── db.py                схема SQLite + соединения + триггеры FTS
│   ├── models.py            dataclass-домены
│   ├── taxonomy.py          загрузка YAML-таксономии, матчинг тем
│   ├── chunker.py           разбиение txt на разделы, детект кодировки
│   ├── classifier.py        оценка соответствия раздела теме
│   ├── search.py            FTS5-запросы, ранжирование, фильтры по дереву
│   ├── storage.py           content-addressed хранилище файлов
│   └── api.py               FastAPI: поиск, дерево, загрузка вложений, media
├── scripts/
│   ├── init_db.py           создать БД + таксономию
│   ├── import_txt.py        импорт .txt/.md директории
│   ├── add_attachment.py    прикрепить файл к теме
│   ├── cli_search.py        поиск из консоли
│   ├── review.py            показать неотсортированные куски, назначить тему
│   └── gc.py                удалить осиротевшие файлы
├── data/
│   ├── encyclopedia.db      (создаётся)
│   ├── files/               (контент-хеш хранилище)
│   └── taxonomy.yaml        словарь тем/подтем/ключевиков
└── tests/test_smoke.py      сквозной тест: импорт → поиск → вложение
```

## 10. Примеры сценариев

```bash
python scripts/init_db.py
python scripts/import_txt.py ~/texts --taxonomy data/taxonomy.yaml
python scripts/add_attachment.py --topic climate/ac/haier-dc1052 \
       --file ./haier_err_E5.mp4 --kind video --note "видео ошибки E5"
python scripts/cli_search.py "кондиционер ошибка e5" --under climate
uvicorn app.api:app --port 8080     # веб-поиск + просмотр медиа
```
