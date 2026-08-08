#!/usr/bin/env python3
"""Simple test for file sharing functionality."""

import sys
import os
import tempfile

sys.path.insert(0, '/workspace')
import file_sharing

print("=" * 60)
print("Testing File Sharing Module")
print("=" * 60)

# Test 1: Token and key generation
print("\n1. Testing token/key generation...")
store = file_sharing.FileTransferStore(
    storage_dir='/tmp/test_file_sharing',
    store_path='/tmp/test_transfers.json'
)

token = store.generate_token()
key = store.generate_key()
assert len(token) > 30
assert len(key) == 6
assert key.isupper() or key.isdigit()
print(f"   ✓ Generated token: {len(token)} chars")
print(f"   ✓ Generated key: {key}")

# Test 2: Create upload session
print("\n2. Creating upload session...")
transfer = store.create_upload_session(
    sender="alice",
    filename="document.pdf",
    recipients=["bob", "charlie"],
    room=None
)

assert transfer.sender == "alice"
assert transfer.filename == "document.pdf"
assert len(transfer.upload_token) > 0
assert len(transfer.upload_key) == 6
assert not transfer.upload_used
print(f"   ✓ Transfer ID: {transfer.transfer_id}")
print(f"   ✓ Upload token: {transfer.upload_token[:20]}...")
print(f"   ✓ Upload key: {transfer.upload_key}")

# Test 3: Validate recipients
print("\n3. Validating recipients...")
assert len(transfer.download_tokens) == 2
assert "bob" in transfer.download_tokens
assert "charlie" in transfer.download_tokens
assert transfer.download_tokens["bob"] != transfer.download_tokens["charlie"]
assert transfer.download_keys["bob"] != transfer.download_keys["charlie"]
print(f"   ✓ Bob's key: {transfer.download_keys['bob']}")
print(f"   ✓ Charlie's key: {transfer.download_keys['charlie']}")

# Test 4: Validate upload
print("\n4. Validating upload...")
valid, t, error = store.validate_upload(transfer.upload_token, transfer.upload_key)
assert valid, f"Validation failed: {error}"
assert t.transfer_id == transfer.transfer_id
print("   ✓ Upload validation successful")

# Test 5: Wrong key rejection
print("\n5. Testing wrong key rejection...")
valid, t, error = store.validate_upload(transfer.upload_token, "WRONG1")
assert not valid
assert "Invalid upload key" in error
print(f"   ✓ Wrong key rejected: {error}")

# Test 6: Mark upload complete (without callback)
print("\n6. Marking upload complete...")
with tempfile.NamedTemporaryFile(delete=False) as f:
    f.write(b"test file content")
    test_file = f.name

file_size = os.path.getsize(test_file)
store.upload_complete_callback = None  # Disable callback
success = store.mark_upload_complete(transfer.upload_token, test_file, file_size)
assert success

transfer = store.get_transfer_by_token(transfer.upload_token)
assert transfer.upload_used
assert transfer.file_path == test_file
print(f"   ✓ Upload marked complete, size: {file_size} bytes")

# Test 7: Validate download
print("\n7. Validating download...")
bob_token = transfer.download_tokens["bob"]
bob_key = transfer.download_keys["bob"]

valid, t, error = store.validate_download(bob_token, bob_key)
assert valid, f"Download validation failed: {error}"
print("   ✓ Bob's download validation successful")

# Test 8: Mark download complete
print("\n8. Marking download complete...")
success = store.mark_download_complete(bob_token)
assert success

transfer = store.get_transfer_by_token(bob_token)
assert transfer.download_used[bob_token]
print("   ✓ Download marked complete")

# Test 9: Cannot reuse download URL
print("\n9. Testing download URL reuse prevention...")
valid, t, error = store.validate_download(bob_token, bob_key)
assert not valid
assert "already used" in error
print(f"   ✓ Used URL rejected: {error}")

# Test 10: Room transfer
print("\n10. Testing room transfer...")
room_transfer = store.create_upload_session(
    sender="dave",
    filename="presentation.pptx",
    recipients=["eve", "frank", "grace"],
    room="meeting"
)

assert room_transfer.room == "meeting"
assert len(room_transfer.download_tokens) == 3
print(f"   ✓ Room transfer created for room: {room_transfer.room}")
print(f"   ✓ Recipients: {', '.join(room_transfer.download_tokens.keys())}")

# Cleanup
os.unlink(test_file)

print("\n" + "=" * 60)
print("✅ All tests passed successfully!")
print("=" * 60)
