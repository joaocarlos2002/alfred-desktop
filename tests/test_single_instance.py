import uuid

import pytest

from alfred.infrastructure.single_instance import SingleInstance


@pytest.fixture
def lock_name() -> str:
    return f"Local\\ALFRED-test-{uuid.uuid4()}"


def test_second_instance_is_rejected(lock_name: str) -> None:
    with SingleInstance(lock_name) as first:
        assert first.acquired
        with SingleInstance(lock_name) as second:
            assert not second.acquired


def test_lock_is_released_on_exit(lock_name: str) -> None:
    with SingleInstance(lock_name) as first:
        assert first.acquired
    with SingleInstance(lock_name) as again:
        assert again.acquired


def test_release_is_idempotent(lock_name: str) -> None:
    instance = SingleInstance(lock_name)
    assert instance.acquire()
    instance.release()
    instance.release()
    assert not instance.acquired
