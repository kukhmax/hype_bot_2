import sys, subprocess, select, os

# Ensure we force pseudo-tty
p = subprocess.Popen(["ssh", "-tt", "-o", "StrictHostKeyChecking=no", "deploy@37.27.16.156", "cd ~/hype_bot_2/trading_bot_1 && docker compose logs bot --tail=2000 | grep -E 'live_engine|handlers|telegram_bot|risk_manager|error|Exception|ПРОПУСК' | tail -100"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

import time
time.sleep(2)
p.stdin.write(b'123\n')
p.stdin.flush()
time.sleep(10)
out, err = p.communicate()
print(out.decode(errors='ignore'))
