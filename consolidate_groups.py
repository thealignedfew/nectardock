"""Reviewed VAT -> BI workspace-only consolidation. No history or account writes.

Preserve an exact recovery bundle before publication. On any partial failure leave
the shared pending marker for reconciliation, never overwrite newer state to undo.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import uuid

import switcher as s
from reconcile_home import no_pending


def combined_workspace(doc):
    paths = [s.norm(p.get('path', '')).rstrip('\\/') for p in doc.get('folders', [])]
    expected = [s.norm(p).rstrip('\\/') for p in s.group_projects('BI')]
    s.require(paths in (expected[:1], expected), 'Unexpected BI workspace roots; review required')
    result = copy.deepcopy(doc)
    result['folders'] = [{'name': p.name, 'path': str(p)} for p in s.group_projects('BI')]
    return result


def rerouted_record(doc, workspace, observed_at):
    result = copy.deepcopy(doc)
    result.update(workspace_path=str(workspace), updated_utc=observed_at)
    return result


def consolidate(apply=False):
    h, maps = s.dependencies()
    with s.mutexes([]):
        no_pending()
        view = maps.load_verified(s.INDEX, s.MAP_HISTORY)
        rows = s.selected(view, 'BI')
        s.clear_runtime(h, rows)
        operations = []
        retired = []
        retired_profiles = {}
        targets = {}
        initial_hashes = {str(s.INDEX): view['index_sha256']}
        for color, account in s.ACCOUNTS.items():
            root = Path(account.get('workspace_root', s.BASE / 'workspaces'))
            target = s.plain(root / ('BI-' + color + '.code-workspace'))
            targets[s.norm(account['home'])] = target
            if target.exists():
                raw = target.read_bytes()
                initial_hashes[str(target)] = hashlib.sha256(raw).hexdigest()
                doc = json.loads(raw)
                result = combined_workspace(doc)
                if result != doc:
                    operations.append((target, result))
            old = s.plain(root / ('VAT-' + color + '.code-workspace'))
            if old.exists():
                raw = old.read_bytes()
                initial_hashes[str(old)] = hashlib.sha256(raw).hexdigest()
                doc = json.loads(raw)
                s.require([s.norm(p.get('path', '')) for p in doc.get('folders', [])] ==
                          [s.norm(s.PROJECTS / 'Compliance')], 'Unexpected VAT workspace roots')
                tabs = s.restored_workspace_tabs(account['user_data'], old)
                s.require(not tabs, 'VAT has saved tabs; preserve and adopt them before retiring workspace')
                retired.append(old)
                retired_profiles[old] = account['user_data']
        at = h.now()
        changed_records = []
        for row in rows:
            target = targets[s.norm(row['config_home'])]
            s.require(target.exists(), 'Create and review BI workspace before rerouting records')
            if s.norm(row['workspace_path']) != s.norm(target):
                path = Path(view['index']['entries'][row['uuid']]['path'])
                initial_hashes[str(path)] = view['index']['entries'][row['uuid']]['sha256']
                operations.append((path, rerouted_record(row, target, at)))
                changed_records.append(row['uuid'])
        plan = {'state': 'WORKSPACE_CONSOLIDATION_PLAN', 'sessions': len(rows),
                'routing_records': changed_records, 'update_paths': [str(p) for p, _ in operations],
                'archive_workspace_paths': [str(p) for p in retired],
                'histories_moved': False, 'accounts_changed': False}
        if not apply or not (operations or retired):
            return plan
        run = s.BASE / 'Workspace-Consolidation-Runs' / (at.replace(':', '').replace('.', '') + '-' + uuid.uuid4().hex[:8])
        run.mkdir(parents=True, exist_ok=False)
        # Exact targets, byte backups, and fingerprints are retained before any writes.
        paths = [s.INDEX] + [p for p, _ in operations] + retired
        before = {}
        for i, path in enumerate(paths):
            s.plain(path)
            sha = initial_hashes[str(path)]
            s.require(s.digest(path) == sha, 'Workspace metadata changed since initial review')
            backup = run / (str(i) + '.before')
            s.copy_exclusive(path, backup, sha)
            before[str(path)] = {'sha256': sha, 'backup': str(backup)}
        histories = {row['primary_history_path']: s.digest(row['primary_history_path']) for row in rows}
        h.write_new(run / 'manifest.json', dict(plan, before=before, histories=histories, at=at), readonly=True)
        maps.assert_unchanged(view)
        s.clear_runtime(h, rows)
        for path, sha in initial_hashes.items():
            s.require(s.digest(path) == sha, 'Workspace metadata changed during preservation')
        for path in retired:
            s.require(not s.restored_workspace_tabs(retired_profiles[path], path),
                      'VAT has new saved tabs; review before publication')
        pending = s.RUNS / 'PENDING.json'
        h.write_new(pending, {'run': str(run), 'state': 'WORKSPACE_CONSOLIDATION_IN_PROGRESS'})
        for path, doc in operations:
            s.require(s.digest(path) == before[str(path)]['sha256'], 'Concurrent workspace metadata update')
            h.publish(path, doc)
            s.require(json.loads(path.read_text(encoding='utf-8')) == doc, 'Metadata readback mismatch')
        index = copy.deepcopy(view['index'])
        for sid in changed_records:
            index['entries'][sid]['sha256'] = s.digest(index['entries'][sid]['path'])
        if changed_records:
            s.require(s.digest(s.INDEX) == view['index_sha256'], 'Concurrent index update')
            index['updated_utc'] = at
            h.publish(s.INDEX, index)
        maps.load_verified(s.INDEX, s.MAP_HISTORY)
        for path in retired:
            s.require(s.digest(path) == before[str(path)]['sha256'], 'VAT workspace changed before archival')
            s.require(not s.restored_workspace_tabs(retired_profiles[path], path),
                      'VAT has new saved tabs; review before archival')
            path.rename(run / (path.name + '.retired'))
        s.require(all(s.digest(path) == sha for path, sha in histories.items()), 'History changed during consolidation')
        maps.refresh(s.INDEX, s.MAP, s.MAP_HISTORY)
        receipt = dict(plan, state='WORKSPACE_CONSOLIDATION_SAVED_NOT_GUI_VERIFIED',
                       run=str(run), preserved_history_hashes=len(histories), runtime_adoption='NOT_CLAIMED')
        h.write_new(run / 'receipt.json', receipt, readonly=True)
        pending.unlink()  # Only this operation's marker, after complete readback.
        return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(consolidate(args.apply), indent=2))
