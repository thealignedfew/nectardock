"""On-demand, account-scoped Claude usage snapshots; no credential values are emitted."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import tempfile


CACHE = Path('D:/NectarDockExample/Session-Capsules/Usage-Last-Known.json')
METERS = ('five_hour', 'seven_day', 'fable')


def _time(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None
        return parsed if parsed and parsed.tzinfo else None
    except (AttributeError, ValueError):
        return None


def _meter(percent, reset, observed):
    try:
        percent = round(float(percent), 1) if percent is not None else None
    except (TypeError, ValueError):
        percent = None
    when = _time(reset)
    remaining = None
    if when is not None:
        minutes = max(0, int((when - observed).total_seconds() // 60))
        remaining = f'{minutes // 60:02d}:{minutes % 60:02d}'
    return {'used_percent': percent, 'resets_at': reset if when else None,
            'remaining_hhmm': remaining}


def normalize(color, email, raw, observed_at):
    observed = observed_at if isinstance(observed_at, dt.datetime) else _time(observed_at)
    if observed is None:
        observed = dt.datetime.now(dt.timezone.utc)
    observed = observed.astimezone(dt.timezone.utc)
    five = raw.get('five_hour') or {}
    seven = raw.get('seven_day') or {}
    limits = raw.get('limits') or []
    fable = next((item for item in limits if isinstance(item, dict)
                  and item.get('kind') == 'weekly_scoped'
                  and str((item.get('scope') or {}).get('model', {}).get('display_name', '')).casefold() == 'fable'), {})
    return {'color': color, 'email': email, 'scope': 'account, not conversation',
            'observed_at': observed.isoformat(),
            'five_hour': _meter(five.get('utilization'), five.get('resets_at'), observed),
            'seven_day': _meter(seven.get('utilization'), seven.get('resets_at'), observed),
            'fable': _meter(fable.get('percent'), fable.get('resets_at'), observed)}


def sample_one(color, account):
    import switcher
    switcher.auth_check(color)
    adapter = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'claude-usage-mcp' / 'server.py'
    if not adapter.is_file():
        raise RuntimeError('Installed Claude usage adapter is unavailable')
    old = os.environ.get('CLAUDE_CONFIG_DIR')
    try:
        os.environ['CLAUDE_CONFIG_DIR'] = account['home']
        spec = importlib.util.spec_from_file_location('switchboard_claude_usage_adapter', adapter)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        raw = module.fetch_raw()
    finally:
        if old is None:
            os.environ.pop('CLAUDE_CONFIG_DIR', None)
        else:
            os.environ['CLAUDE_CONFIG_DIR'] = old
    if not isinstance(raw, dict):
        raise RuntimeError('Usage adapter returned no structured snapshot')
    if raw.get('error'):
        raise RuntimeError(f"Usage endpoint unavailable: {raw['error']}")
    return normalize(color, account['email'], raw, dt.datetime.now(dt.timezone.utc))


def _cache_row(row, color, account):
    """Keep only account-matched, normalized meter fields; never persist adapter data."""
    if not isinstance(row, dict) or row.get('color') != color or \
            str(row.get('email', '')).casefold() != account['email'].casefold():
        return None
    observed = _time(row.get('observed_at'))
    if observed is None:
        return None
    meters = {}
    for name in METERS:
        value = row.get(name)
        if not isinstance(value, dict):
            return None
        meters[name] = _meter(value.get('used_percent'), value.get('resets_at'), observed)
    return {'color': color, 'email': account['email'], 'scope': 'account, not conversation',
            'observed_at': observed.astimezone(dt.timezone.utc).isoformat(), **meters}


def _load_cache(path, accounts):
    if not path.is_file():
        return {}, None
    try:
        stored = json.loads(path.read_text(encoding='utf-8'))
        if stored.get('schema') != 1 or not isinstance(stored.get('accounts'), dict):
            raise ValueError('Unsupported usage cache schema')
        rows = {color: valid for color, account in accounts.items()
                if (valid := _cache_row(stored['accounts'].get(color), color, account)) is not None}
        return rows, None
    except (OSError, ValueError, TypeError, AttributeError):
        return {}, 'Previous usage cache could not be read; stale readings unavailable.'


def _save_cache(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(prefix='.usage-', suffix='.json', dir=path.parent)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump({'schema': 1, 'accounts': rows}, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def collect(colors, accounts, cache_path=CACHE):
    cache, warning = _load_cache(cache_path, accounts)
    rows, errors = [], {}
    updated = False
    for color in colors:
        try:
            fresh = sample_one(color, accounts[color])
            valid = _cache_row(fresh, color, accounts[color])
            if valid is None:
                raise RuntimeError('Usage adapter returned an invalid account snapshot')
            cache[color] = valid
            rows.append({**valid, 'stale': False})
            updated = True
        except Exception as exc:
            errors[color] = str(exc)
            if color in cache:
                rows.append({**cache[color], 'stale': True})
    if updated:
        try:
            _save_cache(cache_path, cache)
        except OSError:
            warning = 'Fresh usage was shown, but the last-known cache could not be saved.'
    result = {'state': 'USAGE_SNAPSHOT' if not errors else 'PARTIAL',
              'scope': 'Account-level Claude usage; not per conversation',
              'accounts': rows, 'errors': errors}
    if warning:
        result['warning'] = warning
    return result


def main():
    import switcher
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--account', action='append', choices=sorted(switcher.ACCOUNTS))
    args = parser.parse_args()
    print(json.dumps(collect(args.account or switcher.ACCOUNTS, switcher.ACCOUNTS),
                     indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
