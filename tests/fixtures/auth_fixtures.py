# tests/fixtures/auth_fixtures.py - Auth-specific fixtures
import pytest
from unittest.mock import Mock

@pytest.fixture
def mock_auth_service():
    auth_service = Mock()
    mock_creds = Mock()
    mock_creds.publickey = "test_public_key"
    mock_creds.secretkey = "test_secret_key"
    auth_service.get_credentials.return_value = mock_creds
    return auth_service