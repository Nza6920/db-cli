"""PostgreSQL value representations through the CLI and database-driver seam."""
from __future__ import annotations

import csv
import io
import json
import unittest

import test_postgresql_cli as pg_cli


class PostgreSqlTypesCliTests(unittest.TestCase):
    def run_cli(self, *args: str, stub: str):
        return pg_cli.PostgreSqlCliTests().run_cli(*args, stub=stub)

    def query(self, values: str, columns: str, output: str = "json"):
        sql = "SELECT 1"
        return self.run_cli("query", "--profile", "local", "--sql", sql,
                            "--format", output, stub=pg_cli.query_stub(sql, values, columns))

    def test_native_postgresql_scalars_keep_boolean_offsets_and_uuid(self):
        values = """[(True, False,
            __import__('datetime').datetime(2026,1,2,3,4,5,tzinfo=__import__('datetime').timezone.utc),
            __import__('datetime').time(3,4,5,tzinfo=__import__('datetime').timezone(__import__('datetime').timedelta(hours=5, minutes=30))),
            __import__('uuid').UUID('12345678-1234-5678-1234-567812345678'))]"""
        result = self.query(values, "['yes','no','dt','t','id']")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["rows"], [{
            "yes": True, "no": False, "dt": "2026-01-02T03:04:05+00:00",
            "t": "03:04:05+05:30", "id": "12345678-1234-5678-1234-567812345678"}])

    def test_nested_json_arrays_and_shared_scalars_preserve_values(self):
        values = """[({'消息': [True, None, {'n': 1.5, 's': 'a,\\"b'}]},
            [[1, None], [2, 3]], [__import__('decimal').Decimal('12.30'),
            __import__('uuid').UUID('12345678-1234-5678-1234-567812345678')],
            None, None, __import__('decimal').Decimal('0.10'),
            memoryview(bytes([0,255])), __import__('datetime').date(2026,1,2),
            __import__('datetime').timedelta(hours=25, seconds=2), 42, 1.5)]"""
        result = self.query(values, "['obj','matrix','typed','sql_null','json_null','d','b','day','interval','n','f']")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["rows"], [{
            "obj": {"消息": [True, None, {"n": 1.5, "s": 'a,"b'}]},
            "matrix": [[1, None], [2, 3]],
            "typed": ["12.30", "12345678-1234-5678-1234-567812345678"],
            "sql_null": None, "json_null": None, "d": "0.10", "b": "0x00ff",
            "day": "2026-01-02", "interval": "25:00:02", "n": 42, "f": 1.5}])

    def test_csv_and_table_render_complex_values_as_compact_json(self):
        values = """[({'text': '消息,a"b', 'empty': None}, [True, None, [1, 2]], None)]"""
        for output in ("csv", "table"):
            with self.subTest(output=output):
                result = self.query(values, "['obj','items','empty']", output)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if output == "csv":
                    self.assertEqual(list(csv.reader(io.StringIO(result.stdout))), [
                        ['obj', 'items', 'empty'],
                        ['{"text":"消息,a\\"b","empty":null}', '[true,null,[1,2]]', r'\N']])
                else:
                    self.assertIn('{"text":"消息,a\\"b","empty":null}', result.stdout)
                    self.assertIn('[true,null,[1,2]]', result.stdout)
                    self.assertIn('NULL', result.stdout)

    def test_unrepresentable_nested_values_fail_without_partial_output(self):
        values = ("[({'nested': [object()]},)]", "[({'nested': {1: 'lost'}},)]",
                  "[([set([1])],)]", "[(__import__('uuid'),)]",
                  "[((lambda x: (x.append(x), x)[1])([]),)]",
                  "[(None,)]")
        for output in ("json", "csv", "table"):
            for rows in values:
                with self.subTest(output=output, rows=rows):
                    # The final case has a mismatched result shape.
                    columns = "['v','other']" if rows == "[(None,)]" else "['v']"
                    result = self.query(rows, columns, output)
                    self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
                    self.assertEqual(json.loads(result.stdout)["error"]["code"], "RESULT_ENCODING_FAILED")
                    self.assertEqual(result.stderr, "")

    def test_empty_results_and_duplicate_columns_keep_output_contract(self):
        for output in ("json", "csv", "table"):
            with self.subTest(output=output):
                result = self.query("[]", "['obj','items']", output)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if output == "json":
                    payload = json.loads(result.stdout)
                    self.assertEqual((payload['columns'], payload['rows'], payload['row_count']),
                                     (['obj','items'], [], 0))
                elif output == "csv":
                    self.assertEqual(result.stdout, "obj,items\n")
                else:
                    self.assertIn('obj', result.stdout)
                    self.assertIn('items', result.stdout)
                result = self.query("[]", "['v','v']", output)
                self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)["error"]["code"], "RESULT_ENCODING_FAILED")


if __name__ == "__main__":
    unittest.main()
