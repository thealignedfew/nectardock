"""One scan, explicit survivor decisions, one reviewed batch. Never automatically opens sessions."""
import json
import os
import tkinter as tk
from tkinter import ttk,messagebox

from batch_transition import decisions
from survivor_dialog import INKS,local_time


class BatchDialog:
    def __init__(self,parent,group,destination,ids,run_command,on_success):
        self.run_command=run_command;self.on_success=on_success;self.destination=destination
        self.scope=['--group',group,'--destination',destination]
        for sid in ids:self.scope+=['--uuid',sid]
        self.data=None;self.prepared=None;self.choices={};self.busy=False;self.review_opened=False
        self.window=tk.Toplevel(parent);self.window.title('Review batch transition');self.window.geometry('1320x860')
        self.window.minsize(850,650);self.window.transient(parent);self.window.grab_set()
        body=ttk.Frame(self.window,padding=12);body.pack(fill='both',expand=True)
        ttk.Label(body,text=f'{group} → {destination} | Batch transition',font=('Segoe UI Semibold',17)).pack(anchor='w')
        self.status=tk.StringVar(value='Scanning all selected conversations before preservation.')
        ttk.Label(body,textvariable=self.status,wraplength=1250).pack(fill='x',pady=5)
        grid=ttk.Frame(body);grid.pack(fill='both',expand=True)
        grid.rowconfigure(0,weight=1);grid.columnconfigure(0,weight=1)
        columns=('name','source','state','choice','source_time','target_time','source_extra','target_extra','companions')
        self.tree=ttk.Treeview(grid,columns=columns,show='headings',selectmode='browse',height=8)
        labels=('Conversation','From','Result','Choice','Source output (local)','Destination output (local)','Source branch','Dest. branch','Companion differences')
        for key,label,width in zip(columns,labels,(220,80,160,120,205,205,100,100,130)):
            self.tree.heading(key,text=label);self.tree.column(key,width=width,minwidth=65,stretch=False)
        self.tree.grid(row=0,column=0,sticky='nsew')
        vertical=ttk.Scrollbar(grid,orient='vertical',command=self.tree.yview);vertical.grid(row=0,column=1,sticky='ns')
        horizontal=ttk.Scrollbar(grid,orient='horizontal',command=self.tree.xview);horizontal.grid(row=1,column=0,sticky='ew')
        self.tree.configure(yscrollcommand=vertical.set,xscrollcommand=horizontal.set)
        self.bulk=ttk.Frame(body);self.bulk.pack(fill='x',pady=5)
        self.bulk_buttons=[]
        actions=ttk.Frame(body);actions.pack(fill='x')
        self.source_button=ttk.Button(actions,text='Use registered source for selected row',command=lambda:self.choose_selected('source'));self.source_button.pack(side='left')
        self.exclude_button=ttk.Button(actions,text='Exclude selected row',command=lambda:self.choose_selected('skip'));self.exclude_button.pack(side='left',padx=6)
        self.clear_button=ttk.Button(actions,text='Reset selected choice',command=lambda:self.choose_selected(None));self.clear_button.pack(side='left')
        ttk.Label(body,text='Click a row for the saved comparison. No new scan is run. Bulk choices preserve exclusions; excluded histories and registration stay unchanged.').pack(anchor='w',pady=5)
        panes=ttk.Frame(body);panes.pack(fill='both',expand=True);panes.columnconfigure(0,weight=1);panes.columnconfigure(1,weight=1);panes.rowconfigure(0,weight=1)
        self.panes=[]
        for column in range(2):
            frame=ttk.Frame(panes);frame.grid(row=0,column=column,sticky='nsew',padx=4);frame.rowconfigure(0,weight=1);frame.columnconfigure(0,weight=1)
            text=tk.Text(frame,wrap='word',height=12,bg='#232521',fg='#F4F1DE',font=('Segoe UI',10));text.grid(row=0,column=0,sticky='nsew')
            bar=ttk.Scrollbar(frame,orient='vertical',command=text.yview);bar.grid(row=0,column=1,sticky='ns');text.configure(yscrollcommand=bar.set,state='disabled');self.panes.append(text)
        ttk.Label(body,text='Source survivors replace, not merge. Both main originals are backed up. Shared memory is unchanged. Old source copies remain preserved.').pack(anchor='w',pady=5)
        self.reviewed=tk.BooleanVar(value=False);self.companions=tk.BooleanVar(value=False)
        self.review_check=ttk.Checkbutton(body,text='I opened the combined review and accept its listed transfers and exact survivor replacements.',variable=self.reviewed);self.review_check.pack(anchor='w')
        self.companion_check=ttk.Checkbutton(body,text='I accept the listed source companion versions; displaced destination bytes are preserved.',variable=self.companions);self.companion_check.pack(anchor='w')
        footer=ttk.Frame(body);footer.pack(fill='x',pady=8)
        self.scan_button=ttk.Button(footer,text='Rescan selection',command=self.scan);self.scan_button.pack(side='left')
        self.prepare_button=ttk.Button(footer,text='Prepare combined review',command=self.prepare);self.prepare_button.pack(side='left',padx=6)
        self.review_button=ttk.Button(footer,text='Open combined review',command=self.open_review);self.review_button.pack(side='left')
        self.apply_button=ttk.Button(footer,text='Apply reviewed batch',command=self.apply);self.apply_button.pack(side='left',padx=6)
        self.close_button=ttk.Button(footer,text='Close',command=self.close);self.close_button.pack(side='right')
        self.tree.bind('<<TreeviewSelect>>',lambda *_:self.display_selected())
        self.reviewed.trace_add('write',lambda *_:self.controls());self.companions.trace_add('write',lambda *_:self.controls())
        self.window.protocol('WM_DELETE_WINDOW',self.close);self.scan()

    def controls(self):
        can_prepare=False
        if self.data:
            try:can_prepare=bool(decisions(self.data['rows'],self.choices)[0])
            except RuntimeError:pass
        ready=not self.busy
        for widget,enabled in [(self.scan_button,ready),(self.close_button,ready),
                (self.prepare_button,ready and can_prepare),(self.review_button,ready and bool(self.prepared)),
                (self.review_check,ready and bool(self.prepared) and self.review_opened),
                (self.companion_check,ready and bool(self.prepared) and bool(self.prepared.get('companion_variants'))),
                (self.apply_button,ready and bool(self.prepared) and self.review_opened and self.reviewed.get()
                 and (not self.prepared.get('companion_variants') or self.companions.get()))]:
            widget.configure(state='normal' if enabled else 'disabled')
        for widget in [self.source_button,self.exclude_button,self.clear_button]+self.bulk_buttons:
            widget.configure(state='normal' if ready and self.data else 'disabled')

    def invalidate(self):
        self.prepared=None;self.review_opened=False;self.reviewed.set(False);self.companions.set(False)

    def execute(self,script,args,callback):
        if self.busy:return
        self.busy=True;self.controls();self.status.set('Working locally. Keep included conversations closed. Progress is also recorded in Activity log.')
        def done(result):
            self.busy=False
            if result.get('state')=='HELD':
                self.data=None;self.invalidate();self.status.set('HELD: '+result.get('error','Unavailable')+' Rescan before another attempt.')
            else:callback(result)
            self.controls()
        try:self.run_command(script,args,done)
        except Exception as exc:done({'state':'HELD','error':str(exc)})

    def scan(self):
        if self.busy:return
        self.data=None;self.choices={};self.invalidate();self.tree.delete(*self.tree.get_children())
        def done(result):
            if result.get('state')!='BATCH_SCAN_READY':return
            self.data=result
            for widget in self.bulk.winfo_children():widget.destroy()
            self.bulk_buttons=[]
            for color in sorted({r['source_color'] for r in result['rows'] if r['state']=='CONFLICT'}):
                button=ttk.Button(self.bulk,text=f'Use {color} for all its listed conflicts',command=lambda c=color:self.choose_color(c));button.pack(side='left',padx=4);self.bulk_buttons.append(button)
            for row in result['rows']:self.tree.insert('', 'end',iid=row['uuid'],values=self.values(row))
            conflicts=sum(r['state']=='CONFLICT' for r in result['rows']);held=sum(r['state']=='HELD' for r in result['rows']);already=sum(r['state']=='ALREADY_DESTINATION' for r in result['rows'])
            self.status.set(f"Scan complete: {len(result['rows'])} rows, {conflicts} conflicts, {held} held, {already} already in {self.destination}. Choose or exclude every conflict; then prepare once.")
        self.execute('batch_transition.py',['scan']+self.scope,done)

    def values(self,row):
        choice=self.choices.get(row['uuid'])
        label={'source':'Use source','skip':'Excluded','move':'Transfer'}.get(choice,'Needs choice' if row['state']=='CONFLICT' else 'Transfer' if row['state']=='READY' else 'No transfer' if row['state']=='ALREADY_DESTINATION' else 'Needs exclusion')
        return (row['label'],row['source_color'],row['state'],label,local_time(row.get('source',{}).get('last_output')),
                local_time(row.get('target',{}).get('last_output')),row.get('source',{}).get('branch_records',''),
                row.get('target',{}).get('branch_records',''),len(row.get('companions',[])))

    def set_choice(self,sid,choice):
        if self.busy or not self.data:return
        row=next(r for r in self.data['rows'] if r['uuid']==sid)
        if row['state']=='ALREADY_DESTINATION':return
        if choice=='source':
            if row['state']=='HELD':return
            choice='source' if row['state']=='CONFLICT' else 'move'
        if choice is None:self.choices.pop(sid,None)
        else:self.choices[sid]=choice
        self.invalidate();self.tree.item(sid,values=self.values(row));self.controls()

    def choose_selected(self,choice):
        for sid in self.tree.selection():self.set_choice(sid,choice)

    def choose_color(self,color):
        if self.data and not self.busy:
            for row in self.data['rows']:
                if row['state']=='CONFLICT' and row['source_color']==color and self.choices.get(row['uuid'])!='skip':
                    self.set_choice(row['uuid'],'source')

    def display_selected(self):
        if not self.data or not self.tree.selection():return
        row=next(r for r in self.data['rows'] if r['uuid']==self.tree.selection()[0])
        for widget,side in zip(self.panes,('source','target')):
            data=row.get(side,{})
            lines=[row['label']+' | '+row['uuid'],row['state']+' | '+row.get('reason',''),
                   data.get('color',side)+' | '+data.get('email',''),
                   'Last user text: '+local_time(data.get('last_prompt')),'Last assistant output: '+local_time(data.get('last_output')),
                   f"Records: {data.get('records','unavailable')} | Shared prefix: {row.get('common_records','unavailable')}",
                   'Records are not human prompt counts; user text can include automated ticks.',
                   'History: '+data.get('path','unavailable'),'SHA256: '+data.get('sha256','unavailable'),
                   f"Latest branch text previews: {len(data.get('previews',[]))} of {data.get('preview_total',0)} (800 characters each)"]
            for preview in data.get('previews',[]):lines += ['',preview['type']+' | '+local_time(preview['timestamp']),preview['text']+(' [truncated]' if preview['truncated'] else '')]
            lines += ['','Companion differences (destination-only files retained):']
            for companion in row.get('companions',[]):lines += [companion['state']+': '+companion['relative'],str(companion.get('source_sha256' if side=='source' else 'target_sha256') or 'absent')]
            widget.configure(state='normal',fg=INKS.get(data.get('color'),'#F4F1DE'));widget.delete('1.0','end');widget.insert('end','\n'.join(lines));widget.configure(state='disabled')

    def prepare(self):
        if self.busy or not self.data:return
        try:
            if not decisions(self.data['rows'],self.choices)[0]:return
        except RuntimeError as exc:self.status.set(str(exc));return
        self.invalidate()
        def done(result):
            if result.get('state')=='PREPARED_REVIEW_REQUIRED':
                self.prepared=result;self.status.set(f"Prepared {result['sessions']} conversations in ONE review; {result.get('reused_snapshots',0)} duplicate captures reused. Open the review, acknowledge it, then Apply once.")
            else:self.status.set(result.get('state','Unavailable'))
        self.execute('batch_transition.py',['prepare','--review',self.data['review'],'--sha256',self.data['sha256'],
                                          '--choices',json.dumps(self.choices,sort_keys=True)],done)

    def open_review(self):
        if self.prepared and not self.busy:
            try:os.startfile(self.prepared['review']);self.review_opened=True;self.controls()
            except OSError as exc:self.status.set('Cannot open review: '+str(exc))

    def apply(self):
        if self.busy or not self.prepared or not self.review_opened or not self.reviewed.get():return
        item=self.prepared
        if item.get('companion_variants') and not self.companions.get():return
        if not messagebox.askyesno('Apply the exact reviewed batch?',
                f"Transfer {item['sessions']} included conversations to {self.destination}, including {item.get('survivor_main_count',0)} explicit main-history replacements?\n\n"
                'Excluded rows stay unchanged. Both main originals are preserved. No workspace opens and no prompt is sent.',parent=self.window):return
        args=['apply','--manifest',item['manifest'],'--sha256',item['sha256'],'--accept-source-main-survivor']
        if self.companions.get():args.append('--accept-companion-variants')
        def done(result):
            self.data=None;self.invalidate();self.status.set(result.get('state','Unavailable')+'. No workspace launch requested.')
            if result.get('state')=='SWITCH_COMPLETE_SAVED_HISTORIES_READY':self.on_success(result)
        self.execute('switcher.py',args,done)

    def close(self):
        if self.busy:self.status.set('Wait for the local operation to finish before closing.');return False
        self.window.grab_release();self.window.destroy();return True
