#!/usr/bin/env python3
"""
Test file sharing functionality.
"""

import os
import sys
import tempfile
import time
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, os.path.dirname(__file__))

import file_sharing


def test_file_transfer_store():
    """Test basic FileTransferStore functionality."""
    print("Testing FileTransferStore...")
    
    with tempfile.TemporaryDirectory() as tmpdir:
        storage_dir = os.path.join(tmpdir, "files")
        store_path = os.path.join(tmpdir, "transfers.json")
        
        store = file_sharing.FileTransferStore(
            storage_dir=storage_dir,
            store_path=store_path
        )
        
        # Test 1: Create upload session
        print("  Test 1: Create upload session...")
        transfer = store.create_upload_session(
            sender="alice",
            filename="test.txt",
            recipients=["bob", "charlie"],
            room=None
        )
        
        assert transfer.sender == "alice"
        assert transfer.filename == "test.txt"
        assert len(transfer.upload_token) > 0
        assert len(transfer.upload_key) == 6
        assert not transfer.upload_used
        assert len(transfer.download_tokens) == 2
        assert "bob" in transfer.download_tokens
        assert "charlie" in transfer.download_tokens
        assert len(transfer.download_keys["bob"]) == 6
        assert len(transfer.download_keys["charlie"]) == 6
        # Each recipient should have different tokens and keys
        assert transfer.download_tokens["bob"] != transfer.download_tokens["charlie"]
        assert transfer.download_keys["bob"] != transfer.download_keys["charlie"]
        print("    ✓ Upload session created successfully")
        
        # Test 2: Validate upload with correct key
        print("  Test 2: Validate upload...")
        valid, t, error = store.validate_upload(transfer.upload_token, transfer.upload_key)
        assert valid, f"Upload validation failed: {error}"
        assert t.transfer_id == transfer.transfer_id
        print("    ✓ Upload validation successful")
        
        # Test 3: Validate upload with wrong key
        print("  Test 3: Validate upload with wrong key...")
        valid, t, error = store.validate_upload(transfer.upload_token, "WRONG1")
        assert not valid
        assert "Invalid upload key" in error
        print("    ✓ Wrong key rejected correctly")
        
        # Test 4: Mark upload complete
        print("  Test 4: Mark upload complete...")
        test_file = os.path.join(storage_dir, "test_upload.txt")
        os.makedirs(storage_dir, exist_ok=True)
        with open(test_file, "w") as f:
            f.write("test content")
        file_size = os.path.getsize(test_file)
        
        # Temporarily disable callback to avoid hanging
        original_callback = store.upload_complete_callback
        store.upload_complete_callback = None
        
        success = store.mark_upload_complete(transfer.upload_token, test_file, file_size)
        assert success
        
        store.upload_complete_callback = original_callback
        
        # Reload transfer to check it was saved
        transfer = store.get_transfer_by_token(transfer.upload_token)
        assert transfer.upload_used
        assert transfer.file_path == test_file
        assert transfer.file_size == file_size
        print("    ✓ Upload marked complete")
        
        # Test 5: Validate download with correct key
        print("  Test 5: Validate download...")
        bob_token = transfer.download_tokens["bob"]
        bob_key = transfer.download_keys["bob"]
        
        valid, t, error = store.validate_download(bob_token, bob_key)
        assert valid, f"Download validation failed: {error}"
        assert t.transfer_id == transfer.transfer_id
        print("    ✓ Download validation successful")
        
        # Test 6: Validate download with wrong key
        print("  Test 6: Validate download with wrong key...")
        valid, t, error = store.validate_download(bob_token, "WRONG2")
        assert not valid
        assert "Invalid download key" in error
        print("    ✓ Wrong download key rejected")
        
        # Test 7: Mark download complete
        print("  Test 7: Mark download complete...")
        success = store.mark_download_complete(bob_token)
        assert success
        
        # Reload and verify
        transfer = store.get_transfer_by_token(bob_token)
        assert transfer.download_used[bob_token]
        print("    ✓ Download marked complete")
        
        # Test 8: Cannot download again with used token
        print("  Test 8: Cannot reuse download URL...")
        valid, t, error = store.validate_download(bob_token, bob_key)
        assert not valid
        assert "already used" in error
        print("    ✓ Used download URL rejected")
        
        # Test 9: Charlie can still download
        print("  Test 9: Other recipient can still download...")
        charlie_token = transfer.download_tokens["charlie"]
        charlie_key = transfer.download_keys["charlie"]
        
        valid, t, error = store.validate_download(charlie_token, charlie_key)
        assert valid, f"Charlie's download validation failed: {error}"
        print("    ✓ Other recipient's download still valid")
        
        # Test 10: Room transfer
        print("  Test 10: Room transfer...")
        room_transfer = store.create_upload_session(
            sender="dave",
            filename="room_file.pdf",
            recipients=["eve", "frank"],
            room="testroom"
        )
        
        assert room_transfer.room == "testroom"
        assert len(room_transfer.download_tokens) == 2
        print("    ✓ Room transfer created")
        
        # Test 11: Persistence
        print("  Test 11: Persistence...")
        store2 = file_sharing.FileTransferStore(
            storage_dir=storage_dir,
            store_path=store_path
        )
        
        loaded_transfer = store2.get_transfer_by_token(transfer.upload_token)
        assert loaded_transfer is not None
        assert loaded_transfer.transfer_id == transfer.transfer_id
        assert loaded_transfer.sender == "alice"
        print("    ✓ Transfers persisted and loaded correctly")
        
        print("\n✅ All tests passed!")
        return True


def test_token_generation():
    """Test token and key generation."""
    print("Testing token/key generation...")
    
    store = file_sharing.FileTransferStore(
        storage_dir="/tmp/test_file_sharing",
        store_path="/tmp/test_transfers.json"
    )
    
    # Generate multiple tokens and keys
    tokens = set()
    keys = set()
    
    for i in range(100):
        token = store.generate_token()
        key = store.generate_key()
        
        assert len(token) > 0
        assert len(key) == 6
        assert key.isalnum()
        assert key.isupper() or key.isdigit()
        
        tokens.add(token)
        keys.add(key)
    
    # Check uniqueness (should be very high)
    assert len(tokens) >= 99, "Tokens are not unique enough"
    assert len(keys) >= 90, "Keys are not unique enough"
    
    print(f"  Generated {len(tokens)} unique tokens")
    print(f"  Generated {len(keys)} unique keys")
    print("  ✓ Token/key generation working correctly")
    print()


if __name__ == "__main__":
    print("=" * 60)
    print("File Sharing Module Tests")
    print("=" * 60)
    print()
    
    try:
        test_token_generation()
        test_file_transfer_store()
        
        print("\n" + "=" * 60)
        print("All tests completed successfully!")
        print("=" * 60)
        sys.exit(0)
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
