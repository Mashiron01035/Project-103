"""

    -*- coding: utf-8 -*-
    Created by Mashiron on 23.11.2025.
    File        : conftest
    Project     : Project-103
    Description : Pytest configuration and shared fixtures for strategy tests.

    SPDX-License-Identifier: AGPL-3.0

"""

import sys
from pathlib import Path

import pytest

# Add the project root to PYTHONPATH
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


@pytest.fixture(scope="session")
def project_root_path():
    """Path to the project root directory"""
    return project_root


@pytest.fixture
def test_symbol():
    """Test trading pair symbol"""
    return "SOLUSDT"


@pytest.fixture
def test_timeframe():
    """Test timeframe"""
    return "15m"


# pytest configuration for async tests
def pytest_configure(config):
    """pytest configuration"""
    config.addinivalue_line("markers", "asyncio: mark test as async")
    config.addinivalue_line("markers", "slow: mark test as slow running")
    config.addinivalue_line("markers", "integration: mark test as integration test")
