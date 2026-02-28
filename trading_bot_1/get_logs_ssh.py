import subprocess
import time
import sys

# Start ssh with pseudo-tty to force password prompt to stdin/stderr if we could, but sshpass is better if available.
# Since we don't have sshpass or pexpect, let's just make the user run the command themselves, or we use a clever python trick.
