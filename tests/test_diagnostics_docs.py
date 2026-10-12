"""Exercise privacy callouts with the installed documentation renderer."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import get_args

import pytest


@pytest.mark.parametrize("page", ["en/reference/api.md", "reference/api.md"])
def test_diagnostic_api_reference_matches_report_contract(page):
    from diagnostic_models import SearchReport

    (version,) = get_args(SearchReport.model_fields["report_version"].annotation)
    text = (Path(__file__).resolve().parents[1] / "docs" / page).read_text()
    assert f"Report v{version};" in text
    for field in ["strategies", "provenance", "search", "merge"]:
        assert f"`{field}`" in text


@pytest.mark.parametrize(
    ("page", "title", "body"),
    [
        (
            "en/features/diagnostics.md",
            "Trusted network and privacy",
            "WebUI, WebAPI and Torznab have no",
        ),
        (
            "features/diagnostics.md",
            "Rete affidabile e privacy",
            "WebUI, WebAPI e Torznab non hanno",
        ),
    ],
)
def test_diagnostics_privacy_warning_rendered_as_title_and_body(page, title, body):
    root = Path(__file__).resolve().parents[1]
    renderer = root / "docs/node_modules/vitepress/dist/node/index.js"
    if shutil.which("node") is None or not renderer.is_file():
        pytest.skip("Installed Node and documentation dependencies are required")
    script = """
        import { readFileSync } from 'node:fs';
        const { createMarkdownRenderer } = await import(process.argv[1]);
        const md = await createMarkdownRenderer(process.argv[2]);
        console.log(JSON.stringify(md.render(readFileSync(process.argv[3], 'utf8'))));
    """
    result = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            script,
            renderer.as_uri(),
            str(root / "docs"),
            str(root / "docs" / page),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    html = json.loads(result.stdout)
    assert f'<p class="custom-block-title">{title}</p>' in html
    assert f"<p>{body}" in html
    assert ":::" not in html
