"""Explicit, read-only local PostgreSQL comparison; never connects on import.

First run with --dsn-env NAME --schema NAME for non-executing JSON plans only.
After human plan review, repeat with --analyze --reviewed-plan. The named env
variable must contain exactly host, port, dbname, user and password for loopback TCP.
Unix sockets require an absolute host directory, dbname and user; optional port
defaults explicitly to 5432, and optional password must be nonempty. No discovery.
Socket mode rejects ambient PGHOSTADDR/PGSERVICE/PGOPTIONS and, without an explicit
password, PGPASSWORD. Password files are disabled; password omission does not
guarantee authentication succeeds. Other libpq environment defaults still apply.
All comparisons and alternating runs share one read-only REPEATABLE READ snapshot.
surviving_match_occurrences sums match_count for all surviving candidates before
LIMIT/OFFSET, not prefilter intermediate rows. Intermediate execution evidence is
reported separately as plan node-level actual rows/loops, not a summary total.
"""

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from original_exact_query import exact_query as original_query  # noqa: E402

from search_strategies import exact_query  # noqa: E402

CASES = {
    "breaking-bad": ("tv", "tt0903747", 1396, 1, 1, 100, 0),
    "hanno": ("tv", "tt26545971", 273419, 1, 1, 100, 0),
}
GENERATORS = {"original": original_query, "optimized": exact_query}


def local_options(dsn):
    options = conninfo_to_dict(dsn)
    if options.get("host", "").startswith("/"):
        required = {"host", "dbname", "user"}
        if not required <= set(options) <= required | {"port", "password"} or not all(
            options.values()
        ):
            raise ValueError("Specify nonempty socket host, dbname, user; optional port/password")
        if (
            "," in options["host"]
            or "=" in options["dbname"]
            or options["dbname"].startswith(("postgres://", "postgresql://"))
        ):
            raise ValueError("Ambiguous socket destination")
        forbidden = {"PGHOSTADDR", "PGSERVICE", "PGOPTIONS"}
        if "password" not in options:
            forbidden.add("PGPASSWORD")
        if forbidden & os.environ.keys():
            raise ValueError("Ambient socket routing or credentials are not permitted")
        options.setdefault("port", "5432")
        if not options["port"].isdigit() or not 1 <= int(options["port"]) <= 65535:
            raise ValueError("Invalid explicit port")
        options.update(connect_timeout="3", passfile="/dev/null")
        return options
    required = {"host", "port", "dbname", "user", "password"}
    if set(options) != required or not all(options.values()):
        raise ValueError("Specify exactly nonempty host, port, dbname, user, password")
    if options["host"] not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Only explicit loopback hosts are permitted")
    if not options["port"].isdigit() or not 1 <= int(options["port"]) <= 65535:
        raise ValueError("Invalid explicit port")
    options["hostaddr"] = "::1" if options["host"] == "::1" else "127.0.0.1"
    options.update(connect_timeout="3", passfile="/dev/null")
    return options


def explain(cursor, query, values, analyze=False):
    options = (
        "ANALYZE, BUFFERS, VERBOSE, COSTS, SETTINGS" if analyze else "VERBOSE, COSTS, SETTINGS"
    )
    cursor.execute(f"EXPLAIN ({options}, FORMAT JSON) " + query, values)
    return cursor.fetchone()[0][0]


def plan_metrics(document):
    """Retain node-level loops, rows, buffers, aggregation and sort evidence."""
    nodes = []

    def visit(node):
        fields = (
            "Node Type",
            "Subplan Name",
            "Parent Relationship",
            "Actual Rows",
            "Actual Loops",
            "Rows Removed by Filter",
            "Strategy",
            "Sort Method",
            "Sort Space Used",
            "Sort Space Type",
            "Shared Hit Blocks",
            "Shared Read Blocks",
            "Temp Read Blocks",
            "Temp Written Blocks",
        )
        nodes.append({key: node[key] for key in fields if key in node})
        for child in node.get("Plans", []):
            visit(child)

    visit(document["Plan"])
    return {
        "planning_ms": document["Planning Time"],
        "execution_ms": document["Execution Time"],
        "nodes": nodes,
    }


