"""Getrennte, capability-orientierte Quell- und Aktions-Registries."""
from dataclasses import dataclass
import threading


class PluginRegistrationError(ValueError):
    pass


class SourcePlugin:
    key = ""
    label = ""
    languages = ()
    fields = ()
    nightly_capable = False


class ActionPlugin:
    key = ""
    label = ""
    effects = ()
    needs_secrets = ()


@dataclass(frozen=True)
class SourceContext:
    """Lesekontext: kein Writer und nur auf die eigene Quelle begrenzte Geheimnisse."""
    http: object
    reader: object
    secrets: object


@dataclass(frozen=True)
class ActionContext:
    """Aktionskontext: Schreibzugriff ist auf diese Aktionsausführung beschränkt."""
    http: object
    reader: object
    writer: object
    secrets: object


class PluginRegistry:
    def __init__(self, name, base_type):
        if name not in {"source", "action"}:
            raise ValueError("Registry muss 'source' oder 'action' heißen.")
        self.name = name
        self.base_type = base_type
        self._plugins = {}
        self._lock = threading.RLock()

    def register(self, plugin):
        plugin_type = plugin if isinstance(plugin, type) else type(plugin)
        if not issubclass(plugin_type, self.base_type):
            raise PluginRegistrationError(f"Plugin muss von {self.base_type.__name__} erben.")
        key = getattr(plugin, "key", "") or getattr(plugin_type, "key", "")
        if not isinstance(key, str) or not key.strip():
            raise PluginRegistrationError("Plugin-Key darf nicht leer sein.")
        with _CLAIM_LOCK:
            previous = _CLAIMS.get(plugin_type)
            if previous is not None and previous != self.name:
                raise PluginRegistrationError("Eine Plugin-Klasse darf nur in einer Registry registriert sein.")
            with self._lock:
                if key in self._plugins:
                    raise PluginRegistrationError("Plugin-Key ist in dieser Registry bereits vergeben.")
                _CLAIMS[plugin_type] = self.name
                self._plugins[key] = plugin
        return plugin

    def get(self, key):
        with self._lock:
            return self._plugins.get(key)

    def all(self):
        with self._lock:
            return tuple(self._plugins.values())

    def keys(self):
        with self._lock:
            return tuple(self._plugins.keys())


_CLAIM_LOCK = threading.RLock()
_CLAIMS = {}
SOURCES = PluginRegistry("source", SourcePlugin)
ACTIONS = PluginRegistry("action", ActionPlugin)


def register_source(plugin):
    return SOURCES.register(plugin)


def register_action(plugin):
    return ACTIONS.register(plugin)
