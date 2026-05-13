"""Coverage closure for ``curator.storage.exceptions`` (v1.7.133).

The module is currently at 0%. Pure exception classes — construct each
one and verify the inheritance + custom attributes.
"""

from __future__ import annotations

import pytest

from curator.storage.exceptions import (
    DuplicateEntityError,
    EntityNotFoundError,
    MigrationError,
    StorageError,
)


class TestStorageError:
    def test_storage_error_is_exception(self):
        assert issubclass(StorageError, Exception)

    def test_storage_error_can_be_raised(self):
        with pytest.raises(StorageError, match="base"):
            raise StorageError("base failure")


class TestEntityNotFoundError:
    def test_inherits_from_storage_error(self):
        assert issubclass(EntityNotFoundError, StorageError)

    def test_constructor_stores_attrs(self):
        exc = EntityNotFoundError("file", "abc123")
        assert exc.entity_type == "file"
        assert exc.entity_id == "abc123"

    def test_message_includes_type_and_id_repr(self):
        exc = EntityNotFoundError("bundle", "xyz")
        assert "bundle" in str(exc)
        assert "'xyz'" in str(exc)  # !r uses repr -> quoted

    def test_can_catch_as_storage_error(self):
        with pytest.raises(StorageError):
            raise EntityNotFoundError("file", 42)


class TestDuplicateEntityError:
    def test_inherits_from_storage_error(self):
        assert issubclass(DuplicateEntityError, StorageError)

    def test_can_be_raised(self):
        with pytest.raises(DuplicateEntityError):
            raise DuplicateEntityError("dup")


class TestMigrationError:
    def test_inherits_from_storage_error(self):
        assert issubclass(MigrationError, StorageError)

    def test_can_be_raised(self):
        with pytest.raises(MigrationError):
            raise MigrationError("migration boom")
