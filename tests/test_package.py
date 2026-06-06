import bookhound


def test_package_import_exposes_version() -> None:
    assert bookhound.__version__ == "0.1.0"
