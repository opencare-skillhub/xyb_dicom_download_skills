import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

import main as main_mod


class StubRunner:
    def __init__(self):
        self.calls = []

    async def __call__(self, args, urls_with_password=None):
        self.calls.append((args, urls_with_password))


@pytest.fixture
def runner_stub(monkeypatch):
    stub = StubRunner()
    monkeypatch.setattr(main_mod, "run_download", stub)
    return stub


def test_parse_args_prefers_config_and_cli(tmp_path, monkeypatch):
    cfg = tmp_path / "dicom_download.toml"
    cfg.write_text(
        "\n".join(
            [
                'provider = "fz"',
                'mode = "diag"',
                "headless = true",
                "max_rounds = 7",
                "step_wait_ms = 120",
                'out_parent = "./cfg-downloads"',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    args = main_mod.parse_args(["--config", str(cfg), "--url", "https://example.com/a", "--step-wait-ms", "200"])

    assert args.provider == "fz"
    assert args.mode == "diag"
    assert args.headless is True
    assert args.max_rounds == 7
    assert args.step_wait_ms == 200
    assert args.out_parent == "./cfg-downloads"
    assert args.url == "https://example.com/a"


def test_resolve_urls_with_passwords_default_urls_txt(tmp_path, monkeypatch):
    urls = tmp_path / "urls.txt"
    urls.write_text("https://a/viewer\nhttps://b/viewer 安全码:1234\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    args = main_mod.parse_args([])
    result = main_mod.resolve_urls_with_passwords(args)

    assert result == [("https://a/viewer", None), ("https://b/viewer", "1234")]


def test_resolve_urls_with_passwords_missing_default_raises(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = main_mod.parse_args([])

    with pytest.raises(FileNotFoundError):
        main_mod.resolve_urls_with_passwords(args)


def test_save_config_writes_toml(tmp_path, monkeypatch):
    cfg = tmp_path / "dicom_download.toml"
    monkeypatch.chdir(tmp_path)

    args = main_mod.parse_args([
        "--config",
        str(cfg),
        "--url",
        "https://example.com/a",
        "--provider",
        "fz",
        "--max-rounds",
        "9",
        "--step-wait-ms",
        "180",
        "--save-config",
    ])

    payload = main_mod.build_config_payload(args)
    main_mod.write_config_file(args.config, payload)

    text = cfg.read_text(encoding="utf-8")
    assert 'provider = "fz"' in text
    assert 'max_rounds = 9' in text
    assert 'step_wait_ms = 180' in text
    assert 'url =' not in text
    assert 'urls_file =' not in text
    assert 'cloud_password =' not in text


def test_auto_generate_starter_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "dicom_download.toml").exists()

    created = main_mod.ensure_starter_config_file("dicom_download.toml", explicit_config=False)
    assert created is True

    text = (tmp_path / "dicom_download.toml").read_text(encoding="utf-8")
    assert 'provider = "auto"' in text
    assert 'max_rounds = 3' in text
    assert 'step_wait_ms = 50' in text


def test_main_menu_single_url_routes_to_runner(tmp_path, monkeypatch, runner_stub, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dicom_download.toml").write_text(
        'provider = "auto"\nmode = "all"\nheadless = false\nout_parent = "./downloads"\nmax_rounds = 3\nstep_wait_ms = 50\n',
        encoding="utf-8",
    )
    inputs = iter([
        "1",  # single url
        "https://example.com/viewer?share_id=AA",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    ])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    monkeypatch.setattr(main_mod, "select_menu_action", lambda: "1")
    monkeypatch.setattr(main_mod, "prompt_text", lambda label, default=None: "https://example.com/viewer?share_id=AA" if "URL" in label else (default or ""))
    monkeypatch.setattr(main_mod, "prompt_yes_no", lambda label, default=False: default)
    monkeypatch.setattr(main_mod, "prompt_int", lambda label, default: default)
    monkeypatch.setattr(main_mod, "collect_menu_settings", lambda state: dict(state))

    main_mod.interactive_main()

    assert runner_stub.calls, "main menu should invoke the download runner"
    args, urls_with_password = runner_stub.calls[0]
    assert args.url == "https://example.com/viewer?share_id=AA"
    assert urls_with_password == [("https://example.com/viewer?share_id=AA", None)]


def test_main_menu_view_config(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "dicom_download.toml").write_text('provider = "fz"\nmax_rounds = 5\n', encoding="utf-8")
    monkeypatch.setattr(main_mod, "select_menu_action", lambda: "4")

    main_mod.interactive_main()

    out = capsys.readouterr().out
    assert 'provider = "fz"' in out
    assert 'max_rounds = 5' in out
