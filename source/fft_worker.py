import json
import sys
from fft_engine import analyze
if __name__=='__main__':
    try:
        data=json.load(sys.stdin)
        rows=(dict(node_id=data['nodeid'],timestamp_ms=p[0],value_numeric=p[1],quality=p[2],ads_filetime=p[3]) for p in data.pop('points'))
        output=dict(ok=True,spectrum=analyze(rows,**data))
        text=json.dumps(output,allow_nan=False)
    except Exception as exc:text=json.dumps(dict(ok=False,error=str(exc) or type(exc).__name__))
    sys.stdout.write(text)
