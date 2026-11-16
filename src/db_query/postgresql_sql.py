"""PostgreSQL lexical and read-only policy; accepted SQL is never rewritten."""
from __future__ import annotations

from dataclasses import dataclass, replace
import re

from db_query.sql_safety import SqlSafetyError, AGGREGATES, SET_OPERATORS, WRITE_WORDS


@dataclass(frozen=True)
class PgToken:
    value: str
    depth: int
    kind: str = "word"

    def keyword(self, value: str) -> bool:
        return self.kind == "word" and self.value == value


# Explicit known side effects. Arbitrary UDF effects remain a privilege boundary.
DANGEROUS_FUNCTIONS = {
    "SET_CONFIG", "NEXTVAL", "SETVAL", "PG_CANCEL_BACKEND", "PG_TERMINATE_BACKEND",
    "PG_RELOAD_CONF", "PG_ROTATE_LOGFILE", "PG_NOTIFY", "PG_LOGICAL_EMIT_MESSAGE",
    "LO_IMPORT", "LO_EXPORT", "LO_CREATE", "LO_UNLINK", "LOWRITE", "LO_PUT", "LO_TRUNCATE", "LO_TRUNCATE64",
    "PG_LOGICAL_SLOT_GET_CHANGES", "PG_LOGICAL_SLOT_GET_BINARY_CHANGES",
    "PG_CREATE_RESTORE_POINT", "PG_SWITCH_WAL", "PG_PROMOTE", "PG_BACKUP_START",
    "PG_BACKUP_STOP", "PG_WAL_REPLAY_PAUSE", "PG_WAL_REPLAY_RESUME",
}
DANGEROUS_PREFIXES = ("PG_ADVISORY_", "PG_TRY_ADVISORY_", "PG_CREATE_", "PG_DROP_",
                      "PG_REPLICATION_ORIGIN_", "PG_COPY_", "PG_FILE_")
FORBIDDEN_WORDS = WRITE_WORDS | {"COPY", "MERGE", "VACUUM", "REFRESH", "PREPARE", "EXECUTE",
                                  "INTO", "FOR", "LISTEN", "NOTIFY", "UNLISTEN"}


def invalid(message: str) -> SqlSafetyError:
    return SqlSafetyError("SQL_INVALID", message)


def tokenize(sql: str) -> list[PgToken]:
    result: list[PgToken] = []
    i, depth = 0, 0
    previous_escape = False
    while i < len(sql):
        char = sql[i]
        if char.isspace():
            i += 1
            continue
        if sql.startswith("--", i):
            end = sql.find("\n", i + 2)
            i = len(sql) if end < 0 else end + 1
            continue
        if sql.startswith("/*", i):
            nesting = 1
            i += 2
            while i < len(sql) and nesting:
                if sql.startswith("/*", i):
                    nesting += 1
                    i += 2
                elif sql.startswith("*/", i):
                    nesting -= 1
                    i += 2
                else:
                    i += 1
            if nesting:
                raise invalid("unterminated block comment")
            continue
        if char == "\\":
            raise SqlSafetyError("SQL_META_COMMAND", "SQL client meta-commands are not allowed")
        if char == "`":
            raise invalid("PostgreSQL does not use backtick identifiers")
        # Fail closed for Unicode identifiers until their decoding is implemented.
        if sql[i:i + 3].upper() == 'U&"':
            raise invalid("Unicode escaped identifiers are not supported")
        prefix = re.match(r"(?i:E|N|B|X|U&)(?=')", sql[i:])
        escape = bool(prefix and prefix.group().upper() == "E")
        if prefix:
            i += len(prefix.group())
            char = sql[i]
        if char in {"'", '"'}:
            quote = char
            if quote == "'" and previous_escape:
                raise invalid("escape string continuation is not supported")
            previous_escape = escape
            i += 1
            content = ""
            while i < len(sql):
                if sql[i] == quote:
                    if i + 1 < len(sql) and sql[i + 1] == quote:
                        content += quote
                        i += 2
                        continue
                    i += 1
                    break
                if escape and sql[i] == "\\":
                    i += 2
                    continue
                content += sql[i]
                i += 1
            else:
                raise invalid("unterminated quoted value")
            # PostgreSQL concatenates newline-separated literals. Reject ambiguous
            # E-string continuation rather than parse it with different escaping.
            if escape and re.match(r"\s*\n\s*'", sql[i:]):
                raise invalid("escape string continuation is not supported")
            result.append(PgToken(content if quote == '"' else "LITERAL", depth,
                                  "identifier" if quote == '"' else "literal"))
            continue
        previous_escape = False
        if char == "$":
            match = re.match(r"\$(?:[^\W\d]\w*)?\$", sql[i:])
            if match:
                delimiter = match.group()
                end = sql.find(delimiter, i + len(delimiter))
                if end < 0:
                    raise invalid("unterminated dollar quoted string")
                result.append(PgToken("LITERAL", depth, "literal"))
                i = end + len(delimiter)
                continue
        if char == "(":
            result.append(PgToken(char, depth, "punctuation"))
            depth += 1
            i += 1
            continue
        if char == ")":
            depth -= 1
            if depth < 0:
                raise invalid("unbalanced parentheses")
            result.append(PgToken(char, depth, "punctuation"))
            i += 1
            continue
        if char.isalpha() or char == "_":
            end = i + 1
            while end < len(sql) and (sql[end].isalnum() or sql[end] in "_$"):
                end += 1
            result.append(PgToken(sql[i:end].upper(), depth))
            i = end
            continue
        if char.isdigit():
            end = i + 1
            while end < len(sql) and sql[end].isdigit():
                end += 1
            result.append(PgToken(sql[i:end], depth, "integer"))
            i = end
            continue
        result.append(PgToken(char, depth, "punctuation"))
        i += 1
    if depth:
        raise invalid("unbalanced parentheses")
    return result


