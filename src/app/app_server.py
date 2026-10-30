"""Independent counseling lab with raw contexts, workflow, and durable logs."""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit

from project_paths import PROJECT_ROOT
HERE = PROJECT_ROOT
from resources.history_cases import load_catalog
import client.registry as experiment_versions
import resources.contrast_profiles as contrast_profiles
import runtime.paired_lab as paired_lab
import storage.file_manager as file_manager
import storage.session_browser as session_browser
import app.prompt_files as prompt_files
from runtime import client_runtime as runtime


def load_group1_profiles():
    profiles = []
    for number in ('001', '002', '005', '007', '008'):
        filename = 'gold_ccd_profile_' + number + '.json'
        profile = json.loads((HERE / 'resources/profiles/group1' / filename).read_text(encoding='utf-8'))
        if profile.get('profile_id') != 'gold_' + number:
            raise ValueError('프로필 번호가 파일명과 일치하지 않습니다: ' + filename)
        profiles.append({'number': number, 'filename': filename, 'profile': profile})
    return {'group': 'group1', 'profiles': profiles}


def make_server(config_path=HERE/'config/app.json',host=None,port=8891,storage_dir=None,mode='dynamic',experiment='counseling',log_factory=None):
    initial = json.loads(Path(config_path).read_text())
    destination = Path(storage_dir) if storage_dir is not None else file_manager.storage_path(initial.get('storage_dir', str(HERE/'runs')), config_path)
    spec = importlib.util.spec_from_file_location('nested_backend_'+mode,HERE/'src/app/server.py')
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    if log_factory is not None:
        backend.TurnLog = log_factory(backend.TurnLog)
    original_read_config = backend.read_config
    def read_config(path):
        config = original_read_config(path)
        config['_delegation_experiment'] = experiment
        config['scenario'] = runtime.load_scenario(config)
        config['filesystem'] = {'enabled': False}
        config['_storage_dir'] = str(server.storage_dir) if 'server' in locals_server else str(destination)
        return config
    backend.read_config = read_config
    locals_server = {}
    base_build = backend.build_payload
    backend.build_payload = lambda config,body,tools=None,preview=False: runtime.build(config,body,tools or [],base_build,mode,preview=preview)
    backend.run_turn = lambda *args: runtime.execute(*args,base_build=base_build,mode=mode)

    class PairedTurnLog(backend.TurnLog):
        def append(self, event):
            if event['type'] == 'agent_event':
                self.state.setdefault('subagent_events', []).append(copy.deepcopy(event))
            return super().append(event)
    backend.PairedTurnLog = PairedTurnLog
    class AgentLog(backend.TurnLog):
        def __init__(self, root, body):
            self.version = experiment_versions.name({}, body)
            if body.get('session_key'):
                root, _, _ = session_browser.resolve(server, body['session_key'])
            super().__init__(Path(root)/self.version, body)
            self.state['experiment_version'] = self.version
        def append(self,event):
            event=copy.deepcopy(event)
            event.setdefault('mode',mode)
            event.setdefault('experiment',experiment)
            event.setdefault('experiment_version', self.version)
            if event['type']!='agent_event':
                event.setdefault('agent_id','main');event.setdefault('agent_name','메인');event.setdefault('depth',0)
            if event['type']=='agent_event':
                self.state.setdefault('subagent_events',[]).append(copy.deepcopy(event))
            if event['type']=='done':
                for key in ('main_usage','subagent_usage','agent_usage'):self.state[key]=event.get(key,{})
                for key in ('model_response', 'inner_state', 'state_after', 'summary_usage'):
                    if key in event:self.state[key]=copy.deepcopy(event[key])
            if event['type'] in ('state_summary','state_record'):
                self.state.setdefault('state_events', []).append(copy.deepcopy(event))
            self.state['mode']=mode
            self.state['experiment']=experiment
            return super().append(event)
    backend.TurnLog=AgentLog

    class Handler(backend.Handler):
        def json_response(self,data,status=200):
            if getattr(self, 'path', None)=='/api/tools' and 'tools' in data:
                config=backend.read_config(self.server.config_path)
                data={**data,'nested_mode':mode,'max_depth':None, 'enabled':True, 'max_calls':config.get('delegation', {}).get('max_calls', 8),
                      **runtime.settings(config)}
            if getattr(self, 'path', None)=='/api/config' and status==200:data={**data,'nested_mode':mode,'experiment':experiment}
            return super().json_response(data,status)
        def do_GET(self):
            import app.client_notes as client_notes
            if client_notes.handle_get(self):return
            if prompt_files.handle_get(self):return
            if session_browser.handle_get(self):return
            if file_manager.handle_get(self):return
            if self.path.startswith('/api/paired/'):
                try:
                    if paired_lab.handle_get(self):return
                except Exception as error:
                    self.json_response({'error': str(error)}, 400)
                    return
            if self.path == '/api/versions':
                self.json_response(experiment_versions.catalog(backend.read_config(self.server.config_path)))
                return
            if self.path == '/api/histories':
                try:
                    config=backend.read_config(self.server.config_path)
                    mode=config.get('profile_context_mode','selective')
                    data=load_catalog(config['scenario'],runtime.validate_scenario,mode)
                    data['profiles'].extend(contrast_profiles.histories(config['scenario'],runtime.validate_scenario,mode))
                    self.json_response(data)
                except (OSError,ValueError,KeyError) as error:
                    self.json_response({'error':'실험 프로필 조회 실패: '+str(error)},500)
                return
            if self.path == '/api/profiles':
                try:
                    data=load_group1_profiles()
                    for entry in data['profiles']:
                        entry['source']='resources/profiles/group1/'+entry['filename']
                        entry['group']='group1'
                    data['profiles'].extend(contrast_profiles.profiles())
                    data['group']='group1_and_trust_contrast'
                    self.json_response(data)
                except (OSError,ValueError,KeyError) as error:
                    self.json_response({'error':'프로필 조회 실패: '+str(error)},500)
                return
            if self.path == '/api/histories/group1':
                try:
                    config = backend.read_config(self.server.config_path)
                    self.json_response(load_catalog(config['scenario'], runtime.validate_scenario,
                                                    config.get('profile_context_mode', 'selective')))
                except (OSError, ValueError, KeyError) as error:
                    self.json_response({'error': '그룹1 히스토리 조회 실패: ' + str(error)}, 500)
                return
            if self.path == '/api/profiles/group1':
                try:
                    self.json_response(load_group1_profiles())
                except (OSError, ValueError) as error:
                    self.json_response({'error': '그룹1 프로필 조회 실패: ' + str(error)}, 500)
                return
            assets={'/':('workspace.html','text/html; charset=utf-8'),
                    '/lab':('index.html','text/html; charset=utf-8'),
                    '/master_controller.js':('master_controller.js','text/javascript'),
                    '/workspace.js':('workspace.js','text/javascript'),'/workspace.css':('workspace.css','text/css'),
                    '/pane_storage.js':('pane_storage.js','text/javascript'),
                    '/pane.js':('pane.js','text/javascript'),'/pane.css':('pane.css','text/css'),
                    '/app.js':('app.js','text/javascript'),
                    '/profiles.js':('profiles.js','text/javascript'),
                    '/client_notes.js':('client_notes.js','text/javascript'),'/client_notes.css':('client_notes.css','text/css'),
                    '/http.js':('http.js','text/javascript'),
                    '/workflow.js':('workflow.js','text/javascript'),
                    '/activation.js':('activation.js','text/javascript'),'/activation.css':('activation.css','text/css'),
                    '/layout.js':('layout.js','text/javascript'),'/journey.js':('journey.js','text/javascript'),'/style.css':('style.css','text/css'),
                    '/paired.js':('paired.js','text/javascript'),'/paired.css':('paired.css','text/css'),
                    '/prompt_editor.js':('prompt_editor.js','text/javascript'),
                    '/paired_trace.js':('paired_trace.js','text/javascript'),
                    '/version_views.js':('version_views.js','text/javascript'),'/version_views.css':('version_views.css','text/css'),
                    '/files.js':('files.js','text/javascript'),'/files.css':('files.css','text/css'),
                    '/sessions.js':('sessions.js','text/javascript'),'/sessions.css':('sessions.css','text/css'),
                    '/fonts/NanumGothic-Regular.ttf':('fonts/NanumGothic-Regular.ttf','font/ttf')}
            asset_path=urlsplit(self.path).path
            if asset_path not in assets:return super().do_GET()
            name,mime=assets[asset_path];raw=(HERE/'frontend'/name).read_bytes()
            self.send_response(200);self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
            self.end_headers();self.wfile.write(raw)

        def do_POST(self):
            if prompt_files.handle_post(self):return
            if file_manager.handle_post(self):return
            if self.path in ('/api/paired/create','/api/paired/step','/api/paired/manual','/api/chat'):
                with file_manager.generation(self.server):
                    if paired_lab.handle_post(self, backend, runtime, base_build):return
                    return super().do_POST()
            if paired_lab.handle_post(self, backend, runtime, base_build):return
            return super().do_POST()

    server=backend.make_server(config_path,host=host,port=port,storage_dir=destination)
    locals_server['server']=server
    file_manager.initialize(server)
    server.storage_dir.mkdir(parents=True, exist_ok=True)
    server.RequestHandlerClass=Handler
    server.paired_locks = {}
    server.paired_registry_lock = threading.Lock()
    filesystem_definitions=server.definitions
    def configure_request(config, body):
        import runtime.connection_settings as connection_settings
        connection_settings.apply(config, body)
        experiment_versions.configure(config, body)
        session_browser.configure(server, config, body)
    server.configure_request = configure_request
    server.definitions=lambda config:runtime.definitions(mode,'main',0,filesystem_definitions(config),experiment,
        version=config.get('_experiment_version', experiment_versions.name(config)))
    return server


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='상담 내담자 반응 실습')
    parser.add_argument('--config',type=Path,default=HERE/'config/app.json')
    parser.add_argument('--host');parser.add_argument('--port',type=int,default=8891)
    args=parser.parse_args()
    try:server=make_server(args.config,args.host,args.port,mode='dynamic',experiment='counseling')
    except OSError as error:raise SystemExit(f'시작 실패: {error}\n기존 UI 서버를 Ctrl+C로 종료한 뒤 실행하거나 --port를 지정하세요.')
    print(f'Counseling Client Lab: http://{server.server_address[0]}:{server.server_address[1]}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
