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
SET_OPERATORS = {"UNION", "INTERSECT", "EXCEPT"}


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

    tokens = _unwrap_query(tokens)
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

    query_start = next((word for word in words if word != "("), "")
    parenthesized_query = first == "(" and query_start in {"SELECT", "WITH"}
    if (first not in ALLOWED_START and not parenthesized_query) or any(
        word in WRITE_WORDS for word in words
    ):
        raise SqlSafetyError("SQL_NOT_READ_ONLY", "only read-only SQL is allowed")
    if first == "WITH" and "SELECT" not in words:
        raise SqlSafetyError("SQL_NOT_READ_ONLY", "WITH must lead to a SELECT")

    top_words = [token.value for token in tokens if token.depth == 0]
    combined = any(word in SET_OPERATORS for word in top_words)
    hidden_cte_body = first == "WITH" and "SELECT" not in top_words
    if parenthesized_query or (
        first in {"SELECT", "WITH"}
        and (
            combined
            or hidden_cte_body
            or (_has_top_level_from(tokens) and not _is_top_level_aggregate(tokens))
        )
    ):
        _validate_limit(tokens, max_rows)


def _unwrap_query(tokens: list[Token]) -> list[Token]:
    """Remove only parentheses enclosing the entire statement, preserving scope."""
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for index, token in enumerate(tokens):
        if token.value == "(":
            stack.append(index)
        elif token.value == ")":
            pairs[stack.pop()] = index
    left, right = 0, len(tokens) - 1
    while left < right and pairs.get(left) == right:
        left += 1
        right -= 1
    if left > right:
        raise SqlSafetyError("SQL_INVALID", "empty query parentheses")
    return [Token(token.value, token.depth - left) for token in tokens[left : right + 1]]


def _validate_limit(tokens: list[Token], max_rows: int) -> None:
    positions = [
        index for index, token in enumerate(tokens) if token.depth == 0 and token.value == "LIMIT"
    ]
    last_operator = max(
        (i for i, token in enumerate(tokens) if token.depth == 0 and token.value in SET_OPERATORS),
        default=-1,
    )
    if not positions or positions[-1] < last_operator:
        raise SqlSafetyError("SQL_LIMIT_REQUIRED", "queries require an outer LIMIT on the whole result")
    if len(positions) != 1:
        raise SqlSafetyError("SQL_LIMIT_INVALID", "cannot confirm the scope of multiple outer LIMIT clauses")
    tail = tokens[positions[0] + 1 :]
    values = [token.value for token in tail]
    if len(values) == 1:
        numbers = values
        row_count = values[0]
    elif len(values) == 3 and values[1] in {",", "OFFSET"}:
        numbers = [values[0], values[2]]
        row_count = values[2] if values[1] == "," else values[0]
    else:
        raise SqlSafetyError("SQL_LIMIT_INVALID", "LIMIT must end the query with integer literals")
    if any(token.depth != 0 for token in tail) or any(
        not value.isascii() or not value.isdigit() for value in numbers
    ):
        raise SqlSafetyError("SQL_LIMIT_INVALID", "LIMIT and OFFSET must use integer literals")
    # Compare decimal strings so oversized input cannot hit Python's int conversion limit.
    count = row_count.lstrip("0") or "0"
    maximum = str(max_rows)
    if len(count) > len(maximum) or (len(count) == len(maximum) and count > maximum):
        raise SqlSafetyError("SQL_LIMIT_EXCEEDED", f"LIMIT must not exceed {max_rows}")


def _is_top_level_aggregate(tokens: list[Token]) -> bool:
    words = [token.value for token in tokens if token.depth == 0]
    if any(word in SET_OPERATORS | {"GROUP", "OVER"} for word in words):
        return False
    if "SELECT" not in words or "FROM" not in words:
        return False
    projection = words[words.index("SELECT") + 1 : words.index("FROM")]
    # At this depth, a function's arguments disappear but its parentheses remain.
    index = 0
    while index < len(projection):
        if projection[index] not in AGGREGATES or projection[index + 1 : index + 3] != ["(", ")"]:
            return False
        index += 3
        if index < len(projection) and projection[index] == "AS":
            index += 1
            if index == len(projection):
                return False
        if index < len(projection) and projection[index] != ",":
            alias = projection[index]
            if not alias.isidentifier():
                return False
            index += 1
        if index == len(projection):
            return True
        if projection[index] != ",":
            return False
        index += 1
    return False


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
