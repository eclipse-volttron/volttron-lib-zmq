import pytest
import os
from pathlib import Path
from unittest.mock import patch, mock_open
from volttron.server.server_options import ServerOptions

class TestServerOptions:
    """Test ServerOptions functionality with proper isolation"""
    
    def test_default_initialization_clean_env(self, monkeypatch, tmp_path):
        """Test default ServerOptions initialization with clean environment"""
        # Set up clean test environment
        test_volttron_home = tmp_path / "volttron_home"
        monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
        
        # Mock socket.gethostname for predictable instance_name
        with patch('socket.gethostname', return_value='test-hostname'):
            options = ServerOptions()
        
        assert options.volttron_home == test_volttron_home
        assert options.instance_name == "test-hostname"
        assert isinstance(options.address, list)
        assert options.auth_enabled is True  # Default from dataclass
        assert options.enable_federation is False
        assert options.federation_url is None
        
    def test_default_initialization_no_config_file(self, monkeypatch, tmp_path):
        """Test initialization when no config file exists"""
        test_volttron_home = tmp_path / "volttron_home"
        monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
        
        # Ensure config file doesn't exist
        config_file = test_volttron_home / "config"
        assert not config_file.exists()
        
        with patch('socket.gethostname', return_value='test-hostname'):
            options = ServerOptions()
        
        # When no config file exists, address should be default from dataclass
        assert options.address == []  # Empty list from field default
        assert options.instance_name == "test-hostname"
        
    def test_initialization_with_existing_config(self, monkeypatch, tmp_path):
        """Test initialization when config file exists with multiple addresses"""
        test_volttron_home = tmp_path / "volttron_home"
        test_volttron_home.mkdir(parents=True)
        monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
        
        # Create a config file with test data including multiple addresses
        config_file = test_volttron_home / "config"
        config_content = """[volttron]
    instance-name = config_platform
    address = tcp://127.0.0.1:22916
    address = tcp://127.0.0.1:22917
    auth-enabled = False
    enable-federation = True
    federation-url = http://test-federation:8080
    """
        config_file.write_text(config_content)
        
        options = ServerOptions()
        
        # Should load from config file with BOTH addresses now
        assert options.instance_name == "config_platform"
        assert len(options.address) == 2  # Now we expect both addresses
        assert "tcp://127.0.0.1:22916" in options.address
        assert "tcp://127.0.0.1:22917" in options.address
        assert options.auth_enabled is False
        assert options.enable_federation is True
        assert options.federation_url == "http://test-federation:8080"
        
    def test_single_address_config(self, monkeypatch, tmp_path):
        """Test config file with single address still works"""
        test_volttron_home = tmp_path / "volttron_home"
        test_volttron_home.mkdir(parents=True)
        monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
        
        config_file = test_volttron_home / "config"
        config_content = """[volttron]
    instance-name = single_address_platform
    address = tcp://127.0.0.1:22916
    auth-enabled = True
    """
        config_file.write_text(config_content)
        
        options = ServerOptions()
        
        assert options.instance_name == "single_address_platform"
        assert len(options.address) == 1
        assert "tcp://127.0.0.1:22916" in options.address
        assert options.auth_enabled is True
        
    def test_multiple_address_preservation_order(self, monkeypatch, tmp_path):
        """Test that multiple addresses preserve order"""
        test_volttron_home = tmp_path / "volttron_home"
        test_volttron_home.mkdir(parents=True)
        monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
        
        config_file = test_volttron_home / "config"
        config_content = """[volttron]
    instance-name = ordered_platform
    address = tcp://127.0.0.1:22916
    address = tcp://127.0.0.1:22917
    address = tcp://127.0.0.1:22918
    """
        config_file.write_text(config_content)
        
        options = ServerOptions()
        
        assert options.instance_name == "ordered_platform"
        assert len(options.address) == 3
        # Test that order is preserved
        assert options.address[0] == "tcp://127.0.0.1:22916"
        assert options.address[1] == "tcp://127.0.0.1:22917"  
        assert options.address[2] == "tcp://127.0.0.1:22918"
        
    def test_custom_initialization_override_defaults(self, monkeypatch, tmp_path):
        """Test custom ServerOptions initialization"""
        test_volttron_home = tmp_path / "volttron_home"
        monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
        
        options = ServerOptions(
            volttron_home=test_volttron_home,
            instance_name="custom_platform",
            address=["tcp://127.0.0.1:22916", "tcp://127.0.0.1:22917"],
            auth_enabled=False,
            enable_federation=True,
            federation_url="http://federation-server:8080"
        )
        
        assert options.volttron_home == test_volttron_home
        assert options.instance_name == "custom_platform"
        assert len(options.address) == 2
        assert options.auth_enabled is False
        assert options.enable_federation is True
        assert options.federation_url == "http://federation-server:8080"
        
    def test_store_and_load_config(self, monkeypatch, tmp_path):
        """Test storing and loading configuration"""
        test_volttron_home = tmp_path / "volttron_home"
        monkeypatch.setenv("VOLTTRON_HOME", str(test_volttron_home))
        
        config_file = test_volttron_home / "test_config"
        
        # Create options with federation settings
        original_options = ServerOptions(
            volttron_home=test_volttron_home,
            instance_name="test_platform",
            address=["tcp://127.0.0.1:22916"],
            enable_federation=True,
            federation_url="http://test-federation:8080"
        )
        
        # Store to file
        original_options.store(config_file)
        assert config_file.exists()
        
        # Load from file
        loaded_options = ServerOptions.from_file(config_file)
        
        assert loaded_options.instance_name == "test_platform"
        assert loaded_options.enable_federation is True
        assert loaded_options.federation_url == "http://test-federation:8080"
        assert "tcp://127.0.0.1:22916" in loaded_options.address
        
    def test_environment_variable_isolation(self, monkeypatch, tmp_path):
        """Test that different VOLTTRON_HOME values are isolated"""
        # Test with first environment
        home1 = tmp_path / "volttron1"
        monkeypatch.setenv("VOLTTRON_HOME", str(home1))
        
        with patch('socket.gethostname', return_value='host1'):
            options1 = ServerOptions()
        
        # Test with second environment  
        home2 = tmp_path / "volttron2"
        monkeypatch.setenv("VOLTTRON_HOME", str(home2))
        
        with patch('socket.gethostname', return_value='host2'):
            options2 = ServerOptions()
        
        # Should be completely isolated
        assert options1.volttron_home != options2.volttron_home
        assert options1.instance_name != options2.instance_name