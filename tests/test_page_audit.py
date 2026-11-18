"""Full-site audit regression: static routes and disconnected HTTP clients."""
from io import BytesIO
from html.parser import HTMLParser
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.request import urlopen
from urllib.parse import urlsplit

from dance_results.gui import create_server
import test_gui as fixture


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = set()

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.paths.add(urlsplit(dict(attrs).get('href') or '').path)


class PageAuditTests(unittest.TestCase):
    setUp = fixture.GuiServiceTests.setUp
    tearDown = fixture.GuiServiceTests.tearDown

    def handler(self):
        server = create_server(self.path, monitor_directory=self.base/'monitor')
        self.addCleanup(server.server_close)
        handler = server.RequestHandlerClass.__new__(server.RequestHandlerClass)
        handler.server = server
        handler.command = 'GET'
        handler.headers = {'Host': f'127.0.0.1:{server.server_port}'}
        handler.path = '/style.css'
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = BytesIO()
        handler.close_connection = False
        return handler

    def test_disconnected_response_body_closes_connection(self):
        handler = self.handler()
        handler.wfile = Mock()
        handler.wfile.write.side_effect = BrokenPipeError('peer left')
        handler._response(200, b'body', 'text/plain')
        self.assertTrue(handler.close_connection)
        handler.send_response.assert_called_once_with(200)

    def test_disconnected_response_headers_close_connection(self):
        handler = self.handler()
        handler.end_headers.side_effect = ConnectionAbortedError('navigation cancelled')
        handler._response(200, b'body')
        self.assertTrue(handler.close_connection)
        self.assertEqual(handler.wfile.getvalue(), b'')

    def test_cancelled_static_get_does_not_log_or_send_second_500(self):
        handler = self.handler()
        handler.wfile = Mock()
        handler.wfile.write.side_effect = ConnectionResetError('peer reset')
        with patch('dance_results.gui.logging.exception') as log:
            handler.do_GET()
        log.assert_not_called()
        handler.send_response.assert_called_once_with(200)
        self.assertTrue(handler.close_connection)

    def test_unrelated_io_error_is_not_silenced(self):
        handler = self.handler()
        handler.wfile = Mock()
        handler.wfile.write.side_effect = OSError('unrelated I/O failure')
        with self.assertRaisesRegex(OSError, 'unrelated'):
            handler._response(200, b'body')

    def test_all_html_and_static_routes_return_expected_mime(self):
        server = create_server(self.path, monitor_directory=self.base/'monitor')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            routes = {
                '/': 'text/html',
                '/reviews': 'text/html',
                '/history': 'text/html',
                '/monitor': 'text/html',
                '/app.js': 'text/javascript',
                '/style.css': 'text/css',
                '/lamps.js': 'text/javascript',
                '/lamps.css': 'text/css',
                '/reviews.js': 'text/javascript',
                '/reviews.css': 'text/css',
                '/history.js': 'text/javascript',
                '/history.css': 'text/css',
                '/monitor.js': 'text/javascript',
            }
            for route, mime in routes.items():
                with self.subTest(route=route), urlopen(f'http://127.0.0.1:{server.server_port}{route}') as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), mime)
                    self.assertGreater(len(response.read()), 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_ocr_routes_served_but_unlinked_from_library_and_history(self):
        server = create_server(
            self.path, monitor_directory=self.base/'monitor', game_sync=False,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            hidden_routes = {'/monitor', '/reviews'}
            for route in ('/', '/history', *sorted(hidden_routes)):
                with self.subTest(route=route), urlopen(
                    f'http://127.0.0.1:{server.server_port}{route}'
                ) as response:
                    self.assertEqual(response.status, 200)
                    markup = response.read().decode('utf-8')
                    if route in ('/', '/history'):
                        links = PageLinks()
                        links.feed(markup)
                        self.assertFalse(links.paths & hidden_routes)
                        self.assertIn('/history' if route == '/' else '/', links.paths)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
