#!/usr/bin/env python3
"""
Test case: Offline file leave message should be cleared from /leave list
when recipient logs in on a federated node and receives the message.

Bug: After sending file to offline user, when the recipient logs in on
a federated node and receives the message, the sender still sees it in
/leave list on the origin node.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from offline_messages import OfflineMessageStore
import tempfile


def test_offline_file_leave_federation_clear():
    """Reproduce the bug: file leave not cleared after federation delivery."""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        store_path = os.path.join(tmpdir, "test_messages.json")
        store = OfflineMessageStore(store_path)
        
        # Simulate: Alice sends file to Bob (Bob is offline)
        # This creates an offline file leave message
        file_leave = store.leave(
            recipient="bob",
            sender="alice",
            text="文件: test.pdf (1.5 MB)",
            kind="file",
            meta={
                "transfer_id": "test-transfer-123",
                "filename": "test.pdf",
                "file_size": 1572864,
                "download_token": "token123",
                "download_key": "key456",
                "download_url": "https://example.com/download/test.pdf",
                "room": "default",
            }
        )
        
        print("Step 1: Alice sends file to offline Bob")
        print(f"  Leave message created: {file_leave is not None}")
        
        # Check Alice's sent messages
        sent_list = store.list_sent_unread("alice", "bob")
        print(f"\nStep 2: Alice checks /leave bob")
        print(f"  Messages from alice to bob: {len(sent_list)}")
        for msg in sent_list:
            print(f"    {msg['index']}. {msg['text']}")
        
        assert len(sent_list) == 1, "Should have 1 sent message"
        
        # Simulate: Bob logs in on a federated node (not the origin node)
        # The federated node delivers the message and broadcasts fleave_clear
        # The origin node receives fleave_clear and should remove the message
        
        print("\nStep 3: Bob logs in on federated node B")
        print("  Node B delivers file message to Bob")
        print("  Node B broadcasts fleave_clear")
        
        # Simulate receiving fleave_clear on origin node A
        # This is what _fed_on_file_leave_clear() does
        removed = store.remove_file_by_transfer("bob", "test-transfer-123")
        print(f"  Node A receives fleave_clear and removes: {len(removed)} message(s)")
        
        # Now check Alice's sent messages again
        sent_list_after = store.list_sent_unread("alice", "bob")
        print(f"\nStep 4: Alice checks /leave bob again")
        print(f"  Messages from alice to bob: {len(sent_list_after)}")
        for msg in sent_list_after:
            print(f"    {msg['index']}. {msg['text']}")
        
        # BUG: This should be 0, but it might still be 1
        if len(sent_list_after) > 0:
            print("\n❌ BUG REPRODUCED: Message still appears in /leave list!")
            print("   Expected: 0 messages")
            print(f"   Actual: {len(sent_list_after)} message(s)")
            return False
        else:
            print("\n✅ Test passed: Message correctly removed from /leave list")
            return True


if __name__ == "__main__":
    success = test_offline_file_leave_federation_clear()
    sys.exit(0 if success else 1)
