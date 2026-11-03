"""Safe YAML file storage; protocol encoding and canonical digests stay separate."""
from pathlib import Path
import json
import yaml


def _normalise(value, allow_nan=True):
    # Keep the former file data model: string mapping keys and list sequences.
    # This is an in-memory conversion, never JSON written under another suffix.
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=allow_nan))


def loads(text):
    try:
        return _normalise(yaml.safe_load(text))
    except (yaml.YAMLError, TypeError, ValueError) as error:
        raise ValueError('Invalid YAML configuration: '+str(error)) from error


def dumps(value, *, allow_nan=True, sort_keys=False, ensure_ascii=False,
          indent=None, separators=None, explicit_start=False):
    try:
        value=_normalise(value, allow_nan=allow_nan)
        return yaml.safe_dump(value, allow_unicode=not ensure_ascii,
                              sort_keys=sort_keys, default_flow_style=False,
                              explicit_start=explicit_start)
    except (yaml.YAMLError, TypeError, ValueError) as error:
        raise ValueError('Cannot encode YAML metadata: '+str(error)) from error


def load_file(path):
    return loads(Path(path).read_text(encoding='utf-8-sig'))


def dump_file(path, value, **options):
    Path(path).write_text(dumps(value, **options), encoding='utf-8', newline='\n')


def load_stream(text):
    try:
        return [_normalise(value) for value in yaml.safe_load_all(text)]
    except (yaml.YAMLError, TypeError, ValueError) as error:
        raise ValueError('Invalid YAML metadata stream: '+str(error)) from error


def append_stream(handle, value, **options):
    handle.write(dumps(value, explicit_start=True, **options))
