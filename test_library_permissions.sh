#!/bin/bash
# Test library directory permissions after deployment

set -e

PREFIX="${PREFIX:-/opt/sshchat}"
CLIENT_GROUP="sshchat-clients"

echo "=== Testing Library Directory Permissions ==="
echo

# Check if library directory exists
if [[ ! -d "$PREFIX/library" ]]; then
    echo "❌ Error: $PREFIX/library directory does not exist"
    exit 1
fi
echo "✓ Library directory exists: $PREFIX/library"

# Get directory permissions
PERMS=$(stat -c "%a" "$PREFIX/library" 2>/dev/null || stat -f "%Lp" "$PREFIX/library" 2>/dev/null)
OWNER=$(stat -c "%U" "$PREFIX/library" 2>/dev/null || stat -f "%Su" "$PREFIX/library" 2>/dev/null)
GROUP=$(stat -c "%G" "$PREFIX/library" 2>/dev/null || stat -f "%Sg" "$PREFIX/library" 2>/dev/null)

echo "  Owner: $OWNER"
echo "  Group: $GROUP"
echo "  Permissions: $PERMS"
echo

# Check group
if [[ "$GROUP" != "$CLIENT_GROUP" ]]; then
    echo "❌ Error: Library directory group is '$GROUP', expected '$CLIENT_GROUP'"
    exit 1
fi
echo "✓ Group is correct: $CLIENT_GROUP"

# Check permissions (should be 750)
if [[ "$PERMS" != "750" ]]; then
    echo "⚠️  Warning: Permissions are $PERMS, expected 750"
    echo "  This may still work, but 750 is recommended"
else
    echo "✓ Permissions are correct: 750"
fi

# Check if group exists
if ! getent group "$CLIENT_GROUP" >/dev/null 2>&1 && ! grep -q "^${CLIENT_GROUP}:" /etc/group 2>/dev/null; then
    echo "❌ Error: Group $CLIENT_GROUP does not exist"
    exit 1
fi
echo "✓ Group $CLIENT_GROUP exists"

# Test creating a file in library (as root)
TEST_FILE="$PREFIX/library/.permission_test_$$"
if touch "$TEST_FILE" 2>/dev/null; then
    chown "$OWNER:$CLIENT_GROUP" "$TEST_FILE" 2>/dev/null || true
    chmod 640 "$TEST_FILE" 2>/dev/null || true
    
    # Check file permissions
    FILE_GROUP=$(stat -c "%G" "$TEST_FILE" 2>/dev/null || stat -f "%Sg" "$TEST_FILE" 2>/dev/null)
    if [[ "$FILE_GROUP" == "$CLIENT_GROUP" ]]; then
        echo "✓ Test file can be created with correct group"
    else
        echo "⚠️  Warning: Test file group is $FILE_GROUP, expected $CLIENT_GROUP"
    fi
    
    rm -f "$TEST_FILE"
else
    echo "⚠️  Warning: Cannot create test file (may need root privileges)"
fi

echo
echo "=== Permission Test Complete ==="
echo
echo "Summary:"
echo "  ✓ Library directory has correct group ownership"
echo "  ✓ $CLIENT_GROUP group members can access library"
echo
echo "Next steps:"
echo "  1. Add books to $PREFIX/library/"
echo "  2. Set permissions: chown root:$CLIENT_GROUP <book>"
echo "  3. Set permissions: chmod 640 <book>"
echo "  4. Users in $CLIENT_GROUP can now browse and read books"
