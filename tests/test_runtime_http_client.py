from pathlib import Path

import pytest

from bookhound.config import load_settings
import bookhound.cli as cli
from bookhound.http_client import BookhoundHttpClient


@pytest.mark.revised
def test_build_http_client_uses_configured_user_agent_and_timeout(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[http]
user_agent = "BookhoundRuntimeTest/1.0"
request_timeout_seconds = 17.5
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path, project_root=tmp_path)

    http_client = cli.build_http_client(settings)

    assert isinstance(http_client, BookhoundHttpClient)
    assert http_client.config.user_agent == "BookhoundRuntimeTest/1.0"
    assert http_client.config.timeout_seconds == 17.5


@pytest.mark.revised
def test_build_http_client_applies_per_domain_rate_limit(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "bookhound.toml"
    config_path.write_text(
        """
[rate_limits]
global_rate_limit_per_second = 8
per_domain_rate_limit_per_second = 0.25
""".strip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path=config_path, project_root=tmp_path)

    http_client = cli.build_http_client(settings)

    assert http_client.config.rate_limit_per_second == 0.25
