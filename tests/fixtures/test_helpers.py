import time
from typing import Callable, Any, List

class RouterTestHelper:
    """Helper utilities for router testing"""
    
    @staticmethod
    def create_vip_frames(sender: bytes = b"test_sender", 
                         recipient: bytes = b"test_recipient",
                         subsystem: bytes = b"test_subsys", 
                         *args: bytes) -> List[bytes]:
        """Create standard VIP message frames"""
        frames = [sender, recipient, b"VIP1", b"auth_token", b"msg_id_123", subsystem]
        frames.extend(args)
        return frames
        
    @staticmethod
    def wait_for_condition(condition_func: Callable[[], bool], 
                          timeout: float = 5.0, 
                          interval: float = 0.1) -> bool:
        """Wait for a condition to become True"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            if condition_func():
                return True
            time.sleep(interval)
        return False
        
    @staticmethod
    def assert_frames_equal(actual: List[bytes], expected: List[bytes], 
                           ignore_indices: List[int] = None):
        """Assert that two frame lists are equal, optionally ignoring some indices"""
        ignore_indices = ignore_indices or []
        
        assert len(actual) == len(expected), f"Frame count mismatch: {len(actual)} vs {len(expected)}"
        
        for i, (a, e) in enumerate(zip(actual, expected)):
            if i not in ignore_indices:
                assert a == e, f"Frame {i} mismatch: {a} vs {e}"

class MessageAssertions:
    """Assertion helpers for message testing"""
    
    @staticmethod
    def assert_hello_response(frames: List[bytes]):
        """Assert frames contain a valid hello response"""
        assert len(frames) >= 7
        assert frames[2] == b"VIP1"  # Protocol
        assert frames[5] == b"hello"  # Subsystem
        assert frames[6] == b"welcome"  # Response
        
    @staticmethod
    def assert_ping_response(frames: List[bytes]):
        """Assert frames contain a valid ping response"""
        assert len(frames) >= 7
        assert frames[2] == b"VIP1"  # Protocol
        assert frames[5] == b"ping"  # Subsystem
        assert frames[6] == b"pong"  # Response
        
    @staticmethod
    def assert_error_response(frames: List[bytes], expected_original_subsystem: bytes = None):
        """Assert frames contain a valid error response"""
        assert len(frames) >= 8
        assert frames[2] == b"VIP1"  # Protocol
        assert frames[5] == b"error"  # Subsystem
        if expected_original_subsystem:
            assert frames[-1] == expected_original_subsystem