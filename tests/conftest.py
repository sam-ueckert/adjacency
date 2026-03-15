"""Shared pytest configuration and fixtures."""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--lab", action="store_true", default=False,
        help="Run integration tests against a live containerlab topology.",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--lab"):
        skip_lab = pytest.mark.skip(reason="Need --lab flag and running containerlab topology")
        for item in items:
            if "lab" in item.keywords:
                item.add_marker(skip_lab)
