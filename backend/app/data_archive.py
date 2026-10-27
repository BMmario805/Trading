"""Content-addressed local market snapshots; no provider calls or execution."""
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]/'.runtime'/'research-data'


def archive_dataset(dataset,root=None):
    if not isinstance(dataset,dict): raise ValueError('Dataset required')
    rows=dataset.get('candles')
    if not isinstance(rows,list) or not 0<=len(rows)<=10000: raise ValueError('Bounded OHLCV required')
    clean=[]
    for row in rows:
        if not isinstance(row,dict): raise ValueError('OHLCV object required')
        item={key:row.get(key) for key in ('time','open','high','low','close','volume')}
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in item.values()):
            raise ValueError('Finite OHLCV values required')
        clean.append(item)
    payload=dict(version='market-snapshot-v1',symbol=dataset.get('symbol'),
        interval=dataset.get('interval'),source=dataset.get('source'),candles=clean)
    if any(not isinstance(payload[k],str) or not 1<=len(payload[k])<=300 for k in ('symbol','interval','source')):
        raise ValueError('Dataset identity required')
    raw=json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    digest=hashlib.sha256(raw).hexdigest()
    folder=Path(root or ROOT);folder.mkdir(parents=True,exist_ok=True)
    destination=folder/(digest+'.json')
    if destination.exists():
        if hashlib.sha256(destination.read_bytes()).hexdigest()!=digest:
            raise ValueError('Immutable dataset snapshot was modified')
    else:
        fd,name=tempfile.mkstemp(dir=folder,prefix='.snapshot-')
        try:
            with os.fdopen(fd,'wb') as output:
                output.write(raw);output.flush();os.fsync(output.fileno())
            # Linking refuses replacement if another writer published the hash.
            try: os.link(name,destination)
            except FileExistsError:
                if destination.read_bytes()!=raw: raise ValueError('Dataset hash collision')
        finally:
            Path(name).unlink(missing_ok=True)
    return dict(version=payload['version'],sha256=digest,artifact=destination.name,
                capturedAt=time.time(),bars=len(clean),source=payload['source'],
                historicalAvailabilityReconstructed=False)
