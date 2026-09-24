"""Reviewed, allowlisted settings and skill regularization for isolated Claude homes."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import uuid


BASE = Path(__file__).resolve().parent
POLICY = BASE / 'regularization_policy.json'
RUNS = BASE / 'Regularize-Runs'
POLICY_SCHEMA = 'claude-regularization-policy/v1'
MANIFEST_SCHEMA = 'claude-regularization-manifest/v1'
SKILL_NAME = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')


class Hold(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise Hold(message)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def digest_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise Hold(f'Cannot read valid JSON: {path}: {exc}') from exc


def inside(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path.resolve()), str(root.resolve()))) == str(root.resolve())
    except ValueError:
        return False


def load_policy(path: Path = POLICY):
    path = Path(path).resolve()
    policy = read_json(path)
    require(policy.get('schema') == POLICY_SCHEMA, 'Unsupported regularization policy schema')
    require(isinstance(policy.get('accounts'), dict) and policy['accounts'], 'Policy accounts are missing')
    require(isinstance(policy.get('shared_settings'), dict), 'Policy shared settings are missing')
    require(isinstance(policy.get('protected_settings'), list), 'Policy protected settings are missing')
    shared = set(policy['shared_settings'])
    protected = set(policy['protected_settings'])
    require(not shared.intersection(protected), 'Shared and protected settings overlap')
    skills_root = Path(policy.get('skills_root', '')).resolve()
    require(skills_root.is_dir(), f'Canonical skills root is unavailable: {skills_root}')
    require(isinstance(policy.get('shared_skills'), list), 'Policy shared skills are missing')
    for skill in policy['shared_skills']:
        require(isinstance(skill, str) and SKILL_NAME.fullmatch(skill), f'Invalid shared skill name: {skill!r}')
        source = (skills_root / skill).resolve()
        require(inside(source, skills_root) and source.is_dir(), f'Canonical skill is unavailable: {skill}')
        require((source / 'SKILL.md').is_file(), f'Canonical skill lacks SKILL.md: {skill}')
    for color, row in policy['accounts'].items():
        require(isinstance(color, str) and color, 'Account color is invalid')
        require(isinstance(row, dict) and row.get('home'), f'Account home is missing: {color}')
        require(Path(row['home']).is_absolute(), f'Account home must be absolute: {color}')
    return policy, path


def choose_accounts(policy, colors):
    selected = list(colors or policy['accounts'])
    require(selected, 'Select at least one account')
    for color in selected:
        require(color in policy['accounts'], f'Unknown account: {color}')
    return selected


def skill_files(policy):
    root = Path(policy['skills_root']).resolve()
    rows = []
    for skill in policy['shared_skills']:
        skill_root = (root / skill).resolve()
        for path in sorted(skill_root.rglob('*'), key=lambda item: str(item).lower()):
            if not path.is_file():
                continue
            relative = path.relative_to(skill_root)
            if any(part in {'.git', '.trash', '__pycache__'} for part in relative.parts):
                continue
            if path.suffix.lower() == '.pyc' or path.name == '.DS_Store':
                continue
            require(inside(path, skill_root), f'Canonical skill path escaped its root: {path}')
            rows.append((skill, relative, path))
    return rows


def settings_scope(before, after, policy):
    shared = set(policy['shared_settings'])
    for key in set(before).union(after) - shared:
        require(before.get(key) == after.get(key), f'Protected/account setting would change: {key}')
    for key in policy['protected_settings']:
        require(before.get(key) == after.get(key), f'Protected/account setting would change: {key}')


def audit(colors=None, policy_path: Path = POLICY, runs_root: Path = RUNS):
    policy, policy_file = load_policy(Path(policy_path))
    selected = choose_accounts(policy, colors)
    canonical = skill_files(policy)
    accounts = []
    for color in selected:
        home = Path(policy['accounts'][color]['home']).resolve()
        settings_path = home / 'settings.json'
        settings = read_json(settings_path)
        drift = sorted(key for key, value in policy['shared_settings'].items() if settings.get(key) != value)
        changed = []
        missing = []
        for skill, relative, source in canonical:
            destination = home / 'skills' / skill / relative
            label = f'{skill}/{relative.as_posix()}'
            if not destination.is_file():
                missing.append(label)
                changed.append(label)
            elif digest(source) != digest(destination):
                changed.append(label)
        accounts.append({
            'account': color,
            'home': str(home),
            'settings': {
                'path': str(settings_path),
                'sha256': digest(settings_path),
                'drift_keys': drift,
                'protected_keys': sorted(key for key in policy['protected_settings'] if key in settings),
                'state': 'CURRENT' if not drift else 'DRIFT',
            },
            'skills': {
                'canonical_root': str(Path(policy['skills_root']).resolve()),
                'changed_files': changed,
                'missing_files': missing,
                'state': 'CURRENT' if not changed else 'DRIFT',
            },
        })
    pending = Path(runs_root).resolve() / 'PENDING.json'
    return {
        'state': 'REGULARIZATION_AUDIT',
        'policy': str(policy_file),
        'policy_sha256': digest(policy_file),
        'accounts': accounts,
        'pending_recovery': str(pending) if pending.exists() else None,
        'runtime_adoption': 'NOT_ASSESSED',
    }


def store_object(directory: Path, raw: bytes, suffix: str) -> Path:
    sha = digest_bytes(raw)
    path = directory / f'{sha}{suffix}'
    if path.exists():
        require(digest(path) == sha, f'Object hash collision: {path}')
    else:
        with path.open('xb') as stream:
            stream.write(raw)
    return path


def new_run(runs_root: Path) -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H%M%S%f+0000')
    run = runs_root / f'{stamp}-{uuid.uuid4().hex[:8]}'
    run.mkdir(parents=True, exist_ok=False)
    (run / 'objects').mkdir()
    return run


def prepare(kind: str, colors=None, policy_path: Path = POLICY, runs_root: Path = RUNS):
    require(kind in {'settings', 'skills'}, f'Unsupported regularization kind: {kind}')
    policy, policy_file = load_policy(Path(policy_path))
    selected = choose_accounts(policy, colors)
    runs_root = Path(runs_root).resolve()
    runs_root.mkdir(parents=True, exist_ok=True)
    pending = runs_root / 'PENDING.json'
    require(not pending.exists(), f'Regularization recovery is required before preparing again: {pending}')
    run = new_run(runs_root)
    objects = run / 'objects'
    operations = []
    if kind == 'settings':
        for color in selected:
            home = Path(policy['accounts'][color]['home']).resolve()
            destination = home / 'settings.json'
            before_raw = destination.read_bytes()
            before = read_json(destination)
            after = copy.deepcopy(before)
            for key, value in policy['shared_settings'].items():
                after[key] = copy.deepcopy(value)
            settings_scope(before, after, policy)
            if before == after:
                continue
            after_raw = json_bytes(after)
            artifact = store_object(objects, after_raw, '.json')
            operations.append({
                'kind': 'settings', 'account': color, 'relative': 'settings.json',
                'destination': str(destination), 'resolved_destination': str(destination.resolve()),
                'before_sha256': digest_bytes(before_raw), 'after_sha256': digest_bytes(after_raw),
                'object': str(artifact), 'shared_keys': sorted(policy['shared_settings']),
            })
    else:
        canonical = skill_files(policy)
        for color in selected:
            home = Path(policy['accounts'][color]['home']).resolve()
            for skill, relative, source in canonical:
                destination = home / 'skills' / skill / relative
                source_raw = source.read_bytes()
                source_sha = digest_bytes(source_raw)
                before_sha = digest(destination) if destination.is_file() else None
                if before_sha == source_sha:
                    continue
                artifact = store_object(objects, source_raw, '.blob')
                operations.append({
                    'kind': 'skill', 'account': color, 'skill': skill,
                    'relative': f'{skill}/{relative.as_posix()}',
                    'source': str(source), 'source_sha256': source_sha,
                    'destination': str(destination), 'resolved_destination': str(destination.resolve()),
                    'before_sha256': before_sha, 'after_sha256': source_sha, 'object': str(artifact),
                })
    manifest = {
        'schema': MANIFEST_SCHEMA,
        'state': 'PREPARED_REVIEW_REQUIRED',
        'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'kind': kind,
        'accounts': selected,
        'policy': str(policy_file),
        'policy_sha256': digest(policy_file),
        'run': str(run),
        'operations': operations,
        'protected_settings': policy['protected_settings'],
        'meaning': 'Reviewed disk changes only; running tasks may retain cached instructions.',
    }
    manifest_path = run / 'manifest.json'
    manifest_path.write_bytes(json_bytes(manifest))
    review_path = run / 'REVIEW.txt'
    lines = [
        f'Claude {kind} regularization prepared',
        f'Accounts: {", ".join(selected)}',
        f'Changed files: {len(operations)}',
        'No files have been changed.',
        'Only policy-allowlisted shared settings or canonical shared-skill files are included.',
        'Auth, credentials, permissions, hooks, model choices, account paths and target-only resources are preserved.',
        'Apply rechecks the policy, source, target and reviewed object hashes. A failed apply leaves PENDING.json for recovery.',
        '',
    ]
    lines.extend(f"{op['account']} | {op['relative']} | {op['before_sha256'] or 'MISSING'} -> {op['after_sha256']}" for op in operations)
    review_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {
        'state': 'PREPARED_REVIEW_REQUIRED', 'kind': kind, 'accounts': selected,
        'changed_files': len(operations), 'manifest': str(manifest_path),
        'sha256': digest(manifest_path), 'review': str(review_path),
    }


def validate_operation(op, policy, run: Path):
    color = op.get('account')
    require(color in policy['accounts'], f'Operation account is not allowlisted: {color}')
    home = Path(policy['accounts'][color]['home']).resolve()
    destination = Path(op['destination'])
    require(str(destination.resolve()) == op['resolved_destination'], f'Destination path rebound: {destination}')
    artifact = Path(op['object']).resolve()
    require(inside(artifact, run / 'objects'), f'Reviewed object escaped the run: {artifact}')
    if op.get('kind') == 'settings':
        require(destination.resolve() == (home / 'settings.json').resolve(), 'Settings destination is not allowlisted')
    elif op.get('kind') == 'skill':
        skill = op.get('skill')
        require(skill in policy['shared_skills'], f'Skill is not allowlisted: {skill}')
        source = Path(op['source']).resolve()
        source_root = (Path(policy['skills_root']).resolve() / skill).resolve()
        require(inside(source, source_root), f'Skill source escaped its canonical root: {source}')
        require(inside(destination, home / 'skills' / skill), f'Skill destination escaped its account root: {destination}')
    else:
        raise Hold(f"Unknown operation kind: {op.get('kind')}")


def write_atomic(destination: Path, raw: bytes):
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f'.{destination.name}.regularize-{uuid.uuid4().hex}.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply(manifest_path: Path, manifest_sha256: str, policy_path: Path = POLICY, runs_root: Path = RUNS):
    policy, policy_file = load_policy(Path(policy_path))
    runs_root = Path(runs_root).resolve()
    manifest_path = Path(manifest_path).resolve()
    require(inside(manifest_path, runs_root), 'Manifest is outside the regularization run root')
    require(manifest_path.name == 'manifest.json', 'Unexpected manifest filename')
    require(digest(manifest_path) == manifest_sha256.lower(), 'Manifest hash changed after review')
    manifest = read_json(manifest_path)
    require(manifest.get('schema') == MANIFEST_SCHEMA, 'Unsupported regularization manifest schema')
    require(manifest.get('state') == 'PREPARED_REVIEW_REQUIRED', 'Manifest is not review-ready')
    require(manifest.get('policy_sha256') == digest(policy_file), 'Regularization policy changed; prepare again')
    require(Path(manifest.get('run', '')).resolve() == manifest_path.parent, 'Manifest run path mismatch')
    pending = runs_root / 'PENDING.json'
    require(not pending.exists(), f'Regularization recovery is required before apply: {pending}')
    pending.parent.mkdir(parents=True, exist_ok=True)
    with pending.open('x', encoding='utf-8') as stream:
        json.dump({'manifest': str(manifest_path), 'sha256': manifest_sha256,
                   'started_at': dt.datetime.now(dt.timezone.utc).isoformat()}, stream, indent=2)
        stream.write('\n')
    run = manifest_path.parent
    operations = manifest.get('operations')
    require(isinstance(operations, list), 'Manifest operations are invalid')
    for op in operations:
        validate_operation(op, policy, run)
        destination = Path(op['destination'])
        current = digest(destination) if destination.is_file() else None
        require(current == op.get('before_sha256'), f'Target changed after review: {destination}')
        artifact = Path(op['object'])
        require(digest(artifact) == op['after_sha256'], f'Reviewed object changed: {artifact}')
        if op['kind'] == 'settings':
            settings_scope(read_json(destination), read_json(artifact), policy)
        if op['kind'] == 'skill':
            source = Path(op['source'])
            require(source.is_file() and digest(source) == op['source_sha256'], f'Source changed after review: {source}')
    completed = []
    journal = run / 'JOURNAL.jsonl'
    for number, op in enumerate(operations):
        destination = Path(op['destination'])
        backup = None
        if destination.is_file():
            backup = run / 'backups' / op['account'] / op['relative']
            backup.parent.mkdir(parents=True, exist_ok=True)
            with backup.open('xb') as stream:
                stream.write(destination.read_bytes())
            require(digest(backup) == op['before_sha256'], f'Backup verification failed: {backup}')
        raw = Path(op['object']).read_bytes()
        write_atomic(destination, raw)
        require(digest(destination) == op['after_sha256'], f'Destination verification failed: {destination}')
        result = dict(op)
        result['backup'] = str(backup) if backup else None
        result['disk_verified'] = True
        completed.append(result)
        with journal.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'number': number, 'destination': str(destination),
                                     'after_sha256': op['after_sha256'], 'state': 'VERIFIED'}) + '\n')
    receipt = {
        'schema': 'claude-regularization-result/v1',
        'state': 'REGULARIZATION_COMPLETE',
        'completed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'manifest': str(manifest_path), 'manifest_sha256': manifest_sha256.lower(),
        'kind': manifest['kind'], 'accounts': manifest['accounts'],
        'changed_files': len(completed), 'operations': completed,
        'runtime_adoption': 'NOT_CLAIMED',
    }
    receipt_path = run / 'RESULT.json'
    write_atomic(receipt_path, json_bytes(receipt))
    pending.unlink()
    return {
        'state': 'REGULARIZATION_COMPLETE', 'kind': manifest['kind'],
        'accounts': manifest['accounts'], 'changed_files': len(completed),
        'receipt': str(receipt_path), 'receipt_sha256': digest(receipt_path),
        'runtime_adoption': 'NOT_CLAIMED',
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description='Reviewed shared settings/skills regularization; never copies auth.')
    parser.add_argument('--policy', type=Path, default=POLICY)
    parser.add_argument('--runs-root', type=Path, default=RUNS)
    sub = parser.add_subparsers(dest='command', required=True)
    audit_parser = sub.add_parser('audit')
    audit_parser.add_argument('--account', action='append')
    prepare_parser = sub.add_parser('prepare')
    prepare_parser.add_argument('--kind', choices=('settings', 'skills'), required=True)
    prepare_parser.add_argument('--account', action='append')
    apply_parser = sub.add_parser('apply')
    apply_parser.add_argument('--manifest', type=Path, required=True)
    apply_parser.add_argument('--sha256', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'audit':
            result = audit(args.account, args.policy, args.runs_root)
        elif args.command == 'prepare':
            result = prepare(args.kind, args.account, args.policy, args.runs_root)
        else:
            result = apply(args.manifest, args.sha256, args.policy, args.runs_root)
        print(json.dumps(result, indent=2))
        return 0
    except Hold as exc:
        print(json.dumps({'state': 'HELD', 'error': str(exc)}, indent=2))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
