"""Storage-layer exceptions.

Custom exception types let services and tests distinguish between
"the thing wasn't found" and "the operation failed for another reason".
"""

from __future__ import annotations


class StorageError(Exception):
    """Base class for all Curator storage errors."""


class EntityNotFoundError(StorageError):
    """Raised when a get-by-id lookup finds nothing.

    Attributes:
        entity_type: 'file', 'bundle', 'edge', etc.
        entity_id: the id that was looked up
    """

    def __init__(self, entity_type: str, entity_id: object):
        self.entity_type = entity_type
        self.entity_id = entity_id
        super().__init__(f"{entity_type} {entity_id!r} not found")


class DuplicateEntityError(StorageError):
    """Raised when an insert would violate a UNIQUE constraint."""


class MigrationError(StorageError):
    """Raised when a migration fails to apply cleanly."""
