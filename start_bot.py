import os
import sys
import getpass
import subprocess
import shutil

def get_docker_compose_cmd():
    if shutil.which("docker-compose"):
        return ["docker-compose"]
    elif shutil.which("docker"):
        # Check if `docker compose` is available
        result = subprocess.run(["docker", "compose", "version"], capture_output=True)
        if result.returncode == 0:
            return ["docker", "compose"]
    return None

def main():
    print("=== Hyperliquid Bot Docker Launcher ===")
    print("This script will start the bot via Docker without saving your private key to any file.")
    print("Please enter your HYPERLIQUID_PRIVATE_KEY. It will be hidden as you type.")
    
    private_key = getpass.getpass("HYPERLIQUID_PRIVATE_KEY: ").strip()
    
    if not private_key:
        print("Error: Private key cannot be empty. Exiting.")
        sys.exit(1)
        
    env = os.environ.copy()
    env["HYPERLIQUID_PRIVATE_KEY"] = private_key
    
    cmd_base = get_docker_compose_cmd()
    if not cmd_base:
        print("Error: Neither 'docker-compose' nor 'docker compose' found on this system.")
        sys.exit(1)
        
    cmd = cmd_base + ["up", "-d", "--build"]
    
    print(f"\nStarting {' '.join(cmd)} ...")
    
    try:
        subprocess.run(cmd, env=env, check=True)
        print("\nBot successfully started!")
        print(f"You can view logs using: {' '.join(cmd_base)} logs -f bot")
        print("\nNote: Make sure your HYPERLIQUID_PRIVATE_KEY is NOT saved in your .env file!")
    except subprocess.CalledProcessError as e:
        print(f"\nError: Docker compose failed with exit code {e.returncode}")
        sys.exit(e.returncode)

if __name__ == "__main__":
    main()
