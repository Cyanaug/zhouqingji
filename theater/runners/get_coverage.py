import subprocess
import os

os.environ["PYTHONUTF8"] = "1"
output = subprocess.check_output(['python', 'runner.py', 'coverage'])
with open("coverage.txt", "w", encoding="utf-8") as f:
    f.write(output.decode('utf-8', errors='ignore'))
