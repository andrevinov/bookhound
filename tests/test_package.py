import pytest
import bookhound

@pytest.mark.revised
def test_package_import_exposes_version() -> None:
    assert bookhound.__version__ == "0.1.0"
