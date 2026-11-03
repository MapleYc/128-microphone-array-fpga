"""Atomic calibration configuration storage with validated disk readback."""
import sys as _store_sys
from pathlib import Path as _StorePath
_store_sys.path.insert(0,str(next(p/'host' for p in _StorePath(__file__).resolve().parents if (p/'host'/'structured_store.py').is_file())))
import structured_store as store
import json
from pathlib import Path
from uuid import uuid4


def _replace_bytes(path,payload):
    temp=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    temp.write_bytes(payload);temp.replace(path)


def _read_configuration(path):
    return store.loads(path.read_text(encoding='utf-8-sig'))


def save_and_readback(path,value,validate,previous_copy=None):
    """Return only the validated actual file; a failed readback restores old bytes.

    The caller publishes its in-memory calibration only after this returns.
    A rejected first configuration is retained under a distinct evidence name.
    """
    validate(value)
    # JSON arrays preserve tuple/list content but not Python container identity.
    # Compare the actual readback to the exact representation we serialized.
    expected=json.loads(json.dumps(value,ensure_ascii=False,allow_nan=False))
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    previous=path.read_bytes() if path.is_file() else None
    if previous_copy is not None and previous is not None:
        Path(previous_copy).write_bytes(previous)
    payload=store.dumps(expected,indent=2,ensure_ascii=False,allow_nan=False).encode('utf-8')
    try:
        _replace_bytes(path,payload)
        stored=_read_configuration(path)
        validate(stored)
        canonical=lambda item:json.dumps(item,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))
        if canonical(stored)!=canonical(expected):raise ValueError('保存后的校准配置与本次观测不一致，未应用')
    except (ValueError,OSError,TypeError):
        if previous is not None:_replace_bytes(path,previous)
        elif path.is_file():path.replace(path.with_name(path.name+'.rejected-'+uuid4().hex+'.yaml'))
        raise
    return stored
