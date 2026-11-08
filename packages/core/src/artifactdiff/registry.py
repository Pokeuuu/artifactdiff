"""Plugin registry with explicit registration.

Safety policy (see docs/security.md):

* Built-in plugins are registered from a fixed list of import paths shipped with ArtifactDiff.
* Third-party plugins advertised through the ``artifactdiff.plugins`` entry-point group are
  *discovered* (their names are listed, which executes no code) but are only *imported* when the
  user enables them by name in the configuration or passes ``--plugin-module`` explicitly.
* Every plugin class is validated (name, semantic version, plugin API major version, options
  model) before it can be used.
"""

from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from importlib.metadata import entry_points

from artifactdiff.errors import PluginNotFoundError, PluginUnavailableError, PluginValidationError
from artifactdiff.files import ArtifactFile
from artifactdiff.models import PluginMetadata
from artifactdiff.plugin import ArtifactPlugin, Detection

log = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "artifactdiff.plugins"

#: Plugins that are only chosen when no specific plugin supports the inputs.
FALLBACK_PLUGINS = ("text", "binary")


@dataclass(frozen=True)
class DiscoveredPlugin:
    name: str
    target: str  # "module:attr" as declared by the distribution
    distribution: str | None
    enabled: bool


@dataclass(frozen=True)
class Selection:
    plugin: ArtifactPlugin
    old_detection: Detection | None
    new_detection: Detection | None
    explicit: bool = False


def load_object(spec: str) -> object:
    """Import ``module:attribute``. Only call this for code the user explicitly trusts."""
    module_name, sep, attr = spec.partition(":")
    if not sep or not module_name or not attr:
        raise PluginValidationError(f"Plugin spec must look like 'package.module:ClassName', got {spec!r}")
    module = importlib.import_module(module_name)
    obj: object = module
    for part in attr.split("."):
        obj = getattr(obj, part)
    return obj


class PluginRegistry:
    def __init__(self) -> None:
        self._classes: dict[str, type[ArtifactPlugin]] = {}
        self._builtin: set[str] = set()
        self._instances: dict[str, ArtifactPlugin] = {}

    # ------------------------------------------------------------------ registration
    def register(self, cls: type[ArtifactPlugin], *, builtin: bool = False, replace: bool = False) -> None:
        if not (isinstance(cls, type) and issubclass(cls, ArtifactPlugin)):
            raise PluginValidationError(f"{cls!r} is not an ArtifactPlugin subclass")
        cls.validate_class()
        if cls.name in self._classes and not replace:
            raise PluginValidationError(f"A plugin named {cls.name!r} is already registered")
        self._classes[cls.name] = cls
        self._instances.pop(cls.name, None)
        if builtin:
            self._builtin.add(cls.name)
        log.debug("registered plugin", extra={"plugin": cls.name, "version": cls.version})

    def register_spec(self, spec: str, *, builtin: bool = False) -> type[ArtifactPlugin]:
        obj = load_object(spec)
        if not (isinstance(obj, type) and issubclass(obj, ArtifactPlugin)):
            raise PluginValidationError(f"{spec} does not refer to an ArtifactPlugin subclass")
        self.register(obj, builtin=builtin)
        return obj

    def unregister(self, name: str) -> None:
        self._classes.pop(name, None)
        self._instances.pop(name, None)
        self._builtin.discard(name)

    # ------------------------------------------------------------------- discovery
    @staticmethod
    def discover_entry_points(enabled: set[str] | frozenset[str] = frozenset()) -> list[DiscoveredPlugin]:
        found = []
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            dist = ep.dist.name if ep.dist is not None else None
            found.append(DiscoveredPlugin(ep.name, ep.value, dist, ep.name in enabled))
        return sorted(found, key=lambda d: d.name)

    def load_enabled_entry_points(self, enabled: set[str] | frozenset[str]) -> list[str]:
        """Import and register only the entry-point plugins the user enabled by name."""
        loaded = []
        for ep in entry_points(group=ENTRY_POINT_GROUP):
            if ep.name not in enabled or ep.name in self._classes:
                continue
            cls = ep.load()
            if not (isinstance(cls, type) and issubclass(cls, ArtifactPlugin)):
                raise PluginValidationError(f"Entry point {ep.name} does not refer to an ArtifactPlugin")
            self.register(cls)
            loaded.append(ep.name)
        return loaded

    # --------------------------------------------------------------------- lookup
    def names(self) -> list[str]:
        return sorted(self._classes)

    def is_builtin(self, name: str) -> bool:
        return name in self._builtin

    def get_class(self, name: str) -> type[ArtifactPlugin]:
        try:
            return self._classes[name]
        except KeyError:
            raise PluginNotFoundError(f"Unknown plugin {name!r}. Available: {', '.join(self.names())}") from None

    def get(self, name: str) -> ArtifactPlugin:
        cls = self.get_class(name)
        available, reason = cls.check_available()
        if not available:
            raise PluginUnavailableError(f"Plugin {name!r} is unavailable: {reason}")
        if name not in self._instances:
            self._instances[name] = cls()
        return self._instances[name]

    def metadata(self, name: str) -> PluginMetadata:
        return self.get_class(name).metadata(builtin=self.is_builtin(name))

    def all_metadata(self) -> list[PluginMetadata]:
        return [self.metadata(n) for n in self.names()]

    # ------------------------------------------------------------------ selection
    def select(self, old: ArtifactFile, new: ArtifactFile, plugin: str | None = None) -> Selection | None:
        """Pick the plugin for a pair of files.

        Ranking: specific plugins before fallbacks, then detection strength (the *weaker* of the
        two files' detections counts), then plugin priority, then name for determinism.
        """
        if plugin is not None:
            inst = self.get(plugin)
            return Selection(inst, inst.supports(old), inst.supports(new), explicit=True)

        candidates: list[tuple[tuple[int, int, int], Selection]] = []
        for name in self.names():
            cls = self._classes[name]
            if not cls.check_available()[0]:
                continue
            inst = self.get(name)
            d_old, d_new = inst.supports(old), inst.supports(new)
            if d_old is None or d_new is None:
                continue
            rank = (0 if name in FALLBACK_PLUGINS else 1, min(d_old.score, d_new.score), cls.priority)
            candidates.append((rank, Selection(inst, d_old, d_new)))
        if not candidates:
            return None
        best_rank = max(c[0] for c in candidates)
        best = sorted((c[1] for c in candidates if c[0] == best_rank), key=lambda s: s.plugin.name)
        return best[0]

    def available_for(self, file: ArtifactFile) -> list[tuple[str, Detection]]:
        out = []
        for name in self.names():
            if not self._classes[name].check_available()[0]:
                continue
            det = self.get(name).supports(file)
            if det is not None:
                out.append((name, det))
        return sorted(out, key=lambda x: (x[0] in FALLBACK_PLUGINS, -x[1].score, x[0]))


_default: PluginRegistry | None = None


def default_registry() -> PluginRegistry:
    """Registry pre-populated with the built-in plugins (created once per process)."""
    global _default  # noqa: PLW0603
    if _default is None:
        reg = PluginRegistry()
        from artifactdiff_plugins import BUILTIN_PLUGINS

        for spec in BUILTIN_PLUGINS:
            reg.register_spec(spec, builtin=True)
        _default = reg
    return _default
