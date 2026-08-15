from __future__ import annotations

from dataclasses import dataclass


class SqlSafetyError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Token:
    value: str
    depth: int


ALLOWED_START = {"SELECT", "WITH", "SHOW", "DESC", "DESCRIBE", "EXPLAIN"}
WRITE_WORDS = {
    "INSERT",
    "UPDATE",
    "DELETE",
    "REPLACE",
    "CREATE",
    "ALTER",
    "DROP",
    "TRUNCATE",
    "RENAME",
    "GRANT",
    "REVOKE",
    "CALL",
    "DO",
    "HANDLER",
    "LOAD",
    "LOCK",
    "UNLOCK",
    "SET",
    "USE",
    "START",
    "COMMIT",
    "ROLLBACK",
    "SAVEPOINT",
    "RELEASE",
    "KILL",
    "OPTIMIZE",
    "REPAIR",
}
AGGREGATES = {"COUNT", "SUM", "AVG", "MIN", "MAX"}


def validate_read_only(sql: str, max_rows: int = 1000) -> None:
    if sql.lstrip().startswith("\\"):
        raise SqlSafetyError("SQL_META_COMMAND", "SQL client meta-commands are not allowed")
    tokens = _tokens(sql)
    if not tokens:
        raise SqlSafetyError("SQL_EMPTY", "SQL is empty")

    semicolons = [index for index, token in enumerate(tokens) if token.value == ";"]
    if semicolons:
        if len(semicolons) != 1 or semicolons[0] != len(tokens) - 1:
            raise SqlSafetyError("SQL_MULTIPLE_STATEMENTS", "exactly one SQL statement is allowed")
        tokens = tokens[:-1]
    if not tokens:
        raise SqlSafetyError("SQL_EMPTY", "SQL is empty")

    words = [token.value for token in tokens]
    first = words[0]
    for sequence in (
        ("INTO",),
        ("INTO", "OUTFILE"),
        ("INTO", "DUMPFILE"),
        ("FOR", "UPDATE"),
        ("FOR", "SHARE"),
        ("LOCK", "IN", "SHARE", "MODE"),
    ):
        if _contains_sequence(words, sequence):
            raise SqlSafetyError("SQL_DANGEROUS_CLAUSE", "dangerous read clause is not allowed")
    if any(word in {"GET_LOCK", "RELEASE_LOCK"} for word in words):
        raise SqlSafetyError("SQL_DANGEROUS_CLAUSE", "advisory lock functions are not allowed")

    if first not in ALLOWED_START or any(word in WRITE_WORDS for word in words):
        raise SqlSafetyError("SQL_NOT_READ_ONLY", "only read-only SQL is allowed")
    if first == "WITH" and "SELECT" not in words:
        raise SqlSafetyError("SQL_NOT_READ_ONLY", "WITH must lead to a SELECT")

    if (
        first in {"SELECT", "WITH"}
        and _has_top_level_from(tokens)
        and not _is_top_level_aggregate(tokens)
    ):
        _validate_limit(tokens, max_rows)


def _validate_limit(tokens: list[Token], max_rows: int) -> None:
    positions = [
        index for index, token in enumerate(tokens) if token.depth == 0 and token.value == "LIMIT"
    ]
    if not positions:
        raise SqlSafetyError("SQL_LIMIT_REQUIRED", "detail queries require an outer LIMIT")
    index = positions[-1]
    if index + 1 >= len(tokens) or not tokens[index + 1].value.isdigit():
        raise SqlSafetyError("SQL_LIMIT_INVALID", "LIMIT must use an integer literal")
    row_count = int(tokens[index + 1].value)
    if index + 2 < len(tokens) and tokens[index + 2].value == ",":
        if index + 3 >= len(tokens) or not tokens[index + 3].value.isdigit():
            raise SqlSafetyError("SQL_LIMIT_INVALID", "LIMIT must use integer literals")
        row_count = int(tokens[index + 3].value)
    elif index + 2 < len(tokens) and tokens[index + 2].value == "OFFSET":
        if index + 3 >= len(tokens) or not tokens[index + 3].value.isdigit():
            raise SqlSafetyError("SQL_LIMIT_INVALID", "OFFSET must use an integer literal")
    if row_count > max_rows:
        raise SqlSafetyError("SQL_LIMIT_EXCEEDED", f"LIMIT must not exceed {max_rows}")


def _is_top_level_aggregate(tokens: list[Token]) -> bool:
    return any(token.depth == 0 and token.value in AGGREGATES for token in tokens)


def _has_top_level_from(tokens: list[Token]) -> bool:
    return any(token.depth == 0 and token.value == "FROM" for token in tokens)


def _contains_sequence(words: list[str], sequence: tuple[str, ...]) -> bool:
    width = len(sequence)
    return any(tuple(words[index : index + width]) == sequence for index in range(len(words) - width + 1))


def _tokens(sql: str) -> list[Token]:
    result: list[Token] = []
    index = 0
    depth = 0
    length = len(sql)
    while index < length:
        char = sql[index]
        if char.isspace():
            index += 1
            continue
        if char == "#":
            index = _skip_line(sql, index + 1)
            continue
        if sql.startswith("--", index):
            index = _skip_line(sql, index + 2)
            continue
        if sql.startswith("/*!", index):
            raise SqlSafetyError("SQL_DANGEROUS_CLAUSE", "executable comments are not allowed")
        if sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            if end < 0:
                raise SqlSafetyError("SQL_INVALID", "unterminated block comment")
            index = end + 2
            continue
        if char in {"'", '"', "`"}:
            index = _skip_quoted(sql, index, char)
            result.append(Token("QUOTED", depth))
            continue
        if char == "(":
            result.append(Token(char, depth))
            depth += 1
            index += 1
            continue
        if char == ")":
            depth -= 1
            if depth < 0:
                raise SqlSafetyError("SQL_INVALID", "unbalanced parentheses")
            result.append(Token(char, depth))
            index += 1
            continue
        if char.isalpha() or char == "_":
            end = index + 1
            while end < length and (sql[end].isalnum() or sql[end] in {"_", "$"}):
                end += 1
            result.append(Token(sql[index:end].upper(), depth))
            index = end
            continue
        if char.isdigit():
            end = index + 1
            while end < length and sql[end].isdigit():
                end += 1
            result.append(Token(sql[index:end], depth))
            index = end
            continue
        result.append(Token(char, depth))
        index += 1
    if depth != 0:
        raise SqlSafetyError("SQL_INVALID", "unbalanced parentheses")
    return result


def _skip_line(sql: str, index: int) -> int:
    newline = sql.find("\n", index)
    return len(sql) if newline < 0 else newline + 1


def _skip_quoted(sql: str, index: int, quote: str) -> int:
    cursor = index + 1
    while cursor < len(sql):
        if sql[cursor] == "\\":
            cursor += 2
            continue
        if sql[cursor] == quote:
            if cursor + 1 < len(sql) and sql[cursor + 1] == quote:
                cursor += 2
                continue
            return cursor + 1
        cursor += 1
    raise SqlSafetyError("SQL_INVALID", "unterminated quoted value")
