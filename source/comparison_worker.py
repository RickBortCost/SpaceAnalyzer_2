"""Worker entry point: imports no acquisition or Flask services."""
import json
import os
from pathlib import Path
import sys

from recording_compare import atomic_json, build_comparison

if __name__ == '__main__':
    try:
        if os.name == 'nt':
            import ctypes
            ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
        else:
            os.nice(5)
        job = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
        result = {'ok': True, 'recording': build_comparison(job, lambda state: atomic_json(job['progress_path'], state))}
    except Exception as exc:
        result = {'ok': False, 'error': str(exc) or type(exc).__name__}
    sys.stdout.write(json.dumps(result, ensure_ascii=True, allow_nan=False))
