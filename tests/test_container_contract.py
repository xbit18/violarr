import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
ENTRYPOINT = ROOT / "entrypoint.sh"
COMPOSE = ROOT / "docker-compose.yml"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish-image.yml"
SNAPSHOT_UPDATER = ROOT / "snapshot_updater.py"
SETTINGS = ROOT / "settings.py"
APP = ROOT / "app.py"
WEBAPI = ROOT / "webapi.py"


def dockerfile_text():
    return DOCKERFILE.read_text(encoding="utf-8")


def final_stage():
    text = dockerfile_text()
    starts = [match.start() for match in re.finditer(r"(?im)^FROM\s+", text)]
    return text[starts[-1] :]


def logical_instructions(text):
    return re.sub(r"\\\r?\n\s*", " ", text)


def test_entrypoint_is_lf_only_and_keeps_bash_shebang():
    content = ENTRYPOINT.read_bytes()

    assert content.startswith(b"#!/usr/bin/env bash\n")
    assert b"\r" not in content


def test_final_stage_uses_python_312_bookworm_runtime_and_pgdg_postgresql_16():
    stage = logical_instructions(final_stage())

    assert re.search(r"(?im)^FROM\s+python:3\.12-slim-bookworm(?:\s|$)", stage)
    assert "apt.postgresql.org/pub/repos/apt" in stage
    assert re.search(r"\bpostgresql-16\b", stage)
    assert re.search(r"\bpostgresql-client-16\b", stage)
    assert "/usr/lib/postgresql/16/bin" in stage
    assert "create_main_cluster = false" in stage
    assert "policy-rc.d" in stage
    assert re.search(r"\bgosu\b", stage)
    assert re.search(r"\btini\b", stage)
    assert re.search(r"\blocales\b", stage)
    assert "en_US.UTF-8 UTF-8" in stage
    assert "locale-gen en_US.UTF-8" in stage


def test_runtime_uses_utf8_locale_for_new_postgresql_clusters():
    stage = logical_instructions(final_stage())
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
    initialization = re.search(
        r'if \[ ! -s "\$PGDATA/PG_VERSION" \]; then(?P<body>.*?)\nfi',
        entrypoint,
        re.DOTALL,
    )

    assert re.search(r'\bLANG="?en_US\.utf8"?', stage)
    assert initialization is not None
    assert re.search(r"\binitdb\b", initialization["body"])
    assert "--encoding=UTF8" in initialization["body"]
    assert "--locale=en_US.utf8" in initialization["body"]


def test_final_stage_declares_only_product_volume_and_port():
    stage = final_stage()
    exposed = re.findall(r"(?im)^EXPOSE\s+(.+?)\s*$", stage)
    volumes = re.findall(r"(?im)^VOLUME\s+(.+?)\s*$", stage)

    assert exposed == ["8000"]
    assert volumes == ['["/data"]']
    assert "/var/lib/postgresql/data" not in stage
    assert not re.search(r"(?im)^EXPOSE\s+.*\b5432\b", stage)


def test_image_keeps_frontend_build_and_all_runtime_modules():
    text = logical_instructions(dockerfile_text())
    stage = logical_instructions(final_stage())

    assert re.search(r"(?im)^FROM\s+node:24-bookworm-slim\s+AS\s+frontend-build", text)
    assert re.search(r"(?im)^RUN\s+npm\s+ci\s*$", text)
    assert re.search(r"(?im)^RUN\s+npm\s+run\s+build\s*$", text)
    assert "COPY --from=frontend-build /build/frontend/dist /app/frontend-dist" in stage

    for module in (
        "app.py",
        "snapshot_updater.py",
        "settings.py",
        "result_processor.py",
        "diagnostic_models.py",
        "search_diagnostics.py",
        "search_monitor.py",
        "prowlarr.py",
        "webapi.py",
        "entrypoint.sh",
    ):
        assert re.search(rf"(?im)^COPY\s+[^\n]*\b{re.escape(module)}\b", stage)


def test_runtime_defaults_keep_data_layout_without_baking_password():
    stage = logical_instructions(final_stage())

    assert re.search(r'\bPGDATA="?/data/postgres"?', stage)
    assert re.search(r'\bSNAPSHOT_STATE_FILE="?/data/state/snapshot-version"?', stage)
    assert not re.search(r"(?im)^ENV\s+[^\n]*\bDB_PASSWORD=", stage)


