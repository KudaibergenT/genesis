"""generate_episode fails early and clearly on an unwritable output directory."""

import os
import stat

import pytest

import judge_adapter as ja


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses permission bits outside a capability-less container")
@pytest.mark.parametrize("existing", [False, True])
def test_unwritable_output_directory_is_reported_before_writing(tmp_path, existing):
    parent = tmp_path / "ro"
    parent.mkdir()
    target = parent / "E"
    if existing:
        target.mkdir()
        locked = target
    else:
        locked = parent
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(PermissionError, match="chmod 0777"):
            ja.generate_episode(1, {}, str(target))
        assert not existing or os.listdir(target) == []
    finally:
        locked.chmod(stat.S_IRWXU)


def test_output_directory_is_created_when_parent_is_writable(tmp_path):
    ja.generate_episode(1, {}, str(tmp_path / "a" / "b"))
    assert len(os.listdir(tmp_path / "a" / "b")) == 8
