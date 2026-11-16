"""Extended PostgreSQL SQL acceptance through the CLI/driver boundary."""
import json
import unittest
import subprocess

import test_postgresql_cli as cli


class PostgreSqlExtendedCliTests(unittest.TestCase):
    def run_cli(self, *args: str, config: str = cli.CONFIG,
                stub: str | None = None) -> subprocess.CompletedProcess[str]:
        return cli.PostgreSqlCliTests().run_cli(*args, config=config, stub=stub)

    def test_read_only_cte_delivers_original_sql(self):
        sql = '/* original */ WITH c AS (SELECT * FROM t) SELECT * FROM c LIMIT 3; -- tail'
        result = self.run_cli('query', '--profile', 'local', '--sql', sql, stub=cli.query_stub(sql))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['row_count'], 1)

    def test_show_setting_and_nonexecuting_explain_preserve_sql_in_all_formats(self):
        for sql in ('SHOW TIME ZONE', 'SHOW ALL', 'SHOW transaction_isolation',
                    'EXPLAIN SELECT * FROM t',
                    'EXPLAIN (FORMAT JSON, COSTS OFF, VERBOSE) WITH c AS (SELECT * FROM t) SELECT * FROM c'):
            for output in ('json', 'csv', 'table'):
                with self.subTest(sql=sql, output=output):
                    result = self.run_cli('query', '--profile', 'local', '--sql', sql,
                                          '--format', output, stub=cli.query_stub(sql))
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_nested_ctes_use_outer_result_limit_and_aggregate_exemptions(self):
        accepted = [
            'WITH RECURSIVE c(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM c WHERE n<3) SELECT * FROM c LIMIT 3',
            'WITH c AS NOT MATERIALIZED (SELECT * FROM t), d AS MATERIALIZED (SELECT * FROM c) SELECT COUNT(*) AS total FROM d',
            'WITH "update" AS (WITH d AS (SELECT $$DELETE; FOR UPDATE$$ AS n) SELECT * FROM d) SELECT * FROM "update" LIMIT 2',
            'SELECT * FROM (WITH c AS (SELECT 1 AS n) SELECT * FROM c) s LIMIT 2',
            'WITH c AS (SELECT 1) (SELECT * FROM c UNION SELECT 2) LIMIT 3',
            'WITH c AS (SELECT 1) ((SELECT * FROM c LIMIT 3))',
            '(WITH c AS (SELECT 1) SELECT * FROM c LIMIT 3)',
        ]
        for sql in accepted:
            with self.subTest(sql=sql):
                result = self.run_cli('query', '--profile', 'local', '--sql', sql,
                                      stub=cli.query_stub(sql))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_unsafe_extended_sql_is_rejected_before_driver_access(self):
        rejected = [
            'WITH c AS (DELETE FROM t RETURNING *) SELECT * FROM c LIMIT 1',
            'WITH c AS NOT MATERIALIZED (UPDATE t SET n=1 RETURNING *) SELECT * FROM c LIMIT 1',
            'WITH c AS (WITH d AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM d) SELECT * FROM c LIMIT 1',
            'WITH c AS (SELECT 1) DELETE FROM t',
            'WITH c AS (SHOW timezone) SELECT * FROM c LIMIT 1',
            'WITH c AS (EXPLAIN SELECT 1) SELECT * FROM c LIMIT 1',
            'SELECT * FROM (WITH c AS (SELECT 1) TABLE c) q LIMIT 1',
            'WITH c AS (SELECT * FROM t LIMIT 1) SELECT * FROM c',
            'WITH c AS (SELECT 1) SELECT 1 UNION SELECT 2',
            'WITH c AS (SELECT 1) (SELECT * FROM c LIMIT 1) UNION (SELECT 2 LIMIT 1)',
            'WITH c AS (SELECT 1) SELECT * FROM c LIMIT 1001',
            'WITH c AS (SELECT 1) SELECT COUNT(*) OVER () FROM c',
            'EXPLAIN SELECT 1 INTO TEMP t', 'EXPLAIN UPDATE t SET n=1',
            'EXPLAIN WITH c AS (DELETE FROM t RETURNING *) SELECT * FROM c',
            'EXPLAIN SELECT * FROM t FOR SHARE',
            'EXPLAIN SELECT pg_catalog."set_config"(\'x\',\'y\',false)',
            'EXPLAIN ANALYZE SELECT 1', 'EXPLAIN ANALYSE SELECT 1',
            'EXPLAIN (ANALYZE) SELECT 1', 'EXPLAIN (ANALYZE ON) SELECT 1',
            'EXPLAIN (ANALYZE 1) SELECT 1', 'EXPLAIN (ANALYZE FALSE) SELECT 1',
            'EXPLAIN ("analyze" true) SELECT 1',
            "EXPLAIN (ANALYZE 'on') SELECT 1",
            'EXPLAIN (ANALYZE FALSE, ANALYZE TRUE) SELECT 1',
            'EXPLAIN (SERIALIZE TEXT) SELECT 1', 'EXPLAIN (UNKNOWN) SELECT 1',
            'EXPLAIN (FORMAT JSON, FORMAT TEXT) SELECT 1',
            'EXPLAIN (COSTS maybe) SELECT 1', 'EXPLAIN () SELECT 1',
            'SHOW timezone; SELECT 1', 'SHOW timezone SELECT 1',
        ]
        for sql in rejected:
            with self.subTest(sql=sql):
                result = self.run_cli('query', '--profile', 'local', '--sql', sql,
                                      stub="raise AssertionError('must reject before import')")
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertTrue(json.loads(result.stdout)['error']['code'].startswith('SQL_'))
