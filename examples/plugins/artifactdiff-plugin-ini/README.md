# artifactdiff-plugin-ini (example third-party plugin)

A complete, working example of an external ArtifactDiff plugin. It compares INI files section by
section (`configparser`), reporting added/removed sections and keys and changed values, with
optional case-insensitive keys and ignored sections.

```bash
pip install -e examples/plugins/artifactdiff-plugin-ini
```

ArtifactDiff never runs third-party code implicitly. Enable the plugin explicitly:

```toml
# artifactdiff.toml
[plugins]
enabled = ["ini"]
```

```bash
artifactdiff plugins list          # shows "ini" as external
artifactdiff diff old.ini new.ini
```

Or, without installing, load the class directly: `artifactdiff diff a.ini b.ini --plugin-module artifactdiff_plugin_ini.plugin:IniPlugin`.

Run its tests with `pytest examples/plugins/artifactdiff-plugin-ini`.
