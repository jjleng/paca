import os
from typing import Any
from unittest.mock import MagicMock, patch

import click
import pytest

from light.cli.utils import init_pulumi, resolve_image, validate_name
from light.utils import get_pulumi_root


def test_resolve_image() -> None:
    with patch("os.path.abspath") as mock_abspath, patch(
        "os.path.expanduser"
    ) as mock_expanduser, patch("os.path.basename") as mock_basename, patch(
        "light.cli.utils.build"
    ) as mock_build, patch(
        "light.cli.utils.read_current_cluster_data"
    ) as mock_read_current_cluster_data:
        mock_abspath.return_value = "/absolute/path/to/source_dir"
        mock_expanduser.return_value = "/path/to/source_dir"
        mock_basename.return_value = "source_dir"
        mock_build.return_value = None
        mock_read_current_cluster_data.return_value = "registry_uri"

        result = resolve_image(None, "source_dir")
        assert result == "registry_uri:source_dir-latest"

        # Test case when image is provided and source_dir is None
        result = resolve_image("image", None)
        assert result == "registry_uri:image"

        # Test case when neither image nor source_dir is provided
        with pytest.raises(click.exceptions.Exit):
            resolve_image(None, None)

        # Test case when both image and source_dir are provided
        with pytest.raises(click.exceptions.Exit):
            resolve_image("image", "source_dir")


def test_validate_name() -> None:
    mock_func = MagicMock()

    decorated_func = validate_name(mock_func)

    # Test case when name is valid
    decorated_func("valid-name")
    mock_func.assert_called_once_with("valid-name")

    mock_func.reset_mock()

    # Test case when name is not valid
    with pytest.raises(click.exceptions.Exit):
        decorated_func("Invalid-Name")
    mock_func.assert_not_called()

    mock_func.reset_mock()

    # Test case when name is too long
    with pytest.raises(click.exceptions.Exit):
        decorated_func("a" * 64)
    mock_func.assert_not_called()


def test_init_pulumi() -> None:
    with patch.dict(
        os.environ,
        {
            "PULUMI_CONFIG_PASSPHRASE": "test_passphrase",
            "PULUMI_BACKEND_URL": "test_backend_url",
        },
    ), patch("os.makedirs") as mock_makedirs:
        init_pulumi()

        assert os.environ["PULUMI_CONFIG_PASSPHRASE"] == "test_passphrase"
        assert os.environ["PULUMI_BACKEND_URL"] == "test_backend_url"

        mock_makedirs.assert_called_once()
