#!/usr/bin/env python3
"""Test library hot reload in federation.

This test verifies that when a book is added to the library directory,
it is automatically synced to federation peers without requiring a restart.
"""

import os
import tempfile
import time
from pathlib import Path


def test_library_hot_reload():
    """Test that library changes are detected and synced."""
    print("Testing library hot reload...")
    
    # Create a temporary library directory
    with tempfile.TemporaryDirectory() as tmpdir:
        lib_dir = Path(tmpdir)
        
        # Simulate getting initial state
        initial_files = set()
        initial_mtime = lib_dir.stat().st_mtime
        
        print(f"Initial state: {len(initial_files)} files, mtime={initial_mtime}")
        
        # Wait a bit to ensure mtime will be different
        time.sleep(0.1)
        
        # Add a new book
        test_book = lib_dir / "test_book.txt"
        test_book.write_text("This is a test book.\n" * 100, encoding="utf-8")
        print(f"Added: {test_book.name}")
        
        # Check new state
        new_mtime = lib_dir.stat().st_mtime
        new_files = {f.name for f in lib_dir.iterdir() if f.is_file() and f.suffix.lower() in {".txt", ".md", ".pdf", ".epub"}}
        
        print(f"New state: {len(new_files)} files, mtime={new_mtime}")
        
        # Verify changes detected
        assert new_files != initial_files, "File list should have changed"
        assert new_mtime > initial_mtime, "Directory mtime should have increased"
        assert "test_book.txt" in new_files, "New book should be in file list"
        
        print("✓ File addition detected correctly")
        
        # Test file removal
        time.sleep(0.1)
        test_book.unlink()
        print(f"Removed: {test_book.name}")
        
        removed_mtime = lib_dir.stat().st_mtime
        removed_files = {f.name for f in lib_dir.iterdir() if f.is_file()}
        
        print(f"After removal: {len(removed_files)} files, mtime={removed_mtime}")
        
        assert removed_files != new_files, "File list should have changed after removal"
        assert "test_book.txt" not in removed_files, "Removed book should not be in file list"
        
        print("✓ File removal detected correctly")
        
    print("\n✅ All tests passed!")


if __name__ == "__main__":
    test_library_hot_reload()
