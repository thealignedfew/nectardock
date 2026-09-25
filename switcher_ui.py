"""Human-operated account switching. Merely opening this window changes nothing."""
import ctypes
import datetime as dt
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import switcher as engine
from switchboard_activity import ActivityRecorder, run_json_command
from switchboard_instance import InstanceGate
from switchboard_preferences import DEFAULTS, PREFERENCES_PATH, load_preferences, save_preferences, validate_preferences
from app_metadata import APP_NAME, APP_VERSION, PUBLISHER, RELEASE_CHANNEL
from survivor_dialog import SurvivorDialog
from survivor_review import conflict_uuid
from batch_dialog import BatchDialog


ACCOUNT_INKS = {'GREEN':'#78D6A0', 'YELLOW':'#FFE066', 'ORANGE':'#FFB278',
                'PURPLE':'#D9B3FF', 'BLUE':'#89BEFF'}
USAGE_BUCKETS = {'Fable':'fable', '5-hour':'five_hour', 'Weekly':'seven_day'}


def start_tracked_worker(jobs,worker):
    jobs[0]+=1
    try:
        threading.Thread(target=worker,daemon=True).start()
    except Exception:
        jobs[0]-=1
        raise


def usage_percent(row, bucket):
    value = (row.get(bucket) or {}).get('used_percent')
    return value if type(value) in (float, int) and math.isfinite(value) else None


def sort_usage_accounts(rows, bucket='fable', descending=False):
    if bucket not in USAGE_BUCKETS.values():
        raise ValueError('Unsupported usage bucket')
    known = [r for r in rows if usage_percent(r, bucket) is not None]
    missing = [r for r in rows if usage_percent(r, bucket) is None]
    return sorted(known, key=lambda r: usage_percent(r, bucket), reverse=descending) + missing


def usage_strikes(row, threshold=95):
    def exceeded(bucket):
        value = usage_percent(row, bucket)
        return value is not None and value > threshold
    plan = exceeded('seven_day')
    return {'plan':plan, **{key:plan or exceeded(key) for key in USAGE_BUCKETS.values()}}


def fit_geometry(width, height, work_area):
    left, top, right, bottom = work_area
    return (min(width, max(1, right-left-24)), min(height, max(1, bottom-top-64)), left+12, top+32)


def fit_window(window, width=None, height=None):
    """Fit to the current monitor work area, retaining scrollbars for excess content."""
    window.update_idletasks()
    area = (0, 0, window.winfo_screenwidth(), window.winfo_screenheight())
    handle = None
    if os.name == 'nt':
        from ctypes import wintypes
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('cbSize',wintypes.DWORD), ('rcMonitor',wintypes.RECT),
                        ('rcWork',wintypes.RECT), ('dwFlags',wintypes.DWORD)]
        user32 = ctypes.windll.user32
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
        handle = user32.GetAncestor(window.winfo_id(), 2)
        info = MonitorInfo(); info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(user32.MonitorFromWindow(handle, 2), ctypes.byref(info)):
            r = info.rcWork; area = (r.left,r.top,r.right,r.bottom)
    width, height, x, y = fit_geometry(width or window.winfo_reqwidth(), height or window.winfo_reqheight(), area)
    window.geometry(f'{width}x{height}')
    if handle:
        user32.SetWindowPos.argtypes = [wintypes.HWND,wintypes.HWND,ctypes.c_int,ctypes.c_int,
                                       ctypes.c_int,ctypes.c_int,wintypes.UINT]
        user32.SetWindowPos(handle, None, x, y, 0, 0, 0x15)  # no resize, z-order or focus change


def render_usage_report(report, data, preferences=None):
    prefs = validate_preferences(preferences or {})
    report.configure(state='normal')
    report.delete('1.0','end')
    for color, ink in ACCOUNT_INKS.items():
        report.tag_configure(color, foreground=ink)
    report.tag_configure('stale', background='#424548')
    report.tag_configure('exhausted', foreground='#FF7C7C', overstrike=True)
    for row in sort_usage_accounts(data.get('accounts', []), prefs['usage_sort'], prefs['usage_descending']):
        tags = [row['color']]
        stale = bool(row.get('stale'))
        if stale: tags.append('stale')
        strike = usage_strikes(row, prefs['usage_threshold'])
        status = 'STALE last successful reading' if stale else 'fresh reading'
        heading = f"{row['color']}  {row.get('email', 'identity unavailable')}  ·  {status}"
        if prefs['show_details']:
            heading += f" · observed {format_observed_local(row.get('observed_at'))} local"
        report.insert('end', heading+'\n', tuple(tags + (['exhausted'] if strike['plan'] else [])))
        for label, key in (('5-hour','five_hour'), ('Weekly','seven_day'), ('Fable','fable')):
            report.insert('end', f"  {label}: {format_usage_meter(row.get(key) or {}, stale)}\n",
                          tuple(tags + (['exhausted'] if strike[key] else [])))
        report.insert('end','\n')
    # Failures remain visible even when optional observation details are hidden.
    for line in usage_report_lines(data)[len(data.get('accounts', []))*5:]:
        report.insert('end', line+'\n')
    report.tag_raise('exhausted')
    report.configure(state='disabled')


TABLE_COLUMNS = (
    ('seat','Conversation',220), ('home','Current',78), ('state','Saved state',160),
    ('checkpoint','Checkpoint',102), ('modified','History modified (local)',170),
    ('version','Last recorded version',155), ('history','Registered history path',355),
    ('uuid','Original UUID',290),
)


def sort_table_rows(rows, column='modified', descending=True):
    columns = [name for name, _, _ in TABLE_COLUMNS]
    if column not in columns:
        raise ValueError('Unknown table column: ' + column)
    index = columns.index(column)
    available, missing = [], []
    for item in rows:
        value = str(item[1][index]).strip()
        (missing if not value or value.casefold() == 'unavailable' else available).append(item)
    def key(item):
        value = str(item[1][index])
        if column == 'version':
            return tuple(int(part) for part in re.findall(r'\d+', value)[:3])
        return value.casefold()
    return sorted(available, key=key, reverse=descending) + missing


