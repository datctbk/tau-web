"""Tests for tau.json and overall package structure."""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TAU_ROOT = ROOT.parent / "tau"


class TestPackageStructure:
    def test_tau_json_exists(self):
        assert (ROOT / "tau.json").is_file()

    def test_tau_json_valid(self):
        data = json.loads((ROOT / "tau.json").read_text())
        assert data["name"] == "tau-web"
        assert "version" in data

    def test_tau_json_has_extensions(self):
        data = json.loads((ROOT / "tau.json").read_text())
        assert "extensions" in data
        assert "extensions/web" in data["extensions"]

    def test_extension_dir_exists(self):
        assert (ROOT / "extensions" / "web").is_dir()

    def test_extension_py_exists(self):
        assert (ROOT / "extensions" / "web" / "extension.py").is_file()

    def test_readme_exists(self):
        assert (ROOT / "README.md").is_file()

    def test_tests_dir_exists(self):
        assert (ROOT / "tests").is_dir()

    def test_extension_paths_resolve(self):
        data = json.loads((ROOT / "tau.json").read_text())
        for ext_path in data.get("extensions", []):
            assert (ROOT / ext_path).is_dir(), f"Extension path {ext_path} not found"


class TestExtensionModule:
    def test_module_loads(self):
        import importlib.util

        mod_name = "_tau_ext_web_pkg"
        sys.path.insert(0, str(TAU_ROOT))

        spec = importlib.util.spec_from_file_location(
            mod_name,
            str(ROOT / "extensions" / "web" / "extension.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)

        assert hasattr(mod, "EXTENSION")
        assert mod.EXTENSION.manifest.name == "web"

    def test_extension_is_extension_subclass(self):
        import importlib.util

        mod_name = "_tau_ext_web_pkg2"
        sys.path.insert(0, str(TAU_ROOT))

        spec = importlib.util.spec_from_file_location(
            mod_name,
            str(ROOT / "extensions" / "web" / "extension.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)

        from tau.core.extension import Extension
        assert isinstance(mod.EXTENSION, Extension)
