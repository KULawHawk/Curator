"""Base class for all Curator entities.

DESIGN.md §3.1 — Three-tier attribute model:
  * Fixed:    schema-defined columns. Type-checked, indexed, queried in SQL.
  * Flex:     free-form key-value attributes in a *_flex_attrs companion table.
              Plugins and users can add arbitrary fields without schema changes.
  * Computed: read-only derived values from plugin-provided getter functions.
              Not stored; recomputed on demand.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, PrivateAttr


class CuratorEntity(BaseModel):
    """Base class for all Curator entities.

    Subclasses define their Fixed attributes as normal pydantic fields.
    Flex attributes are accessed via ``entity.flex[key]`` and persisted
    by repositories into a companion ``*_flex_attrs`` table.
    Computed attributes are accessed via ``entity.get_computed(key)``
    and supplied by plugins implementing ``curator_compute_attr``.
    """

    model_config = ConfigDict(
        validate_assignment=True,
        arbitrary_types_allowed=False,
        # Allow ORM-style construction from dicts that include extra keys —
        # repositories pull from rows that may have join columns, etc.
        extra="ignore",
    )

    # PrivateAttr keeps _flex out of pydantic serialization (model_dump etc.)
    # but accessible via instance.flex.
    _flex: dict[str, Any] = PrivateAttr(default_factory=dict)

    @property
    def flex(self) -> dict[str, Any]:
        """Free-form key-value attributes, persisted to a *_flex_attrs table."""
        return self._flex

    def get_computed(self, key: str) -> Any:
        """Return a computed attribute provided by a plugin.

        Walks the registered plugin manager and returns the first non-None
        result for ``curator_compute_attr(entity=self, key=key)``.

        Raises:
            KeyError: if no plugin provides this computed attribute.
        """
        # Imported here to avoid a circular import at module load time
        # (plugin manager imports models for type hints).
        from curator.plugins import get_plugin_manager

        results = get_plugin_manager().hook.curator_compute_attr(entity=self, key=key)
        for r in results:
            if r is not None:
                return r
        raise KeyError(f"No plugin provides computed attribute {key!r}")

    # Convenience: expose flex via dict-style access on the entity itself.
    # This is sugar; ``entity.flex[k]`` is the canonical accessor.
    def get_flex(self, key: str, default: Any = None) -> Any:
        """Get a flex attribute by key, returning ``default`` if not set."""
        return self._flex.get(key, default)

    def set_flex(self, key: str, value: Any) -> None:
        """Set a flex attribute. Repositories persist these on insert/update."""
        self._flex[key] = value

    def has_flex(self, key: str) -> bool:
        """Check whether a flex attribute is set."""
        return key in self._flex
