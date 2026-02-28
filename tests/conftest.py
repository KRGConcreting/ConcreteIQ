"""
Pytest configuration and fixtures.
"""

import pytest

# Enable anyio for async tests
pytest_plugins = ('anyio',)
import sys
from pathlib import Path

# Add app to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