def unwrap_query(tokens: list[PgToken]) -> list[PgToken]:
    while len(tokens) >= 2 and tokens[0].value == "(" and tokens[-1].value == ")":
        if any(token.depth == tokens[0].depth for token in tokens[1:-1]):
            break
        tokens = [replace(token, depth=token.depth - 1) for token in tokens[1:-1]]
    return tokens


def validate_postgresql(sql: str, max_rows: int = 1000) -> None:
    tokens = tokenize(sql)
    semicolons = [i for i, token in enumerate(tokens) if token.value == ";" and token.kind == "punctuation"]
    if semicolons:
        if semicolons != [len(tokens) - 1]:
            raise SqlSafetyError("SQL_MULTIPLE_STATEMENTS", "exactly one SQL statement is allowed")
        tokens = tokens[:-1]
    if not tokens:
        raise SqlSafetyError("SQL_EMPTY", "SQL is empty")
    validate_select(tokens, max_rows)


def validate_select(tokens: list[PgToken], max_rows: int, require_limit: bool = True) -> None:
    tokens = unwrap_query(tokens)
    validate_safe_tokens(tokens)
    start = next((token for token in tokens if token.value != "("), None)
    if start is None or not start.keyword("SELECT") or any(
        token.kind == "word" and token.value in {"WITH", "SHOW", "EXPLAIN"} for token in tokens
    ):
        raise SqlSafetyError("SQL_NOT_READ_ONLY", "only PostgreSQL SELECT is supported")
    top = [t for t in tokens if t.depth == 0]
    combined = any(t.kind == "word" and t.value in SET_OPERATORS for t in top)
    needs_limit = (tokens[0].value == "(" or combined or
                   (any(t.keyword("FROM") for t in top) and not is_aggregate(top)))
    if require_limit and needs_limit:
        validate_limit(tokens, max_rows)


def validate_safe_tokens(tokens: list[PgToken]) -> None:
    for i, token in enumerate(tokens):
        if token.kind == "word" and token.value in FORBIDDEN_WORDS:
            raise SqlSafetyError("SQL_NOT_READ_ONLY", "only read-only SQL is allowed")
        if token.kind in {"word", "identifier"} and i + 1 < len(tokens) and tokens[i + 1].value == "(":
            name = token.value.upper()
            if name in DANGEROUS_FUNCTIONS or name.startswith(DANGEROUS_PREFIXES):
                raise SqlSafetyError("SQL_DANGEROUS_CLAUSE", "dangerous function calls are not allowed")


def is_aggregate(top: list[PgToken]) -> bool:
    if any(t.kind == "word" and t.value in SET_OPERATORS | {"GROUP", "OVER"} for t in top):
        return False
    first_from = next((i for i, t in enumerate(top) if t.keyword("FROM")), -1)
    if first_from < 0 or not top[0].keyword("SELECT"):
        return False
    projection = top[1:first_from]
    i = 0
    while i < len(projection):
        if not (projection[i].kind == "word" and projection[i].value in AGGREGATES
                and [t.value for t in projection[i + 1:i + 3]] == ["(", ")"]):
            return False
        i += 3
        if i < len(projection) and projection[i].keyword("AS"):
            i += 1
            if i == len(projection):
                return False
        if i < len(projection) and projection[i].kind in {"word", "identifier"}:
            i += 1
        if i == len(projection):
            return True
        if projection[i].value != ",":
            return False
        i += 1
    return False


def validate_limit(tokens: list[PgToken], max_rows: int) -> None:
    positions = [i for i, t in enumerate(tokens) if t.depth == 0 and t.keyword("LIMIT")]
    last_operator = max((i for i, t in enumerate(tokens) if t.depth == 0 and t.kind == "word" and t.value in SET_OPERATORS), default=-1)
    if not positions or positions[-1] < last_operator:
        raise SqlSafetyError("SQL_LIMIT_REQUIRED", "queries require an outer LIMIT on the whole result")
    if len(positions) != 1:
        raise SqlSafetyError("SQL_LIMIT_INVALID", "multiple outer LIMIT clauses")
    tail = tokens[positions[0] + 1:]
    if len(tail) == 3 and tail[1].keyword("OFFSET"):
        numbers = [tail[0], tail[2]]
    elif len(tail) == 1:
        numbers = tail
    else:
        raise SqlSafetyError("SQL_LIMIT_INVALID", "LIMIT must end the query with integer literals")
    if any(t.depth != 0 or t.kind != "integer" or not t.value.isascii() for t in numbers):
        raise SqlSafetyError("SQL_LIMIT_INVALID", "LIMIT and OFFSET must use integer literals")
    count = numbers[0].value.lstrip("0") or "0"
    maximum = str(max_rows)
    if len(count) > len(maximum) or (len(count) == len(maximum) and count > maximum):
        raise SqlSafetyError("SQL_LIMIT_EXCEEDED", f"LIMIT must not exceed {max_rows}")