@lru_cache(maxsize=512)
def last_recorded_version(path, size, mtime_ns):
    """Read a bounded tail; report only a top-level Claude Code version."""
    try:
        with Path(path).open('rb') as stream:
            start = max(0, size - 4 * 1024 * 1024)
            stream.seek(start)
            tail = stream.read(4 * 1024 * 1024)
        if start:
            tail = tail.partition(b'\n')[2]
        for raw in reversed(tail.splitlines()):
            try:
                record = json.loads(raw)
            except (UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(record, dict):
                version = record.get('version')
                if isinstance(version, str) and re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?', version):
                    return version
    except OSError:
        pass
    return 'unavailable'


def table_row_values(row, homes):
    """Show current on-disk history mtime, not a claimed last conversation turn."""
    history = row['primary_history_path']
    try:
        state = Path(history).stat()
        modified = dt.datetime.fromtimestamp(state.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        version = last_recorded_version(history, state.st_size, state.st_mtime_ns)
    except (OSError, OverflowError, ValueError):
        modified = 'unavailable'
        version = 'unavailable'
    home = homes.get(engine.norm(row['config_home']), homes.get(row['config_home'], 'DEFAULT'))
    checkpoint = row.get('checkpoint', {}).get('main_sha256', '')[:12]
    return (row['label'], home, row.get('status', 'UNAVAILABLE'), checkpoint,
            modified, version, history, row['uuid'])


def safe_to_close(main_busy, auxiliary_jobs):
    return not main_busy and auxiliary_jobs == 0


def needs_usage_login(error):
    return bool(re.search(r'\bhttp_401\b|Destination account does not match (?:GREEN|YELLOW|ORANGE|PURPLE)\b|Destination login is unavailable\b',
                          str(error)))


def login_guidance(color):
    account = engine.ACCOUNTS[color]
    method = (f"Choose Continue with Google using {account['email']}." if color in ('GREEN', 'YELLOW')
              else f"Choose Continue with email; {account['email']} is pre-populated by Claude Code.")
    return (f"If Claude's page shows a different account, click Switch account first. {method} "
            f"Choose the {account['plan'].capitalize()} account/plan before authorizing. "
            'The switchboard does not sign out other browser tabs or select Google for you.')


def format_usage_meter(value, stale=False, now=None):
    percent = value.get('used_percent')
    used = 'unavailable' if percent is None else f'{percent:g}%'
    if not stale:
        return f"{used} used; reset in {value.get('remaining_hhmm') or 'unavailable'}"
    try:
        reset = dt.datetime.fromisoformat(str(value.get('resets_at')).replace('Z', '+00:00'))
        if reset.tzinfo is None:
            raise ValueError('Reset lacks timezone')
        observed_now = now or dt.datetime.now(dt.timezone.utc)
        label = reset.astimezone().strftime('%a %b %d, %H:%M') + ' local'
        if reset <= observed_now:
            return f'{used} used; last reported reset on {label} (passed)'
        return f'{used} used; reset on {label}'
    except (TypeError, ValueError, OverflowError):
        return f'{used} used; reset time unavailable'


def usage_report_lines(data):
    lines = []
    for item in data.get('accounts', []):
        stale = bool(item.get('stale'))
        status = 'STALE last successful reading' if stale else 'fresh reading'
        lines.extend([
            f"{item['color']}  {item['email']}  ·  {status} · observed {format_observed_local(item.get('observed_at'))} (local)",
            f"  5-hour: {format_usage_meter(item['five_hour'], stale)}",
            f"  Weekly: {format_usage_meter(item['seven_day'], stale)}",
            f"  Fable: {format_usage_meter(item['fable'], stale)}",
            '',
        ])
    stale_colors = {item['color'] for item in data.get('accounts', []) if item.get('stale')}
    for name, error in data.get('errors', {}).items():
        lines.append(f'{name}: fresh check failed: {error}; stale reading above is not current' if name in stale_colors
                     else f'{name}: unavailable: {error}')
    if data.get('error'):
        lines.append('Snapshot unavailable: ' + str(data['error']))
    if data.get('warning'):
        lines.append('Cache warning: ' + str(data['warning']))
    return lines


def format_observed_local(value):
    try:
        observed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if observed.tzinfo is None:
            return 'unavailable'
        return observed.astimezone().strftime('%Y-%m-%d %H:%M')
    except (TypeError, ValueError, OverflowError):
        return 'unavailable'


def merge_usage_snapshot(previous, update, color=None):
    """Replace only the retried color; retain the other observed account rows."""
    if color is None:
        return update
    order = [row['color'] for row in previous.get('accounts', [])]
    if color not in order:
        order.append(color)
    rows = {row['color']: row for row in previous.get('accounts', [])}
    errors = dict(previous.get('errors', {}))
    rows.pop(color, None)
    errors.pop(color, None)
    replacement = next((row for row in update.get('accounts', []) if row.get('color') == color), None)
    if replacement is not None:
        rows[color] = replacement
    else:
        errors[color] = update.get('errors', {}).get(color) or update.get('error') or 'No account snapshot returned'
    merged = {'state': 'PARTIAL' if errors else 'USAGE_SNAPSHOT',
            'scope': previous.get('scope', update.get('scope')),
            'accounts': [rows[key] for key in order if key in rows],
            'errors': errors}
    if update.get('warning'):
        merged['warning'] = update['warning']
    return merged


def restart_application(gate, launcher=None):
    """Release the singleton before launching a process that reads current disk state."""
    if gate:
        gate.release()
    if launcher is None:
        from desktop_launcher import request
        launcher=request
    return launcher('ui')


def main():
    smoke = os.environ.get('SWITCHBOARD_SMOKE') == '1'
    if not smoke:
        import desktop_launcher
        try:
            if desktop_launcher.needs_broker():
                desktop_launcher.request('ui')
                return
        except Exception as exc:
            ctypes.windll.user32.MessageBoxW(None,str(exc),'NectarDock launch held',0x10)
            return
    gate = None if smoke else InstanceGate()
    if gate and not gate.enter():
        ctypes.windll.user32.MessageBoxW(
            None,
            'An earlier switchboard is still open or busy. No window was force-closed. Finish its operation and try again.',
            'Claude account switchboard', 0x30)
        return
    root = tk.Tk()
    preferences, preferences_warning = load_preferences(PREFERENCES_PATH)
    usage_window = [None]; usage_rerender = [None]
    root.title(f'{APP_NAME} {APP_VERSION} ({RELEASE_CHANNEL})')
    root.geometry('1440x900')
    root.minsize(1180, 760)
    root.configure(bg='#232521')
    activity_recorder=ActivityRecorder(engine.RUNS)
    activity_entries=[]; activity_window=[None]; activity_text=[None]
    activity_path=tk.StringVar(value='Log file will be created when the first local command starts.')
    style = ttk.Style(root); style.theme_use('clam')
    style.configure('.', background='#232521', foreground='#F4F1DE', fieldbackground='#30362C')
    style.configure('Treeview', background='#30362C', fieldbackground='#30362C', foreground='#F4F1DE', rowheight=27)
    style.configure('Treeview.Heading', background='#414C3E', foreground='#F4F1DE', font=('Segoe UI Semibold',10))
    style.configure('TLabelframe', background='#232521', foreground='#F4F1DE')
    style.configure('TLabelframe.Label', background='#232521', foreground='#EFBD58', font=('Segoe UI Semibold',10))
    style.configure('Toolbar.TButton', padding=(14,8), font=('Segoe UI Semibold',10))
    style.map('Treeview', background=[('selected','#506844')])
    style.configure('Workspace.TCombobox', fieldbackground='#ffffff', foreground='#111111')
    style.map('Workspace.TCombobox', fieldbackground=[('readonly','#ffffff')],
              foreground=[('readonly','#111111')], selectforeground=[('readonly','#111111')])
    style.configure('Account.TCombobox', fieldbackground='#ffffff', foreground='#145A32')
    style.map('Account.TCombobox', fieldbackground=[('readonly','#ffffff')],
              foreground=[('readonly','#145A32')], selectforeground=[('readonly','#145A32')])
    root.option_add('*TCombobox*Listbox.background', '#ffffff')
    root.option_add('*TCombobox*Listbox.foreground', '#111111')
    root.option_add('*TCombobox*Listbox.selectBackground', '#1f6285')
    root.option_add('*TCombobox*Listbox.selectForeground', '#ffffff')
    frame = ttk.Frame(root, padding=16); frame.pack(fill='both', expand=True)
    ttk.Label(frame, text=f'{APP_NAME} {APP_VERSION}', font=('Segoe UI Semibold',20)).pack(anchor='w')
    ttk.Label(frame, text=f'by {PUBLISHER} | {RELEASE_CHANNEL}').pack(anchor='w')
    toolbar = ttk.Frame(frame); toolbar.pack(fill='x', pady=(8,10))
    for label, callback in [('Refresh data',lambda:manual_refresh()), ('Usage',lambda:open_usage()),
                            ('Reload App',lambda:reload_app()), ('Options',lambda:open_options())]:
        ttk.Button(toolbar,text=label,command=callback,style='Toolbar.TButton').pack(side='left',padx=(0,10))
    ttk.Label(frame, text='Reviewed saved-history transitions and account configuration. No account logout, credential copy, model prompt, or forced session closure.').pack(anchor='w', pady=(5,8))
    legend = ttk.LabelFrame(frame, text='COLOR → Claude account', padding=(10,5)); legend.pack(fill='x', pady=(0,9))
    for name in engine.ACCOUNTS:
        account = engine.ACCOUNTS[name]
        ink = ACCOUNT_INKS.get(name, '#F4F1DE')
        tk.Label(legend, text=f"{name}  {account['email']} ({account['plan']})", bg='#232521',
                 fg=ink, font=('Segoe UI Semibold',10)).pack(side='left', padx=(0,22))
    if 'BLUE' not in engine.ACCOUNTS:
        tk.Label(legend,text='BLUE  unassigned',bg='#232521',fg=ACCOUNT_INKS['BLUE'],
                 font=('Segoe UI Semibold',10)).pack(side='left')
    controls = ttk.Frame(frame); controls.pack(fill='x')
    group = tk.StringVar(value='FPA'); color = tk.StringVar(value='ORANGE')
    ttk.Label(controls,text='Workspace').pack(side='left')
    group_box = ttk.Combobox(controls,textvariable=group,values=list(engine.GROUPS),state='readonly',width=12,style='Workspace.TCombobox')
    group_box.pack(side='left',padx=10)
    ttk.Label(controls,text='Destination account').pack(side='left')
    color_box = ttk.Combobox(controls,textvariable=color,values=list(engine.ACCOUNTS),state='readonly',width=14,style='Account.TCombobox')
    color_box.pack(side='left',padx=10)
    account_label = ttk.Label(frame,text='');account_label.pack(anchor='w',pady=9)
    tree = ttk.Treeview(frame,columns=tuple(name for name, _, _ in TABLE_COLUMNS),show='headings',selectmode='extended',height=10)
    for name,label,width in TABLE_COLUMNS:
        tree.heading(name,text=label,command=lambda key=name:sort_by_column(key));tree.column(name,width=width)
    tree.pack(fill='both',expand=True)
    horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
    tree.configure(xscrollcommand=horizontal.set)
    horizontal.pack(fill='x')
    sort_state = {'column':'modified', 'descending':True}
    def apply_table_sort():
        rows = [(iid,tree.item(iid,'values')) for iid in tree.get_children()]
        for position,(iid,_) in enumerate(sort_table_rows(rows,sort_state['column'],sort_state['descending'])):
            tree.move(iid,'',position)
        for name,label,_ in TABLE_COLUMNS:
            arrow = (' ↓' if sort_state['descending'] else ' ↑') if name == sort_state['column'] else ''
            tree.heading(name,text=label+arrow)
    def sort_by_column(name):
        if sort_state['column'] == name:
            sort_state['descending'] = not sort_state['descending']
        else:
            sort_state.update(column=name,descending=False)
        apply_table_sort()
    ttk.Label(frame,text='Select only the conversations to move. Ctrl/Shift selects multiple rows; nothing is preselected. Modified time and version come from saved history, not live runtime.').pack(anchor='w',pady=6)
    buttons = ttk.Frame(frame);buttons.pack(fill='x',pady=6)
    prepared = [None]; regularization_prepared = [None]; busy = [False]; auxiliary_jobs = [0]
    active_dialog = [None]
    reload_requested = [False]
    variants = tk.BooleanVar(value=False)
    check = ttk.Checkbutton(frame,variable=variants,text='I reviewed the variant list: preserve old copies, use the current source companions, and keep existing destination memory notes.')
    check.pack(anchor='w',pady=6)
    widgets=[]

    def show(value):
        output.delete('1.0','end')
        output.insert('end',value if isinstance(value,str) else json.dumps(value,indent=2))

    def append_activity(message):
        try:
            line=activity_recorder.record(message)
            activity_path.set(str(activity_recorder.path))
        except OSError:
            line=f'[{dt.datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z}] {message} [disk log unavailable]'
        activity_entries.append(line)
        dialog=active_dialog[0]
        if dialog is not None and dialog.busy and dialog.window.winfo_exists():
            dialog.status.set(str(message))
        panel=activity_text[0]
        if panel is not None and panel.winfo_exists():
            panel.configure(state='normal');panel.insert('end',line+'\n')
            panel.see('end');panel.configure(state='disabled')

    def queue_activity(message):
        root.after(0,lambda item=message:append_activity(item))

    def execute_logged(script,args):
        python=Path(sys.executable).with_name('python.exe')
        argv=[str(python),str(engine.BASE/script)]+args
        queue_activity('RUN '+subprocess.list2cmdline(argv))
        data,exit_code,elapsed=run_json_command(argv,queue_activity)
        queue_activity(f"END exit={exit_code} state={data.get('state','unavailable')} elapsed={elapsed:.1f}s")
        for key in ('manifest','review','pending_recovery'):
            if data.get(key): queue_activity(f'{key}: {data[key]}')
        return data

    def show_activity():
        if activity_window[0] is not None and activity_window[0].winfo_exists():
            activity_window[0].lift();return
        panel=tk.Toplevel(root);panel.title('Switchboard local activity log')
        panel.geometry('1120x480');panel.configure(bg='#0d1821')
        activity_window[0]=panel
        body=ttk.Frame(panel,padding=12);body.pack(fill='both',expand=True)
        ttk.Label(body,text='Local command activity',font=('Segoe UI Semibold',16)).pack(anchor='w')
        ttk.Label(body,textvariable=activity_path).pack(anchor='w',pady=(2,6))
        ttk.Button(body,text='Open log file',command=lambda:os.startfile(activity_recorder.path)
                   if activity_recorder.path and activity_recorder.path.exists()
                   else messagebox.showinfo('No activity yet','A log file is created when the first command starts.',parent=panel)).pack(anchor='w',pady=(0,6))
        log=tk.Text(body,bg='#081117',fg='#d9e8ee',insertbackground='#57c7ff',wrap='none',
                    font=('Cascadia Mono',10),padx=10,pady=8)
        log.pack(fill='both',expand=True)
        log.insert('end','\n'.join(activity_entries)+('\n' if activity_entries else ''))
        log.configure(state='disabled');activity_text[0]=log
        def close_activity():
            activity_text[0]=None;activity_window[0]=None;panel.destroy()
        panel.protocol('WM_DELETE_WINDOW',close_activity)

    def invalidate(*_):
        if not busy[0]:
            prepared[0]=None;variants.set(False);apply_button.configure(state='disabled')

    def invalidate_regularization(*_):
        if not busy[0]:
            regularization_prepared[0]=None;regularization_reviewed.set(False)
            regularization_review.configure(state='disabled');regularization_apply.configure(state='disabled')

    def refresh(*_):
        if busy[0]: return False
        invalidate();invalidate_regularization()
        try:
            _,maps=engine.dependencies();view=maps.load_verified(engine.INDEX,engine.MAP_HISTORY)
            tree.delete(*tree.get_children())
            homes={engine.norm(a['home']):c for c,a in engine.ACCOUNTS.items()}
            for row in engine.selected(view,group.get()):
                tree.insert('', 'end',iid=row['uuid'],values=table_row_values(row,homes))
            apply_table_sort()
            a=engine.ACCOUNTS[color.get()]
            account_label.configure(text=color.get()+' → '+a['email']+' / '+a['plan'])
            ink = {'GREEN':'#145A32','YELLOW':'#7B5800','ORANGE':'#9B3D00','PURPLE':'#5B2C83','BLUE':'#154A80'}.get(color.get(), '#111111')
            style.map('Account.TCombobox', foreground=[('readonly',ink)], selectforeground=[('readonly',ink)])
            return True
        except Exception as exc:
            show(str(exc))
            return False

    def manual_refresh():
        if refresh():
            queue_activity(f'Table refreshed: {group.get()} / {color.get()}')
            show('Main table refreshed from the current-home register and saved history files. Selection and any prepared review were cleared; no histories moved.')

    def reload_app():
        if not safe_to_close(busy[0], auxiliary_jobs[0]):
            messagebox.showinfo('Operation running', 'Wait for all local commands to finish before reloading.', parent=root)
            return
        if (prepared[0] or regularization_prepared[0]) and not messagebox.askyesno(
                'Discard prepared review?',
                'Reloading discards the current prepared review and selection. No histories will move. Reload now?',
                parent=root):
            return
        reload_requested[0] = True
        root.destroy()

    def finished(mode,result):
        busy[0]=False
        for widget in widgets: widget.configure(state='normal')
        group_box.configure(state='readonly');color_box.configure(state='readonly')
        show(result)
        if mode=='prepare' and result.get('state')=='PREPARED_REVIEW_REQUIRED':
            prepared[0]=result;apply_button.configure(state='normal')
            messagebox.showinfo('Ready for review','Preparation is complete. No histories have been changed. Read the review before applying.')
        else:
            prepared[0]=None;apply_button.configure(state='disabled')
        regularization_review.configure(state='normal' if regularization_prepared[0] else 'disabled')
        regularization_apply.configure(state='normal' if regularization_prepared[0] else 'disabled')
        if result.get('state')=='SWITCH_COMPLETE_SAVED_HISTORIES_READY':
            messagebox.showinfo('Switch complete','Saved histories and the account map are updated. The destination workspace has been requested. Resume the original conversations in Claude Code history. Select effort before your next prompt; monitors and AutoClaude participation are not rearmed.')
        sid=conflict_uuid(result,tree.get_children())
        if sid and messagebox.askyesno('Compare the conflicting histories?',
                'This conversation has distinct saved branches. Compare both accounts and choose whether to use '
                'the registered source as the survivor? Nothing is replaced until you review and apply.',parent=root):
            tree.selection_set(sid)
            root.after_idle(open_survivor)

    def run(mode):
        if busy[0]:return
        chosen=list(tree.selection())
        if mode not in ('apply','diagnose') and not chosen:
            messagebox.showinfo('Select conversations','Choose one or more conversations first.');return
        if mode=='diagnose':
            args=['diagnose']
        elif mode=='apply':
            p=prepared[0]
            if not p:return
            if (p.get('memory_variants') or p.get('companion_variants')) and not variants.get():
                messagebox.showinfo('Review variants','Open the review and confirm the variant-handling checkbox first.');return
            if not messagebox.askyesno('Apply this reviewed switch?',f"Move {p['sessions']} selected conversations to {color.get()}?\n\nTheir Claude tabs must remain closed until completion. Old versions are retained. No prompts or warm loops will be started."):
                return
            args=['apply','--manifest',p['manifest'],'--sha256',p['sha256'],'--open']
            if variants.get():args+=['--accept-memory-variants','--accept-companion-variants']
        elif mode=='open':
            if not messagebox.askyesno('Open workspace only?',
                    f'Launch {group.get()} in {color.get()} for the {len(chosen)} selected registered conversation(s)?\n\n'
                    'This opens the whole VS Code workspace, not the selected Claude tabs, and does not move history. '
                    'The saved-tab preflight refuses tabs outside your selection or registered in another account. '
                    'It cannot automatically resume missing selected tabs. Verify the exact original UUID and latest content after opening.'):
                return
            args=['open','--group',group.get(),'--destination',color.get()]
            for sid in chosen:args+=['--uuid',sid]
        else:
            args=[mode,'--group',group.get(),'--destination',color.get()]
            for sid in chosen:args+=['--uuid',sid]
        busy[0]=True
        for widget in widgets:widget.configure(state='disabled')
        group_box.configure(state='disabled');color_box.configure(state='disabled')
        show('Working locally. Keep the selected conversations closed. No model is running.\n'
             'Command: '+subprocess.list2cmdline([str(Path(sys.executable).with_name('python.exe')),
                     str(engine.BASE/'switcher.py')]+args)+'\nOpen Activity log for progress.')
        def worker():
            try:
                data=execute_logged('switcher.py',args)
            except Exception as exc:
                queue_activity('FAILED '+type(exc).__name__)
                data={'state':'HELD','error':str(exc)}
            root.after(0,lambda:finished(mode,data))
        threading.Thread(target=worker,daemon=True).start()

    def review():
        if prepared[0]:os.startfile(prepared[0]['review'])
        else:messagebox.showinfo('No prepared review','Run Prepare first.')

    for label,command in [('Select all',lambda:tree.selection_set(tree.get_children())),('Clear',lambda:tree.selection_remove(tree.selection())),('Refresh table',manual_refresh),('Check readiness',lambda:run('status')),('Prepare transition (batch)',lambda:open_batch()),('Open destination workspace',lambda:run('open'))]:
        b=ttk.Button(buttons,text=label,command=command);b.pack(side='left',padx=(0,8));widgets.append(b)
    apply_button=ttk.Button(buttons,text='Apply and open workspace',command=lambda:run('apply'),state='disabled')
    # Retained for legacy command bookkeeping; new transfers are reviewed/applied in the batch dialog.
    widgets.append(apply_button)
    diagnostic_button=ttk.Button(root,text='Runtime diagnostics',command=lambda:run('diagnose'))
    diagnostic_button.pack(anchor='w',padx=12);widgets.append(diagnostic_button)

    regularization = ttk.LabelFrame(frame,text='Shared settings and skills',padding=(12,8))
    regularization.pack(fill='x',pady=(8,4))
    ttk.Label(regularization,text='Scope: selected destination account. Protected account settings, auth, hooks, permissions, model choices, and target-only resources are never copied.').pack(anchor='w')
    regularization_buttons=ttk.Frame(regularization);regularization_buttons.pack(fill='x',pady=(7,2))
    regularization_reviewed=tk.BooleanVar(value=False)
    regularization_check=ttk.Checkbutton(regularization,variable=regularization_reviewed,text='I reviewed the exact manifest and backup/recovery behavior.')
    regularization_check.pack(anchor='w',pady=(3,0))

    def finished_regularization(mode,result):
        busy[0]=False
        for widget in widgets:widget.configure(state='normal')
        group_box.configure(state='readonly');color_box.configure(state='readonly')
        show(result)
        if mode.startswith('prepare-') and result.get('state')=='PREPARED_REVIEW_REQUIRED':
            regularization_prepared[0]=result
            regularization_review.configure(state='normal');regularization_apply.configure(state='normal')
            messagebox.showinfo('Regularization prepared','No settings or skill files changed. Open and review the exact manifest before applying.')
        elif mode=='apply-regularization':
            regularization_prepared[0]=None;regularization_reviewed.set(False)
            regularization_review.configure(state='disabled');regularization_apply.configure(state='disabled')
            if result.get('state')=='REGULARIZATION_COMPLETE':
                messagebox.showinfo('Regularization complete','Disk hashes and backups are verified. Already-running tasks may still have cached settings or skill instructions.')
        apply_button.configure(state='normal' if prepared[0] else 'disabled')

    def run_regularization(mode,kind=None):
        if busy[0]:return
        if mode=='apply-regularization':
            item=regularization_prepared[0]
            if not item:return
            if not regularization_reviewed.get():
                messagebox.showinfo('Review required','Open the exact review and confirm the checkbox before applying.');return
            if not messagebox.askyesno('Apply reviewed regularization?',f"Apply {item['changed_files']} reviewed {item['kind']} file changes to {color.get()}?\n\nBackups and a durable receipt will be written. Running tasks may retain cached instructions."):
                return
            args=['apply','--manifest',item['manifest'],'--sha256',item['sha256']]
        elif mode=='audit':
            args=['audit','--account',color.get()]
        else:
            args=['prepare','--kind',kind,'--account',color.get()]
        busy[0]=True
        for widget in widgets:widget.configure(state='disabled')
        group_box.configure(state='disabled');color_box.configure(state='disabled')
        show('Working locally. No model is running.\n'
             'Command: '+subprocess.list2cmdline([str(Path(sys.executable).with_name('python.exe')),
                     str(engine.BASE/'regularize.py')]+args)+'\nOpen Activity log for progress.')
        def worker():
            try:
                data=execute_logged('regularize.py',args)
            except Exception as exc:
                queue_activity('FAILED '+type(exc).__name__)
                data={'state':'HELD','error':str(exc)}
            root.after(0,lambda:finished_regularization(mode,data))
        threading.Thread(target=worker,daemon=True).start()

    def review_regularization():
        if regularization_prepared[0]:os.startfile(regularization_prepared[0]['review'])
        else:messagebox.showinfo('No prepared review','Prepare settings or skills first.')

    for label,command in [('Compare settings / skills',lambda:run_regularization('audit')),('Prepare settings',lambda:run_regularization('prepare-settings','settings')),('Prepare skills',lambda:run_regularization('prepare-skills','skills'))]:
        button=ttk.Button(regularization_buttons,text=label,command=command);button.pack(side='left',padx=(0,8));widgets.append(button)
    regularization_review=ttk.Button(regularization_buttons,text='Open regularization review',command=review_regularization,state='disabled')
    regularization_review.pack(side='left',padx=(0,8));widgets.append(regularization_review)
    regularization_apply=ttk.Button(regularization_buttons,text='Apply reviewed regularization',command=lambda:run_regularization('apply-regularization'),state='disabled')
    regularization_apply.pack(side='right');widgets.append(regularization_apply)

    utilities = ttk.Frame(frame); utilities.pack(fill='x', pady=(5,2))

    def child_json(script, args, callback):
        def worker():
            try:
                data=execute_logged(script,args)
            except Exception as exc:
                queue_activity('FAILED '+type(exc).__name__)
                data = {'state':'HELD', 'error':str(exc)}
            def deliver():
                auxiliary_jobs[0] -= 1
                callback(data)
            root.after(0, deliver)
        start_tracked_worker(auxiliary_jobs,worker)

    def open_survivor():
        if not safe_to_close(busy[0],auxiliary_jobs[0]):
            messagebox.showinfo('Operation running','Wait for current local commands to finish.',parent=root);return
        chosen=list(tree.selection())
        if len(chosen)!=1:
            messagebox.showinfo('Select one conversation','Choose exactly one conversation and its destination account, then compare branches.',parent=root);return
        invalidate();invalidate_regularization()
        def command(script,args,callback):
            busy[0]=True
            def done(result):
                busy[0]=False
                callback(result)
            try:
                child_json(script,args,done)
            except Exception:
                busy[0]=False
                raise
        def completed(result):
            refresh();show(result)
            queue_activity('Survivor applied to saved history. No workspace launch requested.')
        active_dialog[0]=SurvivorDialog(root,group.get(),color.get(),chosen[0],command,completed)

    def open_batch():
        if not safe_to_close(busy[0],auxiliary_jobs[0]):
            messagebox.showinfo('Operation running','Wait for current local commands to finish.',parent=root);return
        chosen=list(tree.selection())
        if not chosen:
            messagebox.showinfo('Select conversations','Choose the conversations to transfer, then choose the destination account.',parent=root);return
        invalidate();invalidate_regularization()
        def command(script,args,callback):
            busy[0]=True
            def done(result):
                busy[0]=False;callback(result)
            try:child_json(script,args,done)
            except Exception:
                busy[0]=False;raise
        def completed(result):
            refresh();show(result);queue_activity('Reviewed batch applied. No workspace launch requested.')
        active_dialog[0]=BatchDialog(root,group.get(),color.get(),chosen,command,completed)

    survivor_button=ttk.Button(utilities,text='Compare branches / choose survivor',command=open_survivor)
    survivor_button.pack(side='left',padx=(0,8));widgets.append(survivor_button)

    def open_inventory():
        window = tk.Toplevel(root); window.title('Account inventory, compare and reviewed merge')
        window.geometry('1080x690'); window.configure(bg='#0d1821')
        body = ttk.Frame(window, padding=12); body.pack(fill='both', expand=True)
        ttk.Label(body, text='Cross-account inventory and merge', font=('Segoe UI Semibold',16)).pack(anchor='w')
        ttk.Label(body, text='Source → target. Inventory/compare only read; prepare writes a local review; apply changes the selected target after confirmation.').pack(anchor='w', pady=(2,8))
        row = ttk.Frame(body); row.pack(fill='x')
        src, dst = tk.StringVar(value='GREEN'), tk.StringVar(value=color.get() if color.get()!='GREEN' else 'YELLOW')
        ttk.Label(row,text='Source').pack(side='left')
        source_box = ttk.Combobox(row, textvariable=src, values=list(engine.ACCOUNTS), state='readonly', width=13, style='Workspace.TCombobox')
        source_box.pack(side='left', padx=(7,20))
        ttk.Label(row,text='Target').pack(side='left')
        target_box = ttk.Combobox(row, textvariable=dst, values=list(engine.ACCOUNTS), state='readonly', width=13, style='Workspace.TCombobox')
        target_box.pack(side='left', padx=7)
        categories = {name:tk.BooleanVar(value=False) for name in ('settings','permissions','skills','mcps')}
        options = ttk.Frame(body); options.pack(fill='x', pady=(8,3))
        for name, variable in categories.items():
            ttk.Checkbutton(options,text=name.title(),variable=variable).pack(side='left',padx=(0,16))
        ttk.Label(body,text='Credentialed MCPs are held; shared settings are allowlisted; permissions add nonconflicting entries. Running sessions may need restart.').pack(anchor='w')
        controls2 = ttk.Frame(body); controls2.pack(fill='x',pady=7)
        report = tk.Text(body,bg='#081117',fg='#d9e8ee',insertbackground='#57c7ff',wrap='word',font=('Cascadia Mono',10),padx=10,pady=8)
        report.pack(fill='both',expand=True)
        prepared_merge = [None]
        reviewed = tk.BooleanVar(value=False)
        confirm = ttk.Checkbutton(body,text='I opened and reviewed this exact merge review, including backups and held conflicts.',variable=reviewed)
        confirm.pack(anchor='w',pady=(7,0))
        def display(data):
            report.delete('1.0','end'); report.insert('end',json.dumps(data,indent=2,ensure_ascii=False))
        def invalidate_merge(*_):
            prepared_merge[0]=None; reviewed.set(False)
            open_review.configure(state='disabled'); merge_apply.configure(state='disabled')
        for variable in (src,dst,*categories.values()): variable.trace_add('write',invalidate_merge)
        def operate(mode):
            if mode != 'inventory' and src.get()==dst.get():
                messagebox.showinfo('Choose two accounts','Source and target must differ.',parent=window);return
            selected=[name for name,value in categories.items() if value.get()]
            if mode=='prepare' and not selected:
                messagebox.showinfo('Choose categories','Select at least one category.',parent=window);return
            if mode=='apply':
                item=prepared_merge[0]
                if not item or not reviewed.get():
                    messagebox.showinfo('Review required','Open and confirm the exact review first.',parent=window);return
                if not messagebox.askyesno('Apply reviewed merge?',f"Apply {item['changed_files']} file changes from {src.get()} to {dst.get()}?\n\nBackups and a receipt will be written. Running sessions may retain cached configuration.",parent=window):
                    return
                args=['apply','--manifest',item['manifest'],'--sha256',item['sha256']]
            elif mode=='inventory': args=['inventory']
            else:
                args=[mode,'--source',src.get(),'--target',dst.get()]
                if mode=='prepare':
                    for name in selected: args+=['--category',name]
            for item in action_buttons: item.configure(state='disabled')
            display({'state':'WORKING','operation':mode})
            def finish(data):
                if not window.winfo_exists(): return
                for item in action_buttons: item.configure(state='normal')
                display(data)
                if mode=='prepare' and data.get('state')=='PREPARED_REVIEW_REQUIRED':
                    prepared_merge[0]=data; open_review.configure(state='normal'); merge_apply.configure(state='normal')
                    messagebox.showinfo('Prepared for review','No account files changed. Open the review before applying.',parent=window)
                elif mode=='apply': invalidate_merge()
                open_review.configure(state='normal' if prepared_merge[0] else 'disabled')
                merge_apply.configure(state='normal' if prepared_merge[0] else 'disabled')
            child_json('account_inventory.py',args,finish)
        action_buttons=[]
        for label,mode in [('Inventory all','inventory'),('Compare pair','compare'),('Prepare merge','prepare')]:
            button=ttk.Button(controls2,text=label,command=lambda m=mode:operate(m))
            button.pack(side='left',padx=(0,8));action_buttons.append(button)
        def review_merge():
            if prepared_merge[0]: os.startfile(prepared_merge[0]['review'])
        open_review=ttk.Button(controls2,text='Open merge review',command=review_merge,state='disabled')
        open_review.pack(side='left',padx=(0,8)); action_buttons.append(open_review)
        merge_apply=ttk.Button(controls2,text='Apply reviewed merge',command=lambda:operate('apply'),state='disabled')
        merge_apply.pack(side='right'); action_buttons.append(merge_apply)
        display({'state':'READY','meaning':'Inventory and compare are read-only. Choose distinct source and target for a reviewed merge.'})

    def open_options():
        window = tk.Toplevel(root); window.title('Display options')
        body = ttk.Frame(window,padding=16); body.pack(fill='both',expand=True)
        ttk.Label(body,text='Simple options',font=('Segoe UI Semibold',16)).grid(row=0,column=0,columnspan=2,sticky='w',pady=(0,10))
        threshold = tk.StringVar(value=str(preferences['usage_threshold']))
        auto_fit = tk.BooleanVar(value=preferences['auto_fit'])
        details = tk.BooleanVar(value=preferences['show_details'])
        sort = tk.StringVar(value=next(k for k,v in USAGE_BUCKETS.items() if v==preferences['usage_sort']))
        descending = tk.BooleanVar(value=preferences['usage_descending'])
        ttk.Label(body,text='Strike usage strictly above (%)').grid(row=1,column=0,sticky='w')
        ttk.Spinbox(body,from_=0,to=100,textvariable=threshold,width=8).grid(row=1,column=1,sticky='w',padx=12)
        ttk.Checkbutton(body,text='Auto-fit usage window to content and monitor',variable=auto_fit).grid(row=2,column=0,columnspan=2,sticky='w',pady=6)
        ttk.Checkbutton(body,text='Show usage observation details',variable=details).grid(row=3,column=0,columnspan=2,sticky='w',pady=6)
        ttk.Label(body,text='Default usage sort').grid(row=4,column=0,sticky='w')
        ttk.Combobox(body,values=list(USAGE_BUCKETS),textvariable=sort,state='readonly',width=12,style='Workspace.TCombobox').grid(row=4,column=1,sticky='w',padx=12)
        ttk.Checkbutton(body,text='Sort highest usage first',variable=descending).grid(row=5,column=0,columnspan=2,sticky='w',pady=6)
        ttk.Label(body,text='These options change display only. No safety checks can be disabled.').grid(row=6,column=0,columnspan=2,sticky='w',pady=8)
        def save_options():
            try:
                values = validate_preferences({'usage_threshold':float(threshold.get()),
                    'auto_fit':auto_fit.get(), 'show_details':details.get(),
                    'usage_sort':USAGE_BUCKETS[sort.get()], 'usage_descending':descending.get()})
                if preferences_warning and not messagebox.askyesno('Replace unreadable preferences?',
                        preferences_warning+'\nSave these display choices over that file?',parent=window):
                    return
                save_preferences(values, PREFERENCES_PATH)
            except (ValueError, KeyError, OSError) as exc:
                messagebox.showerror('Options not saved',str(exc),parent=window); return
            preferences.clear(); preferences.update(values)
            if usage_rerender[0] and usage_window[0] is not None and usage_window[0].winfo_exists():
                usage_rerender[0]()
            window.destroy()
        ttk.Button(body,text='Save options',command=save_options).grid(row=7,column=0,sticky='w')
        ttk.Button(body,text='Cancel',command=window.destroy).grid(row=7,column=1,sticky='e')
        advanced = ttk.LabelFrame(body,text='Developer diagnostics',padding=8)
        def toggle_developer():
            if advanced.winfo_manager(): advanced.grid_remove()
            else: advanced.grid(row=9,column=0,columnspan=2,sticky='ew',pady=(8,0))
        ttk.Button(body,text='Show / hide developer diagnostics',command=toggle_developer).grid(row=8,column=0,columnspan=2,sticky='w',pady=(12,0))
        ttk.Button(advanced,text='Activity log',command=show_activity).pack(side='left',padx=4)
        ttk.Button(advanced,text='Runtime diagnostics',command=lambda:run('diagnose')).pack(side='left',padx=4)
        ttk.Label(body,text=f'Preferences: {PREFERENCES_PATH}',wraplength=560).grid(row=10,column=0,columnspan=2,sticky='w',pady=(12,0))
        window.transient(root)

    def open_usage():
        if usage_window[0] is not None and usage_window[0].winfo_exists():
            usage_window[0].lift(); return
        window=tk.Toplevel(root);window.title('Claude account usage snapshots')
        usage_window[0] = window
        window.geometry('980x470');window.configure(bg='#0d1821')
        body=ttk.Frame(window,padding=12);body.pack(fill='both',expand=True)
        ttk.Label(body,text='Account usage snapshots',font=('Segoe UI Semibold',16)).pack(anchor='w')
        ttk.Label(body,text='Account-wide 5-hour, weekly, and Fable limits. Fresh checks show hh:mm at observation; stale readings show last reported local reset time.').pack(anchor='w',pady=(2,8))
        usage_controls=ttk.Frame(body);usage_controls.pack(fill='x',pady=(0,7))
        refresh_button=ttk.Button(usage_controls,text='Refresh snapshot');refresh_button.pack(side='left',padx=(0,16))
        ttk.Label(usage_controls,text='Sort by').pack(side='left')
        usage_sort=tk.StringVar(value=next(k for k,v in USAGE_BUCKETS.items() if v==preferences['usage_sort']))
        usage_descending=tk.BooleanVar(value=preferences['usage_descending'])
        sort_box=ttk.Combobox(usage_controls,values=list(USAGE_BUCKETS),textvariable=usage_sort,state='readonly',width=12,style='Workspace.TCombobox')
        sort_box.pack(side='left',padx=8)
        ttk.Checkbutton(usage_controls,text='Highest first',variable=usage_descending,
                        command=lambda:render_usage(snapshot[0])).pack(side='left')
        ttk.Button(usage_controls,text='Usage options',command=open_options).pack(side='right')
        account_actions=ttk.Frame(body);account_actions.pack(fill='x',pady=(0,7))
        report_frame=ttk.Frame(body);report_frame.pack(fill='both',expand=True)
        report=tk.Text(report_frame,bg='#232521',fg='#F4F1DE',insertbackground='#EFBD58',wrap='word',font=('Cascadia Mono',10),padx=10,pady=8)
        report.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(report_frame,orient='vertical',command=report.yview)
        scroll.pack(side='right',fill='y');report.configure(yscrollcommand=scroll.set)
        snapshot=[{'accounts':[], 'errors':{}}]
        def open_login(account_color):
            account=engine.ACCOUNTS[account_color]
            if not messagebox.askyesno('Open isolated Claude sign-in?',
                    f"Open a sign-in terminal for {account_color}: {account['email']} / {account['plan']}?\n\n"
                    +login_guidance(account_color)+'\n\nNo other account home is touched.',parent=window):
                return
            try:
                result=engine.launch_login(account_color)
            except Exception as exc:
                messagebox.showerror('Sign-in launch failed',str(exc),parent=window)
                return
            messagebox.showinfo('Sign-in opened',
                f"{result['account']} sign-in is open in a separate terminal. Complete it, then click Retry {account_color} usage. "
                +login_guidance(account_color)+' A login window opening does not prove the correct account is authenticated.',
                parent=window)
        def render_usage(data):
            for child in account_actions.winfo_children(): child.destroy()
            for account_color,error in data.get('errors',{}).items():
                if needs_usage_login(error):
                    ttk.Button(account_actions,text=f'Open {account_color} sign-in',
                        command=lambda selected=account_color:open_login(selected)).pack(side='left',padx=(0,7))
                    ttk.Button(account_actions,text=f'Retry {account_color} usage',
                        command=lambda selected=account_color:refresh_usage(selected)).pack(side='left',padx=(0,14))
            display_preferences=dict(preferences,usage_sort=USAGE_BUCKETS[usage_sort.get()],usage_descending=usage_descending.get())
            render_usage_report(report,data,display_preferences)
            if preferences['auto_fit']:
                text=report.get('1.0','end-1c')
                report.configure(width=min(120,max(70,max((len(line) for line in text.splitlines()),default=70))),
                                 height=min(32,max(8,len(text.splitlines())+1)))
                fit_window(window)
        def apply_usage_options():
            usage_sort.set(next(k for k,v in USAGE_BUCKETS.items() if v==preferences['usage_sort']))
            usage_descending.set(preferences['usage_descending'])
            render_usage(snapshot[0])
        usage_rerender[0]=apply_usage_options
        sort_box.bind('<<ComboboxSelected>>',lambda *_:render_usage(snapshot[0]))
        def refresh_usage(account_color=None):
            refresh_button.configure(state='disabled')
            for child in account_actions.winfo_children(): child.configure(state='disabled')
            report.configure(state='normal');report.delete('1.0','end')
            report.insert('end',f'Checking {account_color} usage…' if account_color else 'Checking each isolated Claude account…')
            report.configure(state='disabled')
            def finish(data):
                if not window.winfo_exists(): return
                refresh_button.configure(state='normal')
                snapshot[0]=merge_usage_snapshot(snapshot[0],data,account_color)
                render_usage(snapshot[0])
            child_json('usage_snapshot.py',['--account',account_color] if account_color else [],finish)
        refresh_button.configure(command=refresh_usage)
        refresh_usage()

    def open_registration():
        window=tk.Toplevel(root);window.title('Unregistered Claude histories')
        window.geometry('1150x620');window.configure(bg='#0d1821')
        body=ttk.Frame(window,padding=12);body.pack(fill='both',expand=True)
        ttk.Label(body,text='Unregistered saved histories',font=('Segoe UI Semibold',16)).pack(anchor='w')
        ttk.Label(body,text='Discovery is read-only. Register exact reviewed UUIDs in their existing account before preparing any transfer. No tabs are opened.').pack(anchor='w',pady=(3,8))
        source=tk.StringVar(value='GREEN')
        bar=ttk.Frame(body);bar.pack(fill='x',pady=4)
        ttk.Label(bar,text='Existing account').pack(side='left')
        source_box=ttk.Combobox(bar,textvariable=source,values=list(engine.ACCOUNTS),state='readonly',width=13,style='Account.TCombobox')
        source_box.pack(side='left',padx=(8,15))
        listing=ttk.Treeview(body,columns=('label','home','modified','uuid'),show='headings',selectmode='extended',height=9)
        for key,label,width in [('label','Native title',330),('home','Saved homes',160),('modified','History modified (local)',220),('uuid','UUID',330)]:
            listing.heading(key,text=label);listing.column(key,width=width)
        listing.pack(fill='both',expand=True)
        output_box=tk.Text(body,height=8,bg='#081117',fg='#d9e8ee',insertbackground='#57c7ff',wrap='word',font=('Cascadia Mono',10))
        output_box.pack(fill='both',expand=True,pady=(7,0))
        action=ttk.Frame(body);action.pack(fill='x',pady=(8,0))
        item=[None]; working=[False]
        def display(value):
            output_box.delete('1.0','end');output_box.insert('end',value if isinstance(value,str) else json.dumps(value,indent=2))
        def run_registration(args,done):
            if working[0] or busy[0]:return
            working[0]=True;busy[0]=True
            for button in actions:button.configure(state='disabled')
            source_box.configure(state='disabled')
            display('Working locally: '+subprocess.list2cmdline(['registration.py']+args)+'\nSee Activity log for command progress.')
            def finish(data):
                working[0]=False;busy[0]=False
                if not window.winfo_exists():return
                for button in actions:button.configure(state='normal')
                source_box.configure(state='readonly')
                display(data);done(data)
            child_json('registration.py',args,finish)
        def inventory():
            item[0]=None
            def finish(data):
                listing.delete(*listing.get_children())
                for row in data.get('items',[]):
                    copies=row['copies']
                    modified=max(c['modified_ns'] for c in copies)
                    local=dt.datetime.fromtimestamp(modified/1e9).astimezone().strftime('%Y-%m-%d %I:%M %p %Z')
                    listing.insert('','end',iid=row['uuid'],values=(row['label'] or 'unavailable',
                        ', '.join(row['homes']),local,row['uuid']))
            run_registration(['discover','--group',group.get()],finish)
        def prepare_registration():
            chosen=list(listing.selection())
            if not chosen:
                messagebox.showinfo('Select exact histories','Select one or more unregistered histories first.',parent=window);return
            if not messagebox.askyesno('Prepare registration?',
                    f'Review {len(chosen)} exact UUID(s) as saved in {source.get()}? This preserves a checkpoint but does not register or move them.',parent=window):return
            args=['prepare','--group',group.get(),'--account',source.get()]
            for sid in chosen:args+=['--uuid',sid]
            def finish(data):
                item[0]=data if data.get('state')=='PREPARED_REVIEW_REQUIRED' else None
                if item[0]:messagebox.showinfo('Review required','Open the review before registering. No account map was changed.',parent=window)
            run_registration(args,finish)
        def open_review():
            if item[0]:os.startfile(item[0]['review'])
            else:messagebox.showinfo('No review','Prepare exact histories first.',parent=window)
        def apply_registration():
            prepared_item=item[0]
            if not prepared_item:
                messagebox.showinfo('No review','Prepare exact histories first.',parent=window);return
            if not messagebox.askyesno('Register reviewed histories?',
                    f"Register {prepared_item['sessions']} exact saved UUID(s) as {source.get()}?\n\nThis changes only routing records; it does not move history or open sessions.",parent=window):return
            def finish(data):
                item[0]=None
                if data.get('state')=='REGISTERED_SAVED_HOMES_NOT_REOPENED':
                    refresh()
                    messagebox.showinfo('Registered','Saved histories are now in the main table. Select them there, then prepare the separate account transfer.',parent=window)
            run_registration(['apply','--manifest',prepared_item['manifest'],'--sha256',prepared_item['sha256']],finish)
        actions=[]
        listing.bind('<<TreeviewSelect>>',lambda *_:item.__setitem__(0,None))
        for label,command in [('Discover / refresh',inventory),('Prepare registration',prepare_registration),
                              ('Open registration review',open_review),('Register reviewed UUIDs',apply_registration)]:
            button=ttk.Button(action,text=label,command=command);button.pack(side='left',padx=(0,8));actions.append(button)
        source.trace_add('write',lambda *_:item.__setitem__(0,None))
        inventory()

    ttk.Button(utilities,text='Unregistered sessions',command=open_registration).pack(side='left',padx=(0,8))
    ttk.Button(utilities,text='Account inventory / merge',command=open_inventory).pack(side='left',padx=(0,8))
    ttk.Button(utilities,text='Activity log',command=show_activity).pack(side='left',padx=(0,8))
    help_button=ttk.Button(utilities,text='Show button help');help_button.pack(side='right')
    help_frame=ttk.LabelFrame(frame,text='Button help',padding=9)
    help_text=(
        'Compare branches / choose survivor: select exactly one conversation and a different destination. Compare saved times, record counts, text previews and companion hashes. Choose the registered source to replace the destination, or keep the destination unchanged and cancel. No winner is preselected. Prepare preserves both main originals; open the review, acknowledge replacements, then Apply survivor. Changed evidence or live writers hold. No workspace opens, no prompt is sent, and source copies remain preserved.\n'
        'Top toolbar: Refresh data rereads the main table; Usage opens account snapshots; Reload App restarts only the idle switchboard from disk; Options saves display choices. Usage can sort by each bucket; missing readings sort last. Defaults are Fable ascending and a 95% strike threshold. Stale readings use gray backgrounds and absolute local reset times. Weekly above the threshold strikes the whole plan; 5-hour and Fable above it strike only those buckets. Strike-through is a display warning, not an account lock. Developer diagnostics expose no bypasses. BLUE is reserved and unassigned, not a configured login.\n'
        'Select all / Clear: select or clear visible conversation rows. Refresh table: reread the current-home register and saved history files; clears selection and any prepared review, but never moves history. Check readiness: check selected saved histories without moving them. Runtime diagnostics: identify live writers and unresolved PIDs; exact account login/status helpers are automatically excluded as non-writers, never terminated. Unknown writers remain HELD.\n'
        'Prepare transition (batch): scan the whole selection first and show ALL conflicts together. Already-destination rows need no transfer. Choose source survivors individually or by listed source color, or exclude rows. Open one combined review and Apply the included batch once. Choices are never automatic; changed histories require a rescan. Both originals are preserved, shared project memory is unchanged, and no workspace launches. Open destination workspace is a separate action: it opens the whole VS Code workspace after registration and saved-tab checks, not individual Claude tabs.\n'
        'Compare settings / skills: audit selected destination against canonical shared baseline. Prepare settings / Prepare skills: stage one regularization review. Open regularization review: inspect exact changes. Apply reviewed regularization: write only staged changes after confirmation.\n'
        'Unregistered sessions: discover saved histories missing from the current-home register, select exact UUIDs in their existing account, prepare and open a registration review, then register them. This does not transfer or open histories; use the main table afterward. Account inventory / merge: inventory all colors, compare a source/target pair, prepare selected categories, open the merge review, then apply the reviewed merge. Credentialed MCPs and permission conflicts stay held.\n'
        'Usage snapshot: fetch account-wide 5-hour, 7-day and Fable weekly usage. Last successful readings survive restarts and are labelled STALE after a failed check; stale reset times are absolute local times. On HTTP 401 or wrong-account login, Open sign-in starts only the named account login; use Switch account in the browser if needed, then Retry checks only that account. Unavailable is not zero.\n'
        'Reload App: close this idle switchboard and restart from the latest code and saved state. A running local operation blocks reload; prepared reviews and selections are discarded. No histories move.\n'
        'Activity log: see local commands, history-move progress, final states and artifact paths. The file is created with the first command; no credentials or transcript text are logged.\n'
        'Workspace: choose the conversation group (black text). Destination account: choose the isolated Claude account (its color identifies the selection). The variant and review checkboxes are required only for their respective applies.')
    help_label=tk.Text(help_frame,height=8,wrap='word',bg='#142431',fg='#d9e8ee',relief='flat',font=('Segoe UI',10))
    help_label.insert('1.0',help_text);help_label.configure(state='disabled');help_label.pack(fill='x')
    def toggle_help():
        if help_frame.winfo_manager():
            help_frame.pack_forget();help_button.configure(text='Show button help')
        else:
            help_frame.pack(fill='x',before=output,pady=(2,5));help_button.configure(text='Hide button help')
    help_button.configure(command=toggle_help)

    output = tk.Text(frame,height=10,bg='#081117',fg='#d9e8ee',insertbackground='#57c7ff',wrap='word',font=('Cascadia Mono',10),relief='flat',padx=10,pady=8)
    output.pack(fill='both',expand=True,pady=(5,0))
    group_box.bind('<<ComboboxSelected>>',refresh);color_box.bind('<<ComboboxSelected>>',refresh)
    tree.bind('<<TreeviewSelect>>',invalidate)
    def close():
        if not safe_to_close(busy[0], auxiliary_jobs[0]):
            messagebox.showinfo('Operation running','Wait for the local operation to finish. Closing now can interrupt an apply.');return
        root.destroy()
    root.protocol('WM_DELETE_WINDOW',close)
    def check_replacement():
        if gate and gate.replacement_requested() and safe_to_close(busy[0], auxiliary_jobs[0]):
            close()
            return
        root.after(200,check_replacement)
    if gate:
        root.after(200,check_replacement)
    refresh();show('Select exact conversations for a history move, or compare reviewed shared settings and skills for the selected account. Live sessions produce a stop report; they are never force-closed.')
    if preferences_warning:
        show(preferences_warning)
    if smoke:
        root.after(350,root.destroy)
    try:
        root.mainloop()
    finally:
        if reload_requested[0]:
            try:
                restart_application(gate)
            except (OSError,RuntimeError) as exc:
                ctypes.windll.user32.MessageBoxW(None, str(exc), 'Switchboard reload failed', 0x10)
        elif gate:
            gate.release()


if __name__=='__main__':main()
