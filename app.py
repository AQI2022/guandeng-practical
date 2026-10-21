"""Local demonstration only: python -X utf8 app.py (http://127.0.0.1:8765)."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import json
from src.policy import decide

ROOT=Path(__file__).resolve().parent


class Handler(BaseHTTPRequestHandler):
    def send(self,code,data,content_type):
        self.send_response(code)
        self.send_header('Content-Type',content_type)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path=self.path.split('?',1)[0]
        routes={'/':('web/index.html','text/html; charset=utf-8'),
                '/api/report':('results/report.json','application/json; charset=utf-8')}
        if path not in routes:
            return self.send(404,b'Not found','text/plain')
        name,ctype=routes[path]
        if not (ROOT/name).is_file():
            return self.send(503,b'Run scripts/analyze.py first.','text/plain')
        self.send(200,(ROOT/name).read_bytes(),ctype)

    def do_POST(self):
        if self.path!='/api/check':
            return self.send(404,b'Not found','text/plain')
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=16384:
                raise ValueError('Invalid request size')
            payload=json.loads(self.rfile.read(size))
            if not isinstance(payload,dict):
                raise ValueError('Expected JSON object')
            result=decide(payload)
            self.send(200,json.dumps(result,ensure_ascii=False).encode('utf-8'),'application/json; charset=utf-8')
        except (ValueError,TypeError,UnicodeError,RecursionError):
            self.send(400,b'{"error":"Invalid JSON request"}','application/json')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8765)
    args=parser.parse_args()
    print(f'Local demo: http://127.0.0.1:{args.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
