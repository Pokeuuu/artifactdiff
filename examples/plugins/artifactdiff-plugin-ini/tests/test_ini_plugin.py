from pathlib import Path

from artifactdiff_plugin_ini.plugin import IniPlugin

from artifactdiff_sdk.testing import assert_plugin_contract, make_engine, run_plugin, write_file


def test_contract() -> None:
    assert_plugin_contract(IniPlugin)


def test_sections_and_keys(tmp_path: Path) -> None:
    old = write_file(tmp_path, "old.ini", "[db]\nhost = a\nport = 5432\n[cache]\nttl = 60\n")
    new = write_file(tmp_path, "new.ini", "[db]\nhost = b\nport = 5432\nuser = app\n[queue]\nsize = 9\n")
    r = run_plugin(IniPlugin, old, new)
    assert {(c.kind.value, c.path) for c in r.changes} == {
        ("modified", "[db].host"),
        ("added", "[db].user"),
        ("removed", "[cache]"),
        ("added", "[queue]"),
    }


def test_selected_over_text_fallback(tmp_path: Path) -> None:
    old = write_file(tmp_path, "a.ini", "[x]\nk=1\n")
    new = write_file(tmp_path, "b.ini", "[x]\nk = 1\n")
    r = make_engine(IniPlugin).compare(old, new)
    assert r.plugin is not None and r.plugin.name == "ini" and r.status == "equivalent"


def test_invalid(tmp_path: Path) -> None:
    old = write_file(tmp_path, "a.ini", "[x]\nk=1\n")
    bad = write_file(tmp_path, "b.ini", "k=1 without section\n")
    assert run_plugin(IniPlugin, old, bad).status == "error"
