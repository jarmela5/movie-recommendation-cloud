
import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        data = json.loads(body['messages'][1]['content'])
        if 'candidates' in data:
            count = body['response_format']['schema']['properties']['recommendations']['maxItems']
            result = {'recommendations': [dict(id=m['id'])
                                         for m in data['candidates'][:count]]}
        else:
            result = dict(genres=[], avoids=[], year_from=None, year_to=None, unsupported=False)
        payload = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(result)}}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
