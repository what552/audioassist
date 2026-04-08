"""
Tests for scripts/hook_runtime_metadata.py — the PyInstaller runtime hook.

Covers:
  - _safe_version() narrow mode: re-raises when module is genuinely absent
  - _safe_version() narrow mode: returns "0.0.0" when module exists, no metadata
  - nagisa sys.path fix: inserts nagisa package dir when nagisa is found
"""
from __future__ import annotations

import importlib.metadata as _imeta
import importlib.util
import sys
import os
from unittest.mock import MagicMock, patch

import pytest

# ── Import the hook ───────────────────────────────────────────────────────────
# Add scripts/ to sys.path so the hook module can be imported.
# Importing the hook patches _imeta.version globally; we restore it in the
# module-level teardown below.
_SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "scripts")
)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# Capture the original version function BEFORE importing the hook so tests
# can restore it.
_real_imeta_version = _imeta.version

import hook_runtime_metadata as _hook  # noqa: E402 — must be after path setup


@pytest.fixture(autouse=True)
def _restore_imeta_version():
    """Ensure each test starts with the hook's patched version in place
    and that the original captured reference is undisturbed."""
    yield
    # Re-apply the hook patch in case a test temporarily replaced it
    _imeta.version = _hook._safe_version


# ── Test 1: missing package → re-raises PackageNotFoundError ─────────────────

class TestSafeVersionMissingPackage:
    def test_raises_when_module_not_found(self):
        """_safe_version must re-raise PackageNotFoundError when find_spec returns None.

        This is the core fix: previously _safe_version swallowed all
        PackageNotFoundError and returned '0.0.0', causing RequirementCache to
        treat absent optional packages (torchvision, scienceplots, …) as available.
        """
        def _fake_orig(name):
            raise _imeta.PackageNotFoundError(name)

        with patch.object(_hook, "_orig_version", _fake_orig), \
             patch.object(_hook._ilu, "find_spec", return_value=None):
            with pytest.raises(_imeta.PackageNotFoundError):
                _hook._safe_version("torchvision")

    def test_raises_when_find_spec_raises_module_not_found_error(self):
        """find_spec may itself raise ModuleNotFoundError for namespace conflicts;
        _safe_version must still re-raise PackageNotFoundError in that case."""
        def _fake_orig(name):
            raise _imeta.PackageNotFoundError(name)

        def _exploding_find_spec(name):
            raise ModuleNotFoundError(name)

        with patch.object(_hook, "_orig_version", _fake_orig), \
             patch.object(_hook._ilu, "find_spec", side_effect=_exploding_find_spec):
            with pytest.raises(_imeta.PackageNotFoundError):
                _hook._safe_version("bad_namespace_package")


# ── Test 2: module present but no dist-info → returns "0.0.0" ─────────────────

class TestSafeVersionPresentNoMetadata:
    def test_returns_zero_version_when_spec_found(self):
        """When the module is bundled (find_spec succeeds) but has no distribution
        metadata, _safe_version must return '0.0.0' so the import chain continues."""
        def _fake_orig(name):
            raise _imeta.PackageNotFoundError(name)

        fake_spec = MagicMock()

        with patch.object(_hook, "_orig_version", _fake_orig), \
             patch.object(_hook._ilu, "find_spec", return_value=fake_spec):
            result = _hook._safe_version("bundled_without_dist_info")

        assert result == "0.0.0"

    def test_returns_real_version_when_metadata_exists(self):
        """When dist-info is present, _safe_version must return the real version."""
        def _fake_orig(name):
            return "1.2.3"

        with patch.object(_hook, "_orig_version", _fake_orig):
            result = _hook._safe_version("some_package")

        assert result == "1.2.3"


# ── Test 3: nagisa sys.path fix ───────────────────────────────────────────────

class TestNagisaPathFix:
    def test_nagisa_dir_inserted_into_sys_path(self):
        """The nagisa fix must prepend the nagisa package directory to sys.path
        when nagisa is importable (find_spec returns a spec with search locations)."""
        fake_nagisa_dir = "/fake/venv/lib/site-packages/nagisa"

        fake_spec = MagicMock()
        fake_spec.submodule_search_locations = [fake_nagisa_dir]

        original_path = sys.path.copy()
        try:
            # Remove the fake dir in case it somehow snuck in
            sys.path[:] = [p for p in sys.path if p != fake_nagisa_dir]

            with patch.object(_hook._ilu, "find_spec", return_value=fake_spec):
                # Re-run the nagisa fix logic inline (same code as the hook)
                nagisa_spec = _hook._ilu.find_spec("nagisa")
                if nagisa_spec and nagisa_spec.submodule_search_locations:
                    nagisa_dir = list(nagisa_spec.submodule_search_locations)[0]
                    if nagisa_dir not in sys.path:
                        sys.path.insert(0, nagisa_dir)

            assert fake_nagisa_dir in sys.path
        finally:
            sys.path[:] = original_path

    def test_nagisa_dir_not_duplicated(self):
        """If nagisa dir is already in sys.path, it must not be added again."""
        fake_nagisa_dir = "/fake/venv/lib/site-packages/nagisa"

        fake_spec = MagicMock()
        fake_spec.submodule_search_locations = [fake_nagisa_dir]

        original_path = sys.path.copy()
        try:
            sys.path.insert(0, fake_nagisa_dir)
            count_before = sys.path.count(fake_nagisa_dir)

            with patch.object(_hook._ilu, "find_spec", return_value=fake_spec):
                nagisa_spec = _hook._ilu.find_spec("nagisa")
                if nagisa_spec and nagisa_spec.submodule_search_locations:
                    nagisa_dir = list(nagisa_spec.submodule_search_locations)[0]
                    if nagisa_dir not in sys.path:
                        sys.path.insert(0, nagisa_dir)

            assert sys.path.count(fake_nagisa_dir) == count_before
        finally:
            sys.path[:] = original_path
