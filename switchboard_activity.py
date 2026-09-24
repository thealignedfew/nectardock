"""Local switchboard activity capture; never records process environments."""

from datetime import datetime
from pathlib import Path
import os
import subprocess
import threading
import time
import uuid


PROGRESS_PREFIX = 'SWITCHBOARD_PROGRESS '


class ActivityRecorder:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = None

    def record(self, message):
        if self.path is None:
            self.directory.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')
            self.path = self.directory / f'switchboard-ui-{stamp}-{os.getpid()}-{uuid.uuid4().hex[:8]}.log'
        stamp = datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')
        line = f'[{stamp}] {str(message).replace(chr(10), " ").replace(chr(13), " ")}'
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(line + '\n')
        return line


def run_json_command(argv, on_progress):
    """Stream approved progress markers while keeping final JSON separate."""
    started = time.monotonic()
    process = subprocess.Popen([str(part) for part in argv], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, encoding='utf-8', errors='replace', bufsize=1,
        creationflags=0x08000000)
    output = []
    reader = threading.Thread(target=lambda: output.append(process.stdout.read()), daemon=True)
    reader.start()
    for line in process.stderr:
        if line.startswith(PROGRESS_PREFIX):
            on_progress(line[len(PROGRESS_PREFIX):].strip()[:500])
    exit_code = process.wait()
    reader.join()
    process.stdout.close()
    process.stderr.close()
    elapsed = time.monotonic() - started
    try:
        import json
        result = json.loads(''.join(output))
    except (ValueError, TypeError):
        result = {'state': 'HELD', 'error': f'No structured result (exit {exit_code})'}
    return result, exit_code, elapsed
