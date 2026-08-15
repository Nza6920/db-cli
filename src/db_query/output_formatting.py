from __future__ import annotations

import csv
import io
from typing import cast


def render_csv(columns: list[str], rows: list[dict[str, object]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_csv_value(row[column]) for column in columns])
    return output.getvalue()


def render_table(columns: list[str], rows: list[dict[str, object]]) -> str:
    from tabulate import tabulate  # type: ignore[import-untyped]

    values = [[_table_value(row[column]) for column in columns] for row in rows]
    return cast(
        str,
        tabulate(
            values,
            headers=columns,
            tablefmt="psql",
            disable_numparse=True,
        ),
    ) + "\n"


def _csv_value(value: object) -> object:
    return "\\N" if value is None else value


def _table_value(value: object) -> object:
    return "NULL" if value is None else value
