"""Unit tests for discrepancy generator filtering (Phase P0: K1-051).

Verifies that generator receives 10 input lines including 5 log lines,
and exactly 0 log lines enter the output registry.
Rule K-1: Module must strictly be <= 400 lines.
"""

from __future__ import annotations

import pytest
from scripts.build_discrepancies import filter_requirements, is_requirement

LOG_LINES = [
    "INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.",
    '"jobs_in_queue": 0,',
    "{",
    "ПРОГНАНО:",
    "9 passed in 2.31s",
]

REQUIREMENT_LINES = [
    "п. 18: Создана полная структура директорий репозитория (src/nextlife, alembic, config, scripts, tests, systemd).",
    "п. 3: Реализовано управление пулом трёхсимвольных кодов 000–999 с возвратом в пул после ARCHIVED.",
    "Базовый контракт BaseConnector с валидацией обязательных селекторов и нормализацией цен.",
    "ingest_photo: приём оригинального фото без изменений, вычисление SHA-256 хэша и сохранение в БД.",
    "Фильтр внутренних полей: единая точка сериализации исходящего текста с защитой internal=true.",
]


def test_ten_lines_filter_rejects_all_five_logs() -> None:
    """Test with 10 input lines (5 logs, 5 requirements): exactly 0 logs enter registry."""
    input_lines = [
        LOG_LINES[0],
        REQUIREMENT_LINES[0],
        LOG_LINES[1],
        REQUIREMENT_LINES[1],
        LOG_LINES[2],
        REQUIREMENT_LINES[2],
        LOG_LINES[3],
        REQUIREMENT_LINES[3],
        LOG_LINES[4],
        REQUIREMENT_LINES[4],
    ]
    assert len(input_lines) == 10

    filtered = filter_requirements(input_lines)

    # Exactly 0 logs must enter
    for log_line in LOG_LINES:
        assert log_line not in filtered, f"Log line must not enter registry: {log_line}"

    # All 5 requirements must be preserved
    assert filtered == REQUIREMENT_LINES
    assert len(filtered) == 5


@pytest.mark.parametrize("log_line", LOG_LINES)
def test_each_log_line_rejected(log_line: str) -> None:
    """Individual assertion: is_requirement must be False for log line."""
    assert not is_requirement(log_line), f"Expected is_requirement=False for log line: {log_line}"


@pytest.mark.parametrize("req_line", REQUIREMENT_LINES)
def test_each_requirement_line_accepted(req_line: str) -> None:
    """Individual assertion: is_requirement must be True for requirement line."""
    assert is_requirement(req_line), f"Expected is_requirement=True for req line: {req_line}"
