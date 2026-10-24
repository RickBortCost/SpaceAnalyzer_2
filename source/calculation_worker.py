"""Isolated computation process. It never imports the acquisition/HTTP application."""
import heapq
import itertools
import json
import sys
from formula_engine import Plan, Engine
from calculation_history import calculate_recording

def live():
    engine = None; pending = []; seq = 0; high = None
    for line in sys.stdin:
        try:
            message = json.loads(line); result = []
            if message['op'] == 'configure':
                engine = Engine(Plan(message['formulas'], 'live', protocol=message['protocol']))
                pending = []; high = None; seq = 0
            elif message['op'] in ('batch', 'flush') and engine is not None:
                for t, rows in message.get('events', []):
                    seq += 1; heapq.heappush(pending, (t, seq, rows)); high = t if high is None else max(t, high)
                watermark = float('inf') if message['op'] == 'flush' else (high - 20 if high is not None else float('-inf'))
                ready = []
                while pending and pending[0][0] <= watermark:
                    t, _, rows = heapq.heappop(pending); ready.append((t, rows))
                for t, grouped in itertools.groupby(ready, key=lambda p: p[0]):
                    result.extend(engine.process(t, [row for _, rows in grouped for row in rows]))
                if len(pending) > 4096: raise ValueError('Buffer temporale calcoli pieno')
            print(json.dumps({'ok': True, 'rows': result, 'late_samples': engine.late_samples if engine else 0}, allow_nan=False), flush=True)
        except Exception as exc:
            print(json.dumps({'ok': False, 'error': str(exc) or type(exc).__name__}), flush=True)

if __name__ == '__main__':
    # Windows redirected pipes otherwise inherit a locale code page: preserve
    # Unicode signal names, units and paths independently of the PC language.
    sys.stdin.reconfigure(encoding='utf-8')
    sys.stdout.reconfigure(encoding='utf-8')
    if '--live' in sys.argv: live()
    else:
        try:
            job = json.load(sys.stdin)
            if '--preview' in sys.argv:
                from calculation_service import CalculationService
                result = {'rows': CalculationService.preview_live(job['formulas'],job['nid'],job['points'],job.get('start'),job.get('end'))}
            else: result = {'metadata': calculate_recording(job)}
            print(json.dumps({'ok': True, **result}, allow_nan=False))
        except Exception as exc: print(json.dumps({'ok': False, 'error': str(exc) or type(exc).__name__}))
