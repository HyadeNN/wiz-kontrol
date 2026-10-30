import pytest

from wizkontrol.updates import is_newer


@pytest.mark.parametrize("tag,current,expected", [
    ("v1.2.0", "1.1.0", True),
    ("v1.10.0", "1.9.0", True),
    ("v2.0.0", "1.9.9", True),
    ("v1.1.0", "1.1.0", False),
    ("v1.0.5", "1.1.0", False),
])
def test_is_newer(tag, current, expected):
    assert is_newer(tag, current) is expected
