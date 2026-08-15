from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Sequence

from db_query.errors import RunnerError


def normalize_rows(
    columns: list[str], raw_rows: Sequence[Sequence[object]]
) -> list[dict[str, object]]:
    if len(columns) != len(set(columns)):
        raise RunnerError(
            "RESULT_ENCODING_FAILED",
            "duplicate result column names are not supported; provide unique SQL aliases",
            5,
        )
    rows: list[dict[str, object]] = []
    for raw_row in raw_rows:
        if len(raw_row) != len(columns):
            raise RunnerError(
                "RESULT_ENCODING_FAILED",
                "database result shape does not match its columns",
                5,
            )
        rows.append(
            {
                column: normalize_value(value)
                for column, value in zip(columns, raw_row, strict=True)
            }
        )
    return rows


def normalize_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float)):
        return int(value) if isinstance(value, bool) else value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime.datetime):
        if value.tzinfo is not None:
            raise RunnerError(
                "RESULT_ENCODING_FAILED",
                "timezone-aware datetime values are not supported",
                5,
            )
        return value.isoformat()
    if isinstance(value, datetime.date):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return _format_timedelta(value)
    if isinstance(value, datetime.time):
        if value.tzinfo is not None:
            raise RunnerError(
                "RESULT_ENCODING_FAILED",
                "timezone-aware time values are not supported",
                5,
            )
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"0x{bytes(value).hex()}"
    raise RunnerError(
        "RESULT_ENCODING_FAILED",
        f"unsupported database result type: {type(value).__name__}",
        5,
    )


def _format_timedelta(value: datetime.timedelta) -> str:
    total_microseconds = (
        (value.days * 86_400 + value.seconds) * 1_000_000 + value.microseconds
    )
    sign = "-" if total_microseconds < 0 else ""
    remaining = abs(total_microseconds)
    hours, remaining = divmod(remaining, 3_600_000_000)
    minutes, remaining = divmod(remaining, 60_000_000)
    seconds, microseconds = divmod(remaining, 1_000_000)
    result = f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{result}.{microseconds:06d}" if microseconds else result
