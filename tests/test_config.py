import os

from tastytrade_mcp import config


def test_dotenv_loads_only_non_secret_settings(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "MCP_HTTP_PORT=8123",
                "TASTYTRADE_CLIENT_SECRET=must-not-load",
                "TASTYTRADE_REFRESH_TOKEN=must-not-load",
            ]
        )
    )
    monkeypatch.delenv("MCP_HTTP_PORT", raising=False)
    monkeypatch.delenv("TASTYTRADE_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("TASTYTRADE_REFRESH_TOKEN", raising=False)

    config._load_non_secret_dotenv(tmp_path / ".env")

    assert config.Config.from_env().http_port == 8123
    assert "TASTYTRADE_CLIENT_SECRET" not in os.environ
    assert "TASTYTRADE_REFRESH_TOKEN" not in os.environ


def test_environment_values_override_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("MCP_HTTP_PORT=8123\n")
    monkeypatch.setenv("MCP_HTTP_PORT", "9000")

    config._load_non_secret_dotenv(tmp_path / ".env")

    assert config.Config.from_env().http_port == 9000
