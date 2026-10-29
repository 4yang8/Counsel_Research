"""Independent counseling lab with raw contexts, workflow, and durable logs."""
import argparse
import copy
import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
# Load by file location so importing other labs cannot select their runtime.py.
spec = importlib.util.spec_from_file_location('nested_runtime', HERE/'runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def make_server(config_path=HERE/'config.json',host=None,port=8891,storage_dir=None,mode='dynamic',experiment='counseling'):
    spec = importlib.util.spec_from_file_location('nested_backend_'+mode,HERE/'server.py')
    backend = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(backend)
    original_read_config = backend.read_config
    def read_config(path):
        config = original_read_config(path)
        config['_delegation_experiment'] = experiment
        config['scenario'] = runtime.load_scenario(config)
        config['filesystem'] = {'enabled': False}
        return config
    backend.read_config = read_config
    base_build = backend.build_payload
    backend.build_payload = lambda config,body,tools=None,preview=False: runtime.build(config,body,tools or [],base_build,mode,preview=preview)
    backend.run_turn = lambda *args: runtime.execute(*args,base_build=base_build,mode=mode)

    class AgentLog(backend.TurnLog):
        def append(self,event):
            event=copy.deepcopy(event)
            event.setdefault('mode',mode)
            event.setdefault('experiment',experiment)
            if event['type']!='agent_event':
                event.setdefault('agent_id','main');event.setdefault('agent_name','메인');event.setdefault('depth',0)
            if event['type']=='agent_event':
                self.state.setdefault('subagent_events',[]).append(copy.deepcopy(event))
            if event['type']=='done':
                for key in ('main_usage','subagent_usage','agent_usage'):self.state[key]=event.get(key,{})
            self.state['mode']=mode
            self.state['experiment']=experiment
            return super().append(event)
    backend.TurnLog=AgentLog

    class Handler(backend.Handler):
        def json_response(self,data,status=200):
            if self.path=='/api/tools' and 'tools' in data:
                config=backend.read_config(self.server.config_path)
                data={**data,'nested_mode':mode,'max_depth':None, 'enabled':True, 'max_calls':config.get('delegation', {}).get('max_calls', 8),
                      **runtime.settings(config)}
            if self.path=='/api/config' and status==200:data={**data,'nested_mode':mode,'experiment':experiment}
            return super().json_response(data,status)
        def do_GET(self):
            assets={'/':('index.html','text/html; charset=utf-8'),'/app.js':('app.js','text/javascript'),
                    '/workflow.js':('workflow.js','text/javascript'),
                    '/layout.js':('layout.js','text/javascript'),'/journey.js':('journey.js','text/javascript'),'/style.css':('style.css','text/css'),
                    '/fonts/NanumGothic-Regular.ttf':('fonts/NanumGothic-Regular.ttf','font/ttf')}
            if self.path not in assets:return super().do_GET()
            name,mime=assets[self.path];raw=(HERE/'static'/name).read_bytes()
            self.send_response(200);self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(raw)));self.send_header('Cache-Control','no-store')
            self.end_headers();self.wfile.write(raw)

    server=backend.make_server(config_path,host=host,port=port,storage_dir=storage_dir or (HERE/'sessions'))
    server.RequestHandlerClass=Handler
    filesystem_definitions=server.definitions
    server.definitions=lambda config:runtime.definitions(mode,'main',0,filesystem_definitions(config),experiment)
    return server


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='상담 내담자 반응 실습')
    parser.add_argument('--config',type=Path,default=HERE/'config.json')
    parser.add_argument('--host');parser.add_argument('--port',type=int,default=8891)
    args=parser.parse_args()
    try:server=make_server(args.config,args.host,args.port,mode='dynamic',experiment='counseling')
    except OSError as error:raise SystemExit(f'시작 실패: {error}\n기존 UI 서버를 Ctrl+C로 종료한 뒤 실행하거나 --port를 지정하세요.')
    print(f'Counseling Client Lab: http://{server.server_address[0]}:{server.server_address[1]}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()