def compare_rows(cursor, args, max_rows):
    results = {}
    for name, generator in GENERATORS.items():
        query, values, strategies = generator(*args)
        # Generated final LIMIT/OFFSET is removed only for complete-result proof.
        complete_query = query.rsplit("LIMIT %s OFFSET %s", 1)[0]
        cursor.execute("SELECT COUNT(*) FROM (" + complete_query + ") results", values[:-2])
        total = cursor.fetchone()[0]
        if total > max_rows:
            raise ValueError("Raw distinct total exceeds --max-rows; comparison aborted")
        cursor.execute(complete_query, values[:-2])
        complete = cursor.fetchall()
        cursor.execute(query, values)
        page = cursor.fetchall()
        results[name] = (complete, page, strategies, total)
    if results["original"] != results["optimized"]:
        raise ValueError("Original/optimized complete raw rows, page or strategies differ")
    complete, page, _, total = results["original"]
    return {
        "complete_raw_rows_equal": True,
        "raw_distinct_total": total,
        "returned_limit_100": len(page),
        "surviving_match_occurrences": sum(row[-1] for row in complete),
        "surviving_match_occurrences_scope": (
            "all surviving candidates before LIMIT/OFFSET; not prefilter intermediate rows"
        ),
    }


def run(options, args):
    with psycopg.connect(**options, autocommit=True) as connection:
        try:
            with connection.cursor() as cursor:
                cursor.execute("BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY")
                cursor.execute(
                    sql.SQL("SET LOCAL search_path TO {}, pg_catalog").format(
                        sql.Identifier(args.schema)
                    )
                )
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    (str(args.timeout_ms),),
                )
                cursor.execute("SELECT set_config('lock_timeout', '1000', true)")
                for case, request in CASES.items():
                    queries = {name: generator(*request) for name, generator in GENERATORS.items()}
                    for name, (query, values, _) in queries.items():
                        print(
                            json.dumps(
                                {
                                    "case": case,
                                    "variant": name,
                                    "plan": explain(cursor, query, values),
                                }
                            )
                        )
                    if not args.analyze:
                        continue
                    print(
                        json.dumps(
                            {
                                "case": case,
                                "comparison": compare_rows(cursor, request, args.max_rows),
                            }
                        )
                    )
                    timings = {name: [] for name in queries}
                    for iteration in range(args.warmups + args.runs):
                        order = tuple(queries) if iteration % 2 == 0 else tuple(reversed(queries))
                        for name in order:
                            query, values, _ = queries[name]
                            metrics = plan_metrics(explain(cursor, query, values, analyze=True))
                            warmup = iteration < args.warmups
                            if not warmup:
                                timings[name].append(metrics)
                            print(
                                json.dumps(
                                    {
                                        "case": case,
                                        "variant": name,
                                        "iteration": iteration,
                                        "warmup": warmup,
                                        "metrics": metrics,
                                    }
                                )
                            )
                    print(
                        json.dumps(
                            {
                                "case": case,
                                "medians": {
                                    name: {
                                        key: statistics.median(sample[key] for sample in samples)
                                        for key in ("planning_ms", "execution_ms")
                                    }
                                    for name, samples in timings.items()
                                },
                            }
                        )
                    )
        finally:
            connection.rollback()


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--dsn-env", required=True, help="Explicit authorized DSN variable name")
    result.add_argument("--schema", required=True, help="Explicit authorized dataset schema")
    result.add_argument("--analyze", action="store_true")
    result.add_argument("--reviewed-plan", action="store_true")
    result.add_argument("--runs", type=int, choices=range(3, 10), default=5)
    result.add_argument("--warmups", type=int, choices=range(1, 4), default=2)
    result.add_argument(
        "--timeout-ms", type=int, choices=range(1, 30001), default=10000, metavar="1..30000"
    )
    result.add_argument(
        "--max-rows", type=int, choices=range(1, 10001), default=1000, metavar="1..10000"
    )
    return result


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    if args.analyze and not args.reviewed_plan:
        arguments.error("--analyze requires --reviewed-plan after human review")
    dsn = os.environ.get(args.dsn_env)
    if not dsn:
        arguments.error("The explicit DSN variable is missing or empty")
    try:
        run(local_options(dsn), args)
    except (ValueError, psycopg.Error) as error:
        # Do not echo DSN, user, schema, private paths or server exception details.
        print(
            f"Benchmark aborted ({type(error).__name__}); transaction rolled back.", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
