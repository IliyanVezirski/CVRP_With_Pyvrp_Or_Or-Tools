"""Profile selector embedded in the existing desktop Settings window."""
from copy import deepcopy
from pathlib import Path
import json
import os
import subprocess
import sys
import tkinter as tk
from tkinter import ttk,messagebox,simpledialog

import profile_runtime as runtime
import profile_store


def prepare(app):
    app._settings_mode=os.environ.get('BIZANT_SETTINGS_MODE','global')
    app._file_revision=runtime.config_hash()
    if app._settings_mode=='web':
        from operations_web import profile_config
        app.cfg=profile_config(app.cfg,'web')


def attach(app):
    if not runtime.profile_dir(): return
    cfg=profile_store.read(runtime.home());slug=os.environ['BIZANT_PROFILE']
    bar=ttk.Frame(app.root,style='Toolbar.TFrame',padding=(18,10))
    bar.pack(side='top',fill='x',before=app.root.winfo_children()[0])
    ttk.Label(bar,text='Профил',style='Surface.TLabel').pack(side='left',padx=(0,8))
    choices={entry['name']+'  /'+key:key for key,entry in cfg['profiles'].items()}
    profile=tk.StringVar(value=next(label for label,key in choices.items() if key==slug))
    selector=ttk.Combobox(bar,textvariable=profile,values=list(choices),state='readonly',width=26);selector.pack(side='left')
    mode=tk.StringVar(value='Тестови' if app._settings_mode=='web' else 'Основни')
    selector_mode=ttk.Combobox(bar,textvariable=mode,values=['Основни','Тестови'],state='readonly',width=12);selector_mode.pack(side='left',padx=10)
    def switch(_=None):
        target=choices[profile.get()];selected='web' if mode.get()=='Тестови' else 'global'
        if app.is_dirty:
            answer=messagebox.askyesnocancel('Незаписани промени','Да запазя ли промените преди смяната?',parent=app.root)
            if answer is None or (answer and not app._save_config(False)):
                profile.set(next(k for k,v in choices.items() if v==slug));mode.set('Тестови' if app._settings_mode=='web' else 'Основни');return
        from profile_server import command
        env=os.environ.copy();env.update(BIZANT_PROFILE=target,BIZANT_PROFILE_DIR=str(runtime.home()/'profiles'/target),BIZANT_SETTINGS_MODE=selected,PYINSTALLER_RESET_ENVIRONMENT='1')
        env.pop('BIZANT_INTERNAL_PORT',None)
        subprocess.Popen(command()+['--settings'],cwd=runtime.home(),env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        app.root.destroy()
    selector.bind('<<ComboboxSelected>>',switch);selector_mode.bind('<<ComboboxSelected>>',switch)
    def add(copy_current):
        name=simpledialog.askstring('Нов профил','Име на фирмата:',parent=app.root)
        if not name: return
        new_slug=simpledialog.askstring('Нов профил','Кратък адрес с латински букви:',parent=app.root)
        if not new_slug: return
        username=simpledialog.askstring('Web достъп','Потребител за новия профил:',parent=app.root)
        if not username: return
        password=simpledialog.askstring('Web достъп','Нова парола:',show='*',parent=app.root)
        if not password: return
        repeat=simpledialog.askstring('Web достъп','Повтори паролата:',show='*',parent=app.root)
        if password!=repeat: messagebox.showerror('Парола','Паролите не съвпадат.',parent=app.root);return
        try:
            source=runtime.profile_dir() if copy_current else runtime.home()/'template'
            profile_store.create(runtime.home(),new_slug,name,source,username=username,password=password)
            label=name+'  /'+new_slug;choices[label]=new_slug;selector['values']=list(choices)
            messagebox.showinfo('Готово','Профилът е създаден в тестов режим, без изпращане. Избери го от списъка.',parent=app.root)
        except Exception as exc: messagebox.showerror('Профил',str(exc),parent=app.root)
    ttk.Button(bar,text='+ Нов профил',command=lambda:add(False)).pack(side='left',padx=4)
    ttk.Button(bar,text='Копирай профила',command=lambda:add(True)).pack(side='left',padx=4)
    safety=tk.StringVar(value='Без изпращане' if runtime.metadata().get('trial_mode',True) else 'Реално изпращане')
    def toggle_safety():
        trial=runtime.metadata().get('trial_mode',True)
        text=('Да разреша ли реално SetData и качване на карти за '+cfg['profiles'][slug]['name']+'? Ще се използват неговите записани настройки и изходни папки.' if trial else 'Да включа ли тестовата защита срещу изпращане?')
        if messagebox.askyesno('Режим на профила',text,parent=app.root):
            profile_store.set_trial(runtime.home(),slug,not trial)
            safety.set('Реално изпращане' if trial else 'Без изпращане')
    ttk.Button(bar,textvariable=safety,command=toggle_safety).pack(side='right')
    app.root.title('Bizant Settings — '+cfg['profiles'][slug]['name']+' — '+mode.get())
    for key in ('api.api_host','api.api_port','api.api_public_url','api.web_gui_endpoint','api.web_gui_public_url'):
        control=app.controls.get(key)
        if control:
            try: control.configure(state='disabled')
            except tk.TclError: pass
    if app._settings_mode=='web':
        from cvrp_api_server import _WEB_RUN_SECTION_FIELDS
        allowed={s+'.'+f for s,fields in _WEB_RUN_SECTION_FIELDS.items() for f in fields}
        for key,control in app.controls.items():
            if key not in allowed:
                try: control.configure(state='disabled')
                except tk.TclError: pass
        app.status_var.set('Тестов профил. Общите бусове, локации и изключения се редактират в „Основни“.')


def save(app,values,original):
    with runtime.CONFIG_LOCK:
        if runtime.config_hash()!=getattr(app,'_file_revision',runtime.config_hash()):
            raise ValueError('Настройките са променени от Web или друг Settings прозорец. Отвори профила отново преди запис.')
        if getattr(app,'_settings_mode','global')=='web':
            import cvrp_api_server as api
            # Reload the base before persisting only the test-profile whitelist.
            api._reload_config_from_disk()
            changes={}
            for section,fields in api._WEB_RUN_SECTION_FIELDS.items():
                row={key:values[section+'.'+key] for key in fields if section+'.'+key in values and values[section+'.'+key]!=app._saved_snapshot.get(section+'.'+key)}
                for key,value in list(row.items()):
                    current=getattr(getattr(app.cfg,section),key)
                    if isinstance(current,bool): row[key]=bool(value)
                    elif isinstance(current,int):
                        number=float(str(value).replace(',','.'))
                        if not number.is_integer(): raise ValueError(section+'.'+key+': въведи цяло число.')
                        row[key]=int(number)
                    elif isinstance(current,float): row[key]=float(str(value).replace(',','.'))
                    elif isinstance(current,list): row[key]=api._parse_string_list(value)
                    elif current is None and value=='': row[key]=None
                if row: changes[section]=row
            shared_changes=[key for key in values if values[key]!=app._saved_snapshot.get(key) and ('.' not in key or key.split('.',1)[1] not in api._WEB_RUN_SECTION_FIELDS.get(key.split('.',1)[0],set()))]
            if shared_changes: raise ValueError('Общите настройки се редактират в режим „Основни“: '+', '.join(shared_changes[:4]))
            if changes: api._save_web_run_defaults({'run_settings':changes})
            changed=bool(changes)
        else:
            changed=original(values)
        app._file_revision=runtime.config_hash()
        return changed
