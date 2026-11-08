# Executed/readiness reproduction commands. Run from repository root.
# Provision only a NEW dedicated project; never target the original stack.
export ACCEPTANCE_DB_PORT=18432 ACCEPTANCE_APP_PORT=18083 ACCEPTANCE_PROM_PORT=19096 ACCEPTANCE_GRAFANA_PORT=13083
export CROSSX_CAPACITY_RUN_SECONDS=900 CROSSX_BOOK_SAMPLE_INTERVAL_SECONDS=0
# Before up: inspect docker ps, free ports, df, docker system df; choose unused names/ports.
docker compose -p crossx-acceptance-readiness-20261108 -f ops/compose.acceptance.yaml config --quiet
docker compose -p crossx-acceptance-readiness-20261108 -f ops/compose.acceptance.yaml build
docker compose -p crossx-acceptance-readiness-20261108 -f ops/compose.acceptance.yaml up -d
.venv/bin/python ops/observe_acceptance.py --project crossx-acceptance-readiness-20261108 --app-port 18083 --prom-port 19096 --grafana-port 13083 --duration 900 --interval 15 --out NEW-observations.jsonl
.venv/bin/python ops/summarize_acceptance.py NEW-observations.jsonl
.venv/bin/python -m ops.capture_acceptance_database NEW-observations.jsonl --project crossx-acceptance-readiness-20261108 --out NEW-database.json
.venv/bin/python ops/capture_acceptance_prometheus.py NEW-observations.jsonl --port 19096 --out NEW-prometheus.json
# Separate fault project: choose fresh name/volume, wait healthy, migrate BEFORE running tests.
ACCEPTANCE_DB_PORT=18433 docker compose -p crossx-acceptance-readiness-faults-20261108 -f ops/compose.acceptance.yaml up -d postgres
export CROSSX_DATABASE_URL=postgresql+asyncpg://crossx:crossx@127.0.0.1:18433/crossx
.venv/bin/alembic upgrade head
.venv/bin/alembic check
export CROSSX_TEST_DATABASE_URL="$CROSSX_DATABASE_URL" CROSSX_FAULT_TEST_DATABASE_URL="$CROSSX_DATABASE_URL" CROSSX_TEST_GRAFANA_URL=http://127.0.0.1:13083
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src/crossx
# Inspect --help before invoking bounded helper/fault harness; it only uses disposable DB.
.venv/bin/python ops/check_forward_recovery.py --help
.venv/bin/python ops/run_readiness_paper.py --help
# Promtool was executed inside the built image; this performs no feed operations.
docker compose -p crossx-acceptance-readiness-20261108 -f ops/compose.acceptance.yaml run --rm --no-deps --entrypoint /bin/promtool --workdir /etc/prometheus prometheus test rules alerts.test.yml
# Capacity example is read-only; NOT authorization to begin fourteen days.
docker compose -p crossx-acceptance-readiness-20261108 -f ops/compose.acceptance.yaml exec -T app python -m crossx.capacity --path /pgdata --days 14 --daily-gb 23.22 --headroom-gib 5
# Bounded paper scenario: repeat with500/1000/1500 configs and NEW output paths.
export CROSSX_DATABASE_URL=postgresql+asyncpg://crossx:crossx@127.0.0.1:18432/crossx
.venv/bin/python -m crossx.forward --config config/readiness-gbp-500.json --out NEW-paper-500 --source-commit f26bdc083cc331c5ef586fbac259ce6a598d613d --duration 60
# Resume exact source/config only; substitute real recorded session id, new attempt output.
# .venv/bin/python -m crossx.forward --config config/readiness-gbp-500.json --out NEW-resume --source-commit f26bdc083cc331c5ef586fbac259ce6a598d613d --duration 60 --resume SESSION_ID
# .venv/bin/python -m crossx.recovery --inspect SESSION_ID
# .venv/bin/python -m crossx.window_archive --session SESSION_ID --version 1 --out NEW-export --dry-run
# .venv/bin/python -m crossx.window_archive --session SESSION_ID --version 1 --out NEW-export
# .venv/bin/python -m crossx.window_archive --verify NEW-export
# Finish only the named disposable projects; retain all volumes/evidence.
docker compose -p crossx-acceptance-readiness-20261108 -f ops/compose.acceptance.yaml stop
docker compose -p crossx-acceptance-readiness-faults-20261108 -f ops/compose.acceptance.yaml stop
# Image scanning: trivy image --severity HIGH,CRITICAL --format json --output NEW-scan.json IMAGE_ID
# Exact final image ids and per-finding triage are in runtime/security JSON evidence.
