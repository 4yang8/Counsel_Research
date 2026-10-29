"""Malformed requests must not leak HTML into the JSON client."""
import json
import socket
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from test_lab import fixture, close, Model


class HTTPResponseTests(unittest.TestCase):
    def setUp(self):
        self.server, self.model, self.temp = fixture()
        Model.requests.clear()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        close(self.server, self.model, self.temp)

    def test_binary_prefix_config_request_returns_json_and_closes(self):
        # Replay the prefix in the user's runtime log. parse_request fails before
        # self.path exists, so subclass JSON decoration must also tolerate it.
        with socket.create_connection(('127.0.0.1', self.server.server_port), timeout=3) as connection:
            connection.sendall((b'\t' + b'\x00'*12)*2 + b'GET /api/config HTTP/1.1\r\nHost: localhost\r\n\r\n')
            response = bytearray()
            while chunk := connection.recv(4096):
                response.extend(chunk)
        headers, body = bytes(response).split(b'\r\n\r\n', 1)
        self.assertIn(b'400 Bad Request', headers)
        self.assertIn(b'application/json', headers)
        self.assertIn(b'Connection: close', headers)
        self.assertTrue(json.loads(body)['request_error'])
        self.assertNotIn(b'<!DOCTYPE', body)
        self.assertIn('scenario', json.load(urlopen(self.base + '/api/config')))
        self.assertEqual(Model.requests, [])

    def test_unsupported_method_is_json_and_normal_404_is_preserved(self):
        for request, code in ((Request(self.base + '/api/config', method='PATCH'), 501),
                              (self.base + '/api/missing', 404)):
            with self.subTest(code=code), self.assertRaises(HTTPError) as raised:
                urlopen(request)
            response = raised.exception
            self.assertEqual(response.code, code)
            self.assertEqual(response.headers.get_content_type(), 'application/json')
            self.assertIn('error', json.load(response))


if __name__ == '__main__':
    unittest.main()
