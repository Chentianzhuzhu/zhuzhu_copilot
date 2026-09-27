"""
Configuration for CI/CD and testing
"""
import pytest

# Pytest configuration
def pytest_configure(config):
    """Register custom markers"""
    config.addinivalue_line("markers", "slow: marks tests as slow")
    config.addinivalue_line("markers", "integration: marks tests as integration tests")
