"""Explicit survivor UI. Never automatically selects a winner or opens a workspace."""
import datetime as dt
import os
import tkinter as tk
from tkinter import ttk, messagebox

from survivor_review import apply_arguments


INKS={'GREEN':'#78D6A0','YELLOW':'#FFE066','ORANGE':'#FFB278','PURPLE':'#D9B3FF','BLUE':'#89BEFF'}


def local_time(stamp):
    if not stamp:
        return 'unavailable'
    try:
        return dt.datetime.fromisoformat(stamp.replace('Z','+00:00')).astimezone().strftime('%b %d, %Y %I:%M %p local')
    except (ValueError,AttributeError):
        return 'unavailable'


class SurvivorDialog:
    def __init__(self,parent,group,destination,sid,run_command,on_success):
        self.run_command=run_command;self.on_success=on_success
        self.scope=['--group',group,'--destination',destination,'--uuid',sid]
        self.data=None;self.prepared=None;self.busy=False;self.review_opened=False
        self.window=tk.Toplevel(parent);self.window.title('Compare branches / choose survivor')
        self.window.geometry('1100x780');self.window.minsize(720,520)
        self.window.transient(parent);self.window.grab_set()
        body=ttk.Frame(self.window,padding=12);body.pack(fill='both',expand=True)
        body.columnconfigure(0,weight=1);body.columnconfigure(1,weight=1);body.rowconfigure(2,weight=1)
        self.title=ttk.Label(body,text='Comparing exact saved histories…',font=('Segoe UI Semibold',15))
        self.title.grid(row=0,column=0,columnspan=2,sticky='w')
        self.status=tk.StringVar(value='No winner selected. Comparison does not change histories.')
        ttk.Label(body,textvariable=self.status,wraplength=1000).grid(row=1,column=0,columnspan=2,sticky='ew',pady=6)
        self.panes=[]
        for column in range(2):
            pane=ttk.Frame(body);pane.grid(row=2,column=column,sticky='nsew',padx=(0,8) if column==0 else (8,0))
            pane.rowconfigure(0,weight=1);pane.columnconfigure(0,weight=1)
            text=tk.Text(pane,wrap='word',width=45,height=18,bg='#232521',fg='#F4F1DE',font=('Segoe UI',10))
            text.grid(row=0,column=0,sticky='nsew')
            bar=ttk.Scrollbar(pane,orient='vertical',command=text.yview);bar.grid(row=0,column=1,sticky='ns')
            text.configure(yscrollcommand=bar.set,state='disabled');self.panes.append(text)
        extra=ttk.Frame(body);extra.grid(row=3,column=0,columnspan=2,sticky='ew',pady=6)
        self.details=tk.Text(extra,height=5,wrap='word',bg='#232521',fg='#F4F1DE',font=('Segoe UI',9))
        self.details.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(extra,orient='vertical',command=self.details.yview);scroll.pack(side='right',fill='y')
        self.details.configure(yscrollcommand=scroll.set,state='disabled')
        self.choice=tk.StringVar(value='');self.reviewed=tk.BooleanVar(value=False);self.companions=tk.BooleanVar(value=False)
        self.source_radio=ttk.Radiobutton(body,text='Use registered source history in destination',variable=self.choice,value='source')
        self.source_radio.grid(row=4,column=0,columnspan=2,sticky='w')
        self.target_radio=ttk.Radiobutton(body,text='Keep destination unchanged / cancel transfer',variable=self.choice,value='target')
        self.target_radio.grid(row=5,column=0,columnspan=2,sticky='w')
        self.review_check=ttk.Checkbutton(body,text='I opened the prepared review and accept replacing the destination main history, not merging it.',variable=self.reviewed)
        self.review_check.grid(row=6,column=0,columnspan=2,sticky='w',pady=(6,0))
        self.companion_check=ttk.Checkbutton(body,text='I reviewed differing companions and accept the registered source versions; displaced bytes are backed up.',variable=self.companions)
        self.companion_check.grid(row=7,column=0,columnspan=2,sticky='w')
        footer=ttk.Frame(body);footer.grid(row=8,column=0,columnspan=2,sticky='ew',pady=(10,0))
        self.compare_button=ttk.Button(footer,text='Refresh comparison',command=self.compare);self.compare_button.pack(side='left')
        self.prepare_button=ttk.Button(footer,text='Prepare chosen survivor',command=self.prepare);self.prepare_button.pack(side='left',padx=6)
        self.review_button=ttk.Button(footer,text='Open prepared review',command=self.open_review);self.review_button.pack(side='left')
        self.apply_button=ttk.Button(footer,text='Apply survivor',command=self.apply);self.apply_button.pack(side='left',padx=6)
        self.close_button=ttk.Button(footer,text='Close',command=self.close);self.close_button.pack(side='right')
        self.choice.trace_add('write',self.choice_changed)
        self.reviewed.trace_add('write',lambda *_:self.controls())
        self.companions.trace_add('write',lambda *_:self.controls())
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        self.compare()

    def controls(self):
        ready=not self.busy
        for button,enabled in [(self.compare_button,ready),(self.close_button,ready),
                (self.source_radio,ready and bool(self.data)),(self.target_radio,ready and bool(self.data)),
                (self.prepare_button,ready and bool(self.data) and self.choice.get()=='source'),
                (self.review_button,ready and bool(self.prepared)),
                (self.review_check,ready and bool(self.prepared) and self.review_opened),
                (self.companion_check,ready and bool(self.prepared) and bool(self.prepared.get('companion_variants'))),
                (self.apply_button,ready and bool(self.prepared) and self.choice.get()=='source'
                 and self.review_opened and self.reviewed.get()
                 and (not self.prepared.get('companion_variants') or self.companions.get()))]:
            button.configure(state='normal' if enabled else 'disabled')

    def choice_changed(self,*_):
        self.prepared=None;self.review_opened=False;self.reviewed.set(False);self.companions.set(False)
        self.controls()

    def execute(self,script,args,callback):
        if self.busy:return
        self.busy=True;self.controls();self.status.set('Working locally. Keep both copies closed; no prompts are sent.')
        def done(result):
            self.busy=False
            if result.get('state')=='HELD':
                self.prepared=None;self.data=None;self.review_opened=False
                self.status.set('HELD: '+result.get('error','Unavailable. Compare again.'))
            else:callback(result)
            self.controls()
        try:
            self.run_command(script,args,done)
        except Exception as exc:
            done({'state':'HELD','error':str(exc)})

    def compare(self):
        self.data=None;self.choice.set('')
        def done(result):
            if result.get('state')!='BRANCH_COMPARISON_READY':
                self.status.set('Comparison unavailable; no changes made.');return
            self.data=result;self.title.configure(text=result['label']+' | '+result['uuid'])
            src,dst=result['source_color'],result['destination']
            self.source_radio.configure(text=f'Use {src} history in {dst} (replace, not merge)')
            self.target_radio.configure(text=f'Keep {dst} unchanged / cancel transfer (does not change the registered home)')
            self.status.set(f"Shared exact prefix: {result['common_records']} records. No automatic winner. Times are saved-record timestamps, not completion proof.")
            for widget,side in zip(self.panes,('source','target')):
                r=result[side]
                lines=[r['color']+' | '+r['email']+(' | REGISTERED CURRENT' if r['registered'] else ' | DESTINATION COPY'),
                       'Last user text: '+local_time(r['last_prompt']), 'Last assistant output: '+local_time(r['last_output']),
                       f"Records: {r['records']} | after first difference: {r['branch_records']}",
                       f"User-text records: {r['user_text_records']} | assistant-text records: {r['assistant_text_records']}",
                       f"Tool calls: {r['tool_calls']} | tool results: {r['tool_results']}",
                       'User-text records may include automated or summary messages, not just human prompts.',
                       'SHA256: '+r['sha256'],'History: '+r['path'],'',
                       f"Recent branch text previews ({len(r['previews'])} of {r['preview_total']}; latest 8, 800 characters each):"]
                for p in r['previews']:
                    lines += ['',p['type'].upper()+' | '+local_time(p['timestamp']),p['text']+(' [truncated]' if p['truncated'] else '')]
                widget.configure(state='normal',fg=INKS.get(r['color'],'#F4F1DE'));widget.delete('1.0','end')
                widget.insert('end','\n'.join(lines));widget.configure(state='disabled')
            lines=['Companion differences (not automatically merged):']
            for r in result['companions']:
                lines += [r['state']+': '+r['relative'],
                          '  Source SHA256: '+(r['source_sha256'] or 'absent'),
                          '  Destination SHA256: '+(r['target_sha256'] or 'absent')]
            if not result['companions']:lines += ['None observed.']
            lines += ['Prepare preserves both main histories in a local backup bundle. Destination-only companions are retained.',
                      'Shared project memory is excluded. Old source copies are NOT retired from native pickers by this operation.']
            self.details.configure(state='normal');self.details.delete('1.0','end');self.details.insert('end','\n'.join(lines));self.details.configure(state='disabled')
        self.execute('survivor_review.py',['compare']+self.scope,done)

    def prepare(self):
        if not self.data or self.choice.get()!='source' or self.busy:return
        self.prepared=None;self.review_opened=False;self.reviewed.set(False);self.companions.set(False)
        def done(result):
            if result.get('state')=='PREPARED_REVIEW_REQUIRED':
                self.prepared=result
                self.status.set('Prepared only. Open the review, then confirm. Backup bundle: '+result['backups'])
        self.execute('survivor_review.py',['prepare']+self.scope+['--token',self.data['token']],done)

    def open_review(self):
        if not self.prepared or self.busy:return
        try:
            os.startfile(self.prepared['review'])
            self.review_opened=True;self.controls()
        except OSError as exc:
            self.status.set('Cannot open review: '+str(exc))

    def apply(self):
        if not self.prepared or not self.data or self.busy:return
        try:
            args=apply_arguments(self.prepared,self.choice.get(),self.review_opened and self.reviewed.get(),self.companions.get())
        except RuntimeError as exc:
            self.status.set(str(exc));return
        src,dst=self.data['source_color'],self.data['destination']
        if not messagebox.askyesno('Replace destination with selected survivor?',
                f"{self.data['label']}\n{self.data['uuid']}\n\nUse {src} history in {dst}?\n"
                'Destination-only main conversation content will not be in the active survivor. Both originals are backed up. '
                'This is not a merge. No workspace will open and no prompt will be sent.',parent=self.window):return
        def done(result):
            self.prepared=None;self.data=None
            self.status.set(result.get('state','Unavailable')+'. No workspace launch requested.')
            if result.get('state')=='SWITCH_COMPLETE_SAVED_HISTORIES_READY':self.on_success(result)
        self.execute('switcher.py',args,done)

    def close(self):
        if self.busy:
            self.status.set('Wait for the local operation to finish before closing.');return False
        self.window.grab_release();self.window.destroy();return True
