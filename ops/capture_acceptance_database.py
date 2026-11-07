"""Read-only timestamp/depth diagnostics, scoped to the timed acceptance interval."""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import datetime
from pathlib import Path

from ops.observe_acceptance import command, utc


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--project", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not args.project.startswith("crossx-acceptance-") or args.out.exists():
        parser.error("Require isolated project and new evidence path")
    contents = (
        gzip.decompress(args.evidence.read_bytes()).decode()
        if args.evidence.suffix == ".gz"
        else args.evidence.read_text()
    )
    samples = [json.loads(line) for line in contents.splitlines() if '"index"' in line]
    start = datetime.fromisoformat(samples[0]["timestamp"]).isoformat()
    end = datetime.fromisoformat(samples[-1]["timestamp"]).isoformat()
    # Literals are produced only from validated datetime objects, never arbitrary SQL input.
    sql = f"""SELECT json_agg(t) FROM (
      SELECT exchange,symbol,count(*) AS rows,
        avg(extract(epoch FROM received_at-observed_at)) AS event_to_receive_mean_seconds,
        max(extract(epoch FROM received_at-observed_at)) AS event_to_receive_max_seconds,
        percentile_disc(0.95) WITHIN GROUP
          (ORDER BY extract(epoch FROM received_at-observed_at)) AS event_to_receive_p95_seconds,
        count(*) FILTER (WHERE received_at-observed_at > interval '5 seconds')
          AS lag_over_5_seconds,
        count(*) FILTER (WHERE observed_at > received_at) AS event_ahead_of_receive,
        min(jsonb_array_length(payload->'bids')) AS min_bid_depth,
        max(jsonb_array_length(payload->'bids')) AS max_bid_depth,
        min(jsonb_array_length(payload->'asks')) AS min_ask_depth,
        max(jsonb_array_length(payload->'asks')) AS max_ask_depth
      FROM book_observations
      WHERE received_at >= '{start}'::timestamptz AND received_at <= '{end}'::timestamptz
      GROUP BY exchange,symbol ORDER BY exchange,symbol) t;"""
    result = json.loads(
        command(
            [
                "docker",
                "compose",
                "--project-name",
                args.project,
                "--file",
                "ops/compose.acceptance.yaml",
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "crossx",
                "-d",
                "crossx",
                "-At",
                "-c",
                sql,
            ]
        )
    )
    args.out.write_text(
        json.dumps({"timestamp": utc(), "sql": sql, "result": result}, indent=2) + "\n"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
