# Exact reproduction from repository root; choose fresh names/ports/output paths.
export ACCEPTANCE_DB_PORT=18532 ACCEPTANCE_APP_PORT=18183 ACCEPTANCE_PROM_PORT=19196 ACCEPTANCE_GRAFANA_PORT=13183
export COMPOSE_PROJECT_NAME=crossx-acceptance-integration-20261108
# Verify unused names/ports first; original project is never a target.
docker compose -p "$COMPOSE_PROJECT_NAME" -f ops/compose.acceptance.yaml config --quiet
docker compose -p "$COMPOSE_PROJECT_NAME" -f ops/compose.acceptance.yaml build
docker compose -p "$COMPOSE_PROJECT_NAME" -f ops/compose.acceptance.yaml up -d
.venv/bin/python -m ops.check_storage_lifecycle --project "$COMPOSE_PROJECT_NAME" --database-port 18532 --out NEW-lifecycle-evidence
# Harness stops all project services and never removes volumes/records.
# Start only PostgreSQL/Grafana for a NEW, separately named regression database.
docker compose -p "$COMPOSE_PROJECT_NAME" -f ops/compose.acceptance.yaml start postgres grafana
docker compose -p "$COMPOSE_PROJECT_NAME" -f ops/compose.acceptance.yaml exec -T postgres psql -U crossx -c 'CREATE DATABASE gate_tests'
export CROSSX_DATABASE_URL=postgresql+asyncpg://crossx:crossx@127.0.0.1:18532/gate_tests
export CROSSX_TEST_DATABASE_URL="$CROSSX_DATABASE_URL" CROSSX_FAULT_TEST_DATABASE_URL="$CROSSX_DATABASE_URL" CROSSX_TEST_GRAFANA_URL=http://127.0.0.1:13183
.venv/bin/alembic upgrade head
.venv/bin/alembic check
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/mypy src/crossx
docker compose -p "$COMPOSE_PROJECT_NAME" -f ops/compose.acceptance.yaml run --rm --no-deps --entrypoint /bin/promtool --workdir /etc/prometheus prometheus test rules alerts.test.yml
# Stop only the disposable services; do not use down -v or prune.
docker compose -p "$COMPOSE_PROJECT_NAME" -f ops/compose.acceptance.yaml stop
# Real sidecar-image scan, without suppression:
trivy image --severity HIGH,CRITICAL --format json --output NEW-trivy.json sha256:458c29bd837b3e7e8fd7df9675511174c61906a4c4d753d94daaeaadb84c49e3
