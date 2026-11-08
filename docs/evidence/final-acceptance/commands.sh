# Executed commands for this run; do not blindly reuse its project or database.
source docs/evidence/final-acceptance/environment.sh
# Initial inspection (before service creation):
git status --short
git rev-parse HEAD
docker ps -a
docker compose ls
git switch -c codex/phase-1-final-acceptance e7b585320bd80be1ba476c1ce9ff9fb299191291
PYTHONPATH=. .venv/bin/python /tmp/crossx-final-prepare.py
docker compose -p "$FINAL_PROJECT" -f ops/compose.acceptance.yaml config
docker compose -p "$FINAL_PROJECT" -f ops/compose.acceptance.yaml up -d --build
PYTHONPATH=. .venv/bin/python /tmp/crossx-final-start.py
# Independent checks during observation; distinct integration database:
docker compose -p "$FINAL_PROJECT" -f ops/compose.acceptance.yaml exec -T postgres createdb -U crossx crossx_final_unit
.venv/bin/ruff check .
.venv/bin/mypy src
CROSSX_TEST_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:17432/crossx_final_unit CROSSX_FAULT_TEST_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:17432/crossx_final_unit CROSSX_TEST_GRAFANA_URL=http://127.0.0.1:13082 .venv/bin/pytest -q
# First run failed (4 missing-schema failures); migrate the new test database:
CROSSX_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:17432/crossx_final_unit .venv/bin/alembic upgrade head
CROSSX_TEST_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:17432/crossx_final_unit CROSSX_FAULT_TEST_DATABASE_URL=postgresql+asyncpg://crossx:crossx@localhost:17432/crossx_final_unit CROSSX_TEST_GRAFANA_URL=http://127.0.0.1:13082 .venv/bin/pytest -q
docker compose config --quiet
docker compose -p "$FINAL_PROJECT" -f ops/compose.acceptance.yaml run --rm --no-deps --entrypoint /bin/promtool --workdir /etc/prometheus prometheus check config /etc/prometheus/prometheus.yml
docker compose -p "$FINAL_PROJECT" -f ops/compose.acceptance.yaml run --rm --no-deps --entrypoint /bin/promtool --workdir /etc/prometheus prometheus test rules alerts.test.yml
.venv/bin/python scripts/check_connectivity.py --timeout 55 --duration 35
PYTHONPATH=. .venv/bin/python /tmp/crossx-final-security.py
# Scripts above are preserved as *-script.txt alongside this file.
source docs/evidence/final-acceptance/environment.sh
.venv/bin/python /tmp/crossx-final-identity.py
PYTHONPATH=. .venv/bin/python /tmp/crossx-final-finish.py
# finish-script.txt waits for the complete 900-second end marker before diagnostics/lifecycle.
# No down or volume deletion command is used.
.venv/bin/python /tmp/crossx-final-installed.py
