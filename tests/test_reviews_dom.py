"""Exercise actual review HTML/JS with an existing local Playwright/Edge."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import unittest

from dance_results.gui import create_server
import test_result_review as fixture


class ReviewDOMTests(unittest.TestCase):
    setUp = fixture.ReviewPersistenceTests.setUp
    tearDown = fixture.ReviewPersistenceTests.tearDown

    def run_browser(self, mode):
        runtime = Path(os.environ.get('LOCALAPPDATA', Path.home()/'AppData/Local')).parent.parent/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
        node = shutil.which('node') or str(runtime/'bin/node.exe')
        playwright = runtime/'node_modules/playwright'
        if not Path(node).is_file() or not playwright.exists():
            self.skipTest('Existing Node/Playwright runtime unavailable')
        server = create_server(self.db, monitor_directory=self.output)
        if hasattr(self, 'configure_server'):
            self.configure_server(server)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = subprocess.run([
                node, str(Path(__file__).with_name('reviews_dom.cjs')), str(playwright),
                f'http://127.0.0.1:{server.server_port}', mode,
            ], capture_output=True, text=True, encoding='utf8', timeout=75)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
            self.assertTrue(json.loads(result.stdout)['passed'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_render_search_edit_draft_cancel_and_single_save_path(self):
        self.run_browser('submit')

    def test_render_discard_cancel_confirm_and_empty_state(self):
        self.run_browser('discard')

    def test_failed_submission_stays_reachable_and_can_retry(self):
        self.run_browser('retry')

    def test_unprocessed_card_can_be_discarded(self):
        (self.output/'sessions'/self.sid/'result-review.json').unlink()
        self.run_browser('unprocessed')

    def test_committed_receipt_recovers_lost_submit_response(self):
        self.run_browser('receipt')

    def test_all_pages_fit_phone_and_tablet_viewports(self):
        self.run_browser('responsive')


if __name__ == '__main__':
    unittest.main()