def test_database_update_settings_are_not_forced_as_runtime_overrides():
    stage = logical_instructions(final_stage())
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
    compose = COMPOSE.read_text(encoding="utf-8")

    assert not re.search(r'\bDB_AUTO_UPDATE="?true"?', stage)
    assert not re.search(r'\bDB_UPDATE_INTERVAL="?86400"?', stage)

    assert 'DB_AUTO_UPDATE="${DB_AUTO_UPDATE:-true}"' not in entrypoint
    assert 'DB_UPDATE_INTERVAL="${DB_UPDATE_INTERVAL:-86400}"' not in entrypoint

    assert not re.search(r"(?m)^\s+DB_AUTO_UPDATE:", compose)
    assert not re.search(r"(?m)^\s+DB_UPDATE_INTERVAL:", compose)

    assert re.search(r'\bDB_UPDATE_START_DELAY="?60"?', stage)


def test_distribution_metadata_uses_violarr_public_identity():
    dockerfile = dockerfile_text()

    assert 'org.opencontainers.image.title="Violarr"' in dockerfile
    assert 'org.opencontainers.image.source="https://github.com/xbit18/violarr"' in dockerfile

    package_names = {
        "frontend": "violarr-webui",
        "docs": "violarr-docs",
    }
    for directory, expected_name in package_names.items():
        package = json.loads((ROOT / directory / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / directory / "package-lock.json").read_text(encoding="utf-8"))
        assert package["name"] == expected_name
        assert lock["name"] == expected_name
        assert lock["packages"][""]["name"] == expected_name


def test_compose_uses_primary_image_and_preserves_operational_aliases():
    compose = COMPOSE.read_text(encoding="utf-8")

    assert re.search(r"(?m)^\s{2}icvdb-torznab:\s*$", compose)
    assert re.search(r"(?m)^\s{4}image:\s+ghcr\.io/xbit18/violarr:latest\s*$", compose)
    assert re.search(r"(?m)^\s{4}container_name:\s+icvdb-torznab\s*$", compose)
    assert re.search(r"(?m)^\s{6}-\s+icvdb_data:/data\s*$", compose)
    assert re.search(r"(?m)^\s{4}name:\s+icvdb_torznab_data\s*$", compose)


def test_release_publishes_only_primary_image_tags():
    workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    assert "PRIMARY_IMAGE_NAME: xbit18/violarr" in workflow
    assert "${{ env.REGISTRY }}/${{ env.PRIMARY_IMAGE_NAME }}" in workflow
    assert "LEGACY_IMAGE_NAME" not in workflow
    assert "xbit18/icvdb-torznab" not in workflow

    assert "workflow_dispatch:" in workflow
    assert "ref: ${{ steps.release-tag.outputs.tag }}" in workflow

    assert "META_TAGS: ${{ steps.meta.outputs.tags }}" in workflow
    assert "META_LABELS: ${{ steps.meta.outputs.labels }}" in workflow
    assert 'tags: "${{ steps.meta_patched.outputs.tags }}"' in workflow
    assert 'labels: "${{ steps.meta_patched.outputs.labels }}"' in workflow

    assert "action: docker/build-push-action@v6" in workflow
    assert "attempt_limit: 3" in workflow
    assert "attempt_delay: 15000" in workflow


def test_snapshot_repository_remains_a_separate_compatibility_contract():
    updater = SNAPSHOT_UPDATER.read_text(encoding="utf-8")

    assert "https://api.github.com/repos/xbit18/icvdb-snapshots/releases/latest" in updater


def test_stable_data_environment_and_route_identifiers_remain_unchanged():
    dockerfile = dockerfile_text()
    settings = SETTINGS.read_text(encoding="utf-8")
    app = APP.read_text(encoding="utf-8")
    webapi = WEBAPI.read_text(encoding="utf-8")

    assert 'DEFAULT_SETTINGS_PATH = Path("/data/state/settings.json")' in settings
    assert '"schema_version": 1' in settings

    for name in (
        "DB_HOST",
        "DB_PORT",
        "DB_NAME",
        "DB_USER",
        "DB_UPDATE_START_DELAY",
    ):
        assert name in dockerfile

    for name in (
        "DB_AUTO_UPDATE",
        "DB_UPDATE_INTERVAL",
        "ICVDB_SETTINGS_PATH",
        "ICVDB_RESULT_PRESET",
        "ICVDB_PROWLARR_URL",
        "ICVDB_PROWLARR_API_KEY",
    ):
        assert name in settings

    assert '@app.get("/api")' in app
    assert 'APIRouter(prefix="/webapi"' in webapi
