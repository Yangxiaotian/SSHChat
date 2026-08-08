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
        
        # Test 1: Create upload session (no filename: it comes from the upload)
        print("  Test 1: Create upload session...")
        transfer = store.create_upload_session(
            sender="alice",
            recipients=["bob", "charlie"],
            room=None
        )
        
        assert transfer.sender == "alice"
        assert transfer.filename == ""
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
        assert "上传密钥" in error
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
        
        success = store.mark_upload_complete(
            transfer.upload_token, test_file, file_size, "C:\\docs\\test.txt"
        )
        assert success
        
        store.upload_complete_callback = original_callback
        
        # Reload transfer to check it was saved
        transfer = store.get_transfer_by_token(transfer.upload_token)
        assert transfer.upload_used
        assert transfer.file_path == test_file
        assert transfer.file_size == file_size
        # Filename is taken from the uploaded file, with any path stripped
        assert transfer.filename == "test.txt"
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
        assert "下载密钥" in error
        print("    ✓ Wrong download key rejected")
        
        # Test 7: Mark download complete
        print("  Test 7: Mark download complete...")
        success = store.mark_download_complete(bob_token)
        assert success
        
        # Reload and verify
        transfer = store.get_transfer_by_token(bob_token)
        assert transfer.download_used[bob_token]
        print("    ✓ Download marked complete")
        
        # Test 8: A completed download retires the link by default
        print("  Test 8: Download URL cannot be reused...")
        valid, t, error = store.validate_download(bob_token, bob_key)
        assert not valid
        assert "已使用过" in error
        
        # ...unless one-time downloads are explicitly turned off
        file_sharing.ONE_TIME_DOWNLOAD = False
        try:
            valid, t, error = store.validate_download(bob_token, bob_key)
            assert valid, f"Reuse should be allowed when opted out: {error}"
        finally:
            file_sharing.ONE_TIME_DOWNLOAD = True
        print("    ✓ One-time by default, reusable only when explicitly opted out")
        
        # Test 9: Charlie can still download
        print("  Test 9: Other recipient can still download...")
        charlie_token = transfer.download_tokens["charlie"]
        charlie_key = transfer.download_keys["charlie"]
        
        valid, t, error = store.validate_download(charlie_token, charlie_key)
        assert valid, f"Charlie's download validation failed: {error}"
        print("    ✓ Other recipient's download still valid")
        
        # Test 9b: Tickets are single-use and separate per purpose
        print("  Test 9b: Single-use preview/download tickets...")
        t, tickets, error = store.issue_tickets(charlie_token, charlie_key)
        assert t is not None, error
        assert set(tickets) == {"preview", "download"}
        assert tickets["preview"] != tickets["download"]
        
        # A wrong key mints nothing
        _, empty, error = store.issue_tickets(charlie_token, "WRONG3")
        assert not empty and "密钥" in error
        
        # Each ticket works exactly once
        got, entry, error = store.consume_ticket(tickets["preview"])
        assert got is not None and entry.kind == "preview", error
        got, entry, error = store.consume_ticket(tickets["preview"])
        assert got is None and "已被使用" in error, "preview ticket was replayable"
        
        # Previewing leaves the download link alone
        valid, _, error = store.validate_download(charlie_token, charlie_key)
        assert valid, f"preview must not consume the download link: {error}"
        
        got, entry, error = store.consume_ticket(tickets["download"])
        assert got is not None and entry.kind == "download", error
        got, _, error = store.consume_ticket(tickets["download"])
        assert got is None and "已被使用" in error, "download ticket was replayable"
        
        # Re-entering the key retires whatever was minted before
        _, first, _ = store.issue_tickets(charlie_token, charlie_key)
        _, second, _ = store.issue_tickets(charlie_token, charlie_key)
        assert first["download"] != second["download"]
        got, _, error = store.consume_ticket(first["download"])
        assert got is None, "superseded ticket stayed valid"
        got, _, error = store.consume_ticket(second["download"])
        assert got is not None, error
        print("    ✓ Tickets are per-purpose, single-use and superseded on reissue")
        
        # Test 10: Room transfer
        print("  Test 10: Room transfer...")
        room_transfer = store.create_upload_session(
            sender="dave",
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
