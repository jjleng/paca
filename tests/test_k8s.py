import json
import os
from unittest.mock import mock_open, patch

from light.utils import get_project_data_dir, save_kubeconfig


def test_save_kubeconfig() -> None:
    m = mock_open()
    # Replace the built-in open function with the mock object
    with patch("builtins.open", m):
        kubeconfig_json = json.dumps({"apiVersion": "v1"})
        save_kubeconfig("test", kubeconfig_json)
    f = os.path.join(get_project_data_dir(), "clusters", "test", "kubeconfig.yaml")
    m.assert_called_once_with(f, "w")
    handle = m()
    handle.write.assert_called_once()
