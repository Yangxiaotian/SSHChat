#!/usr/bin/env python3
"""
Full integration test for offline file leave bug in federation.

Scenario:
1. Alice on node-a sends file to offline Bob
2. node-a stores offline file leave and broadcasts fleave to all nodes
3. Bob logs in on node-b and receives the file message
4. node-b broadcasts fleave_clear to all nodes
5. node-a should remove the message from Bob's mailbox
6. Alice on node-a runs /leave bob - should show no messages

Bug: Step 6 still shows the message
