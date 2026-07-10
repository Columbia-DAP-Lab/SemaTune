# SSH Setup Complete ✅

## Summary

SSH has been successfully configured between the server (10.10.1.1) and client (10.10.1.2) machines.

## What Was Fixed

1. **SSH Key Ownership**: Fixed user SSH keys that were incorrectly owned by root
2. **SSH Key Permissions**: Set correct permissions (600 for private keys, 644 for public keys)
3. **Bidirectional Access**: Ensured both user and root SSH work in both directions

## Current Status

### From SERVER (10.10.1.1) to CLIENT (10.10.1.2):
- ✅ User SSH: `ssh gliargko@10.10.1.2` - **WORKS**
- ✅ Root SSH: `sudo ssh root@10.10.1.2` - **WORKS**

### From CLIENT (10.10.1.2) to SERVER (10.10.1.1):
- ✅ User SSH: `ssh gliargko@10.10.1.1` - **WORKS** (verified by setup script)
- ✅ Root SSH: `sudo ssh root@10.10.1.1` - **WORKS** (verified by setup script)

## Important Notes

1. **For Root SSH**: Always use `sudo ssh root@...` instead of `ssh root@...`
   - This ensures the correct SSH key (root's key) is used
   - Regular `ssh root@...` tries to use your user's key, which won't work

2. **Mutilate Doesn't Require SSH**: The mutilate benchmark uses TCP sockets (port 19876) for communication, not SSH. However, SSH is useful for:
   - Remote administration
   - Copying files between machines
   - Running commands remotely

## Scripts Available

1. **`scripts/fix_ssh_setup.sh`**: Fixes SSH key permissions on the current machine
2. **`scripts/setup_ssh_bidirectional.sh`**: Sets up bidirectional SSH between server and client
3. **`scripts/setup_mutilate.sh`**: Main setup script (now includes SSH fixes)

## Testing SSH

To test SSH connectivity:

```bash
# From server to client
ssh gliargko@10.10.1.2 'echo "User SSH OK"'
sudo ssh root@10.10.1.2 'echo "Root SSH OK"'

# From client to server  
ssh gliargko@10.10.1.1 'echo "User SSH OK"'
sudo ssh root@10.10.1.1 'echo "Root SSH OK"'
```

## Next Steps

You're now ready to run the mutilate benchmark! The SSH setup is complete and working.
