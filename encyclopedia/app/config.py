"""Глобальные настройки приложения «Энциклопедия»."""
from __future__ import annotations

import os
from pathlib import Path

# Корень проекта: encyclopedia/
BASE_DIR = Path(os.environ.get("ENCY_BASE", Path(__file__).resolve().parents[1]))

DATA_DIR = BASE_DIR / "data"
FILES_DIR = DATA_DIR / "files"            # content-addressed хранилище вложений
DB_PATH = Path(os.environ.get("ENCY_DB", DATA_DIR / "encyclopedia.db"))
TAXONOMY_PATH = DATA_DIR / "taxonomy.yaml"

# Минимальная уверенность классификатора, чтобы автоматически привязать статью к теме.
# Ниже — статья попадает в очередь модерации (review).
AUTO_ASSIGN_THRESHOLD = float(os.environ.get("ENCY_THRESHOLD", "0.35"))

# Максимальный размер инлайнового предпросмотра текстовых вложений (драйверы/код)
PREVIEW_MAX_BYTES = 64 * 1024

KINDS = ("archive", "backup", "software", "driver", "photo", "video", "doc", "other")

TOPIC_KINDS = ("category", "device_class", "device_model")
