# Executed command families. Replace project/ports/output paths for human reruns.
# Never run bare Compose lifecycle commands against the original stack.
export ACCEPTANCE_DB_PORT=17432 ACCEPTANCE_APP_PORT=18082
export ACCEPTANCE_PROM_PORT=19094 ACCEPTANCE_GRAFANA_PORT=13082
PROJECT=crossx-acceptance-phases27-20261108
# Initial isolated build; final accounting source rebuild repeated the app/storage targets.
docker compose -p "$PROJECT" -f ops/compose.acceptance.yaml up -d --build
# Only newly created test databases were used for fault tests.
docker compose -p "$PROJECT" -f ops/compose.acceptance.yaml exec -T postgres createdb -U crossx crossx_tests
CROSSX_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:17432/crossx_tests .venv/bin/alembic upgrade head
CROSSX_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:17432/crossx_tests .venv/bin/alembic check
.venv/bin/ruff check .
.venv/bin/mypy src
CROSSX_TEST_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:17432/crossx_tests \
CROSSX_FAULT_TEST_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:17432/crossx_tests \
CROSSX_TEST_GRAFANA_URL=http://127.0.0.1:13082 .venv/bin/pytest -q
.venv/bin/python scripts/check_connectivity.py --timeout 50 --duration 20
.venv/bin/python ops/benchmark_paper.py
# First window had a startup metrics error; retained, marked incomplete.
.venv/bin/python ops/observe_acceptance.py --project "$PROJECT" --duration 120 --interval 15 --app-port 18082 --prom-port 19094 --grafana-port 13082 --out /tmp/new-startup-observations.jsonl
# Wait for metrics/readiness; the warm window was separate and not concatenated.
.venv/bin/python ops/observe_acceptance.py --project "$PROJECT" --duration 60 --interval 10 --app-port 18082 --prom-port 19094 --grafana-port 13082 --out /tmp/new-warmed-observations.jsonl
.venv/bin/python ops/summarize_acceptance.py /tmp/new-warmed-observations.jsonl
# Optional local/free notifications: extra loopback Alertmanager port 19093.
docker compose -p "$PROJECT" -f ops/compose.acceptance.yaml -f ops/compose.alerts.yaml up -d --build alertmanager alert-sink prometheus
docker compose -p "$PROJECT" -f ops/compose.acceptance.yaml run --rm --no-deps --entrypoint /bin/promtool --workdir /etc/prometheus prometheus test rules alerts.test.yml
docker compose -p "$PROJECT" -f ops/compose.acceptance.yaml -f ops/compose.alerts.yaml exec -T prometheus promtool check config /etc/prometheus/prometheus.alerts.yml
# Replay each final-scenarios/{500,1000,1500}-{equal,selective}.json to a NEW output directory.
.venv/bin/python -m crossx.replay --config docs/evidence/phases2-7/final-scenarios/1000-equal.json --input docs/evidence/phases2-7/final-scenarios/books.jsonl --source-commit 0a28f827bf6e029e1f19724cd2fdb402e936c864 --out /tmp/new-synthetic-replay
CROSSX_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:17432/crossx .venv/bin/python -m crossx.replay --config config/paper.example.json --database --start 2026-11-08T09:50:00+00:00 --end 2026-11-08T09:51:00+00:00 --source-commit 0a28f827bf6e029e1f19724cd2fdb402e936c864 --out /tmp/new-public-replay
CROSSX_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:17432/crossx .venv/bin/python -m crossx.forward --config config/paper.example.json --duration 20 --commit-lag 12 --source-commit 0a28f827bf6e029e1f19724cd2fdb402e936c864 --out /tmp/new-forward-smoke
CROSSX_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:17432/crossx .venv/bin/python -m crossx.archive --start 2026-11-08T09:50:00+00:00 --end 2026-11-08T09:51:00+00:00 --out /tmp/new-crossx-export --dry-run
CROSSX_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:17432/crossx .venv/bin/python -m crossx.archive --start 2026-11-08T09:50:00+00:00 --end 2026-11-08T09:51:00+00:00 --out /tmp/new-crossx-export
.venv/bin/python -m crossx.archive --verify /tmp/new-crossx-export
trivy fs --scanners vuln --severity HIGH,CRITICAL --format json .
# Images were scanned separately; full reports identify actual image IDs/artifacts.
trivy image --scanners vuln --severity HIGH,CRITICAL crossx-acceptance-phases27-20261108-app
trivy image --scanners vuln --severity HIGH,CRITICAL crossx-acceptance-phases27-20261108-grafana
trivy image --scanners vuln --severity HIGH,CRITICAL crossx-acceptance-phases27-20261108-prometheus
trivy image --scanners vuln --severity HIGH,CRITICAL postgres:16-alpine
trivy image --scanners vuln --severity HIGH,CRITICAL prom/alertmanager:v0.34.2
# Bounded lifecycle check was performed after the timed window.
docker compose -p "$PROJECT" -f ops/compose.acceptance.yaml -f ops/compose.alerts.yaml stop app
docker compose -p "$PROJECT" -f ops/compose.acceptance.yaml -f ops/compose.alerts.yaml start app
# Final cleanup stops only disposable services; volumes and records remain.
docker compose -p "$PROJECT" -f ops/compose.acceptance.yaml -f ops/compose.alerts.yaml stop
