"""Local display preferences only. No account, permission or safety policy switches."""
import json
import math
import os
from pathlib import Path
import tempfile

DEFAULTS = {'usage_threshold': 95, 'auto_fit': True, 'show_details': True,
            'usage_sort': 'fable', 'usage_descending': False}
PREFERENCES_PATH = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'TheAlignedFew' / 'SessionSwitch' / 'preferences.json'


def validate_preferences(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError('Only supported display preferences may be saved')
    result = dict(DEFAULTS, **values)
    threshold = result['usage_threshold']
    if type(threshold) not in (int, float) or not math.isfinite(threshold) or not 0 <= threshold <= 100:
        raise ValueError('Usage threshold must be a number from 0 to 100')
    for name in ('auto_fit', 'show_details', 'usage_descending'):
        if type(result[name]) is not bool:
            raise ValueError(name + ' must be true or false')
    if result['usage_sort'] not in ('five_hour', 'seven_day', 'fable'):
        raise ValueError('Choose a supported usage bucket')
    return result


def load_preferences(path=PREFERENCES_PATH):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('schema_version') != 1:
            raise ValueError('Unsupported preferences schema')
        return validate_preferences(data['display']), None
    except FileNotFoundError:
        return dict(DEFAULTS), None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return dict(DEFAULTS), f'Display preferences not loaded: {exc}. Original file retained.'


def save_preferences(values, path=PREFERENCES_PATH):
    values = validate_preferences(values)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='preferences-', suffix='.tmp', delete=False) as stream:
            staged = Path(stream.name)
            json.dump({'schema_version': 1, 'display': values}, stream, indent=2, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(staged, path)
    finally:
        if staged is not None and staged.exists():
            staged.unlink()  # Only this call's own temporary file.
