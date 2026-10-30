"""One public listener; isolated profile workers using the same application build."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from http.client import HTTPConnection
from pathlib import Path
from urllib.parse import urlsplit,unquote
import html
import json
import os
import socket
import subprocess
import sys
import threading
import time

import profile_store

HOP={'connection','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','transfer-encoding','upgrade'}
BODY_LIMIT=16*1024*1024


def command():
    return [sys.executable] if getattr(sys,'frozen',False) else [sys.executable,str(Path(__file__).with_name('main_exe.py'))]


def route(path,profiles):
    parsed=urlsplit(path)
    if parsed.scheme or parsed.netloc: raise ValueError('Invalid request target')
    decoded=unquote(parsed.path)
    if decoded!=parsed.path or '\\' in decoded or any(p in ('.','..') for p in decoded.split('/')): raise ValueError('Invalid path')
    parts=decoded.strip('/').split('/')
    if parts[0]=='api' and len(parts)>=3:
        slug=parts[1];target='/'+ '/'.join(parts[2:])
        if parts[2] not in {'run','run_saturday','solve','tsp','tsp-report','health'}: raise ValueError('Unknown API endpoint')
    else:
        slug=parts[0];target=decoded
    if slug not in profiles or not profiles[slug].get('enabled',True): raise ValueError('Unknown profile')
    return slug,target+('?' + parsed.query if parsed.query else '')


class Supervisor:
    def __init__(self,root):
        self.root=Path(root).resolve();self.workers={};self.lock=threading.RLock()
        self.run_lock=threading.Lock();self.pending={};self.tsp_slots=threading.BoundedSemaphore(2)

    def config(self): return profile_store.read(self.root)

    def ensure(self,slug):
        with self.lock:
            item=self.workers.get(slug)
            if item and item['process'].poll() is None: return item['port']
            cfg=self.config()
            if slug not in cfg['profiles']: raise ValueError('Unknown profile')
            with socket.socket() as sock:
                sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
            directory=self.root/'profiles'/slug
            env=os.environ.copy();env.update(BIZANT_HOME=str(self.root),BIZANT_PROFILE=slug,
                BIZANT_PROFILE_DIR=str(directory),BIZANT_INTERNAL_PORT=str(port),BIZANT_PUBLIC_PORT=str(cfg['port']),
                BIZANT_PUBLIC_ORIGIN=cfg.get('public_url','http://127.0.0.1:'+str(cfg['port'])),
                BIZANT_WORKER_BUDGET=str(cfg.get('worker_budget',4)),PYINSTALLER_RESET_ENVIRONMENT='1',PYTHONUTF8='1')
            (directory/'logs').mkdir(exist_ok=True)
            stream=open(directory/'logs'/'profile_server.log','ab')
            process=subprocess.Popen(command()+['--server'],cwd=directory,env=env,stdout=stream,stderr=stream,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            stream.close();self.workers[slug]={'process':process,'port':port}
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError('Профилът не успя да стартира. Виж неговия лог.')
                try:
                    connection=HTTPConnection('127.0.0.1',port,timeout=.5)
                    connection.request('GET','/health');response=connection.getresponse();response.read();connection.close()
                    if response.status==200: return port
                except OSError: pass
                time.sleep(.2)
            raise RuntimeError('Профилът не отговори навреме.')

    def status(self,slug):
        item=self.workers.get(slug)
        if not item or item['process'].poll() is not None: return {'status':'offline','running':False}
        conn=HTTPConnection('127.0.0.1',item['port'],timeout=2)
        try:
            conn.request('GET','/health');response=conn.getresponse();data=json.loads(response.read())
            return {'status':data.get('status'),'running':bool(data.get('current_run',{}).get('running'))}
        except Exception: return {'status':'unavailable','running':False}
        finally: conn.close()

    def stop(self):
        for item in self.workers.values():
            p=item['process']
            if p.poll() is None:
                # This supervisor owns these PIDs, not the legacy installations.
                if os.name=='nt': subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True)
                else: p.terminate()


def landing(profiles):
    cards=''.join('<a class="card" href="/'+html.escape(slug)+'"><small>РАБОТНО ПРОСТРАНСТВО</small><h2>'+html.escape(v['name'])+'</h2><p>'+('Тестова инсталация · без изпращане' if v.get('trial_mode',True) else 'Официална среда')+'</p><p data-status="'+slug+'">Проверка…</p><span>Отвори →</span></a>' for slug,v in profiles.items() if v.get('enabled',True))
    return '''<!doctype html><html lang="bg"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Bizant · Профили</title>
    <style>*{box-sizing:border-box}body{margin:0;background:#f5f7fb;color:#152841;font:16px 'Segoe UI',sans-serif}header{background:#142942;color:white;padding:25px max(24px,calc((100vw - 1100px)/2))}header b{font-size:25px}main{max-width:1150px;margin:64px auto;padding:24px}h1{font-size:36px;margin:0 0 12px}main>p{color:#64748b;margin-bottom:32px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px}.card{display:block;text-decoration:none;color:inherit;background:white;padding:28px;border:1px solid #dce3ee;border-radius:16px;transition:.15s}.card:hover{transform:translateY(-3px);border-color:#185de0;box-shadow:0 12px 25px #13264010}.card small{font-size:11px;letter-spacing:1.5px;color:#708199}.card h2{font-size:28px;margin:18px 0}.card p{font-size:13px;color:#64748b}.card span{display:block;margin-top:35px;color:#185de0;font-weight:600}footer{margin-top:25px;color:#718096;font-size:13px}</style>
    <header><b>Bizant</b></header><main><h1>Избери профил</h1><p>Отделни настройки, заявки и резултати. Една програма.</p><div class="grid">'''+cards+'''</div><footer>Профили се добавят и копират от Settings. Достъпът до всеки е с неговия потребител и парола.</footer></main>
    <script>async function refresh(){try{const data=await(await fetch('/health',{cache:'no-store'})).json();for(const [key,value] of Object.entries(data.profiles)){const el=document.querySelector('[data-status="'+key+'"]');if(el){el.textContent=value.running?'● Изчислява':value.status==='ok'?'● Готовност':'○ Не е стартиран';el.style.color=value.running?'#185de0':value.status==='ok'?'#16804a':'#64748b'}}}catch(_){}setTimeout(refresh,5000)}refresh();</script></html>'''


class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def setup(self): super().setup();self.connection.settimeout(30)
    def log_message(self,*args): pass  # Never log query strings/API credentials.
    def send(self,status,body,kind='application/json; charset=utf-8'):
        raw=body.encode() if isinstance(body,str) else json.dumps(body,ensure_ascii=False).encode()
        self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
        if self.command!='HEAD': self.wfile.write(raw)

    def do_GET(self): self.forward()
    def do_POST(self): self.forward()
    def do_HEAD(self): self.forward()

    def forward(self):
        supervisor=self.server.supervisor
        cfg=supervisor.config()
        if self.path=='/' and self.command in ('GET','HEAD'): return self.send(200,landing(cfg['profiles']),'text/html; charset=utf-8')
        if self.path=='/health' and self.command in ('GET','HEAD'):
            return self.send(200,{'status':'ok','service':'Bizant profiles','profiles':{s:supervisor.status(s) for s in cfg['profiles']}})
        connection=None;slot=False;run_lock=False
        try:
            slug,target=route(self.path,cfg['profiles'])
            if self.headers.get('Transfer-Encoding'): return self.send(400,{'error':'Transfer-Encoding is not supported'})
            lengths=self.headers.get_all('Content-Length',[])
            if len(lengths)>1: return self.send(400,{'error':'Ambiguous Content-Length'})
            length=int(self.headers.get('Content-Length','0'))
            if length<0 or length>BODY_LIMIT: return self.send(413,{'error':'Заявката е прекалено голяма.'})
            body=self.rfile.read(length) if length else None
            if length and len(body)!=length: return self.send(400,{'error':'Incomplete body'})
            port=supervisor.ensure(slug)
            plain=urlsplit(target).path
            starting=plain in ('/run','/run_saturday','/solve',f'/{slug}/api/run')
            if starting:
                supervisor.run_lock.acquire();run_lock=True
                active=[s for s in cfg['profiles'] if supervisor.status(s)['running'] or supervisor.pending.get(s,0)>time.monotonic()]
                if slug in active or len(active)>=cfg.get('max_parallel_runs',1):
                    return self.send(409,{'error':'Вече има активно изчисление. Изчакай приключването му.'})
            if plain=='/tsp':
                slot=supervisor.tsp_slots.acquire(False)
                if not slot: return self.send(429,{'error':'TSP е зает. Опитай отново.'})
            headers={k:v for k,v in self.headers.items() if k.lower() not in HOP and not k.lower().startswith('x-forwarded-')}
            headers['Connection']='close'
            headers['X-Forwarded-For']=self.client_address[0]
            connection=HTTPConnection('127.0.0.1',port,timeout=900)
            connection.request(self.command,target,body=body,headers=headers)
            response=connection.getresponse();raw=response.read()
            if starting and response.status==202: supervisor.pending[slug]=time.monotonic()+5
            self.send_response(response.status)
            for key,value in response.getheaders():
                if key.lower() not in HOP|{'content-length','server','date'}: self.send_header(key,value)
            self.send_header('Content-Length',str(len(raw)));self.end_headers()
            if self.command!='HEAD': self.wfile.write(raw)
        except ValueError as exc: self.send(400,{'error':str(exc)})
        except (OSError,RuntimeError): self.send(503,{'error':'Профилът временно не е достъпен. Виж неговия лог.'})
        finally:
            if connection: connection.close()
            if slot: supervisor.tsp_slots.release()
            if run_lock: supervisor.run_lock.release()


def serve(root):
    supervisor=Supervisor(root);cfg=supervisor.config()
    server=ThreadingHTTPServer(('0.0.0.0',int(cfg['port'])),Handler);server.supervisor=supervisor
    # Start isolated workers on first access, not all at once. The landing page
    # and health checks must not allocate three solver runtimes unnecessarily.
    print('Bizant profiles ready on port '+str(cfg['port']),flush=True)
    try: server.serve_forever()
    finally: server.server_close();supervisor.stop()
