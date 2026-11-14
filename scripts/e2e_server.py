"""Isolated browser-test server; temporary test database is removed on normal exit."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory(prefix='lawyerai-e2e-') as directory:
    env={**os.environ,'LAW_DATA_DIR':directory,'LAW_DB_PATH':str(Path(directory)/'office.sqlite3')}
    process=subprocess.Popen([sys.executable,'-m','uvicorn','backend.main:app','--host','127.0.0.1','--port','8001'],env=env,cwd=Path(__file__).resolve().parents[1])
    try:process.wait()
    except KeyboardInterrupt:pass
    finally:
        process.terminate()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:process.kill();process.wait()
