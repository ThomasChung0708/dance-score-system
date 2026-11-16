"""Exercise actual review HTML/JS with an existing local Playwright/Edge."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import unittest
import copy
from uuid import uuid4

from dance_results.monitor_tracking import atomic_json

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

    def test_dirty_switch_poll_focus_and_late_draft_response(self):
        sid = str(uuid4())
        folder = self.output/'sessions'/sid
        shutil.copytree(self.output/'sessions'/self.sid, folder)
        session = copy.deepcopy(self.session)
        session['session_id'] = sid
        atomic_json(folder/'session.json', session)
        row = copy.deepcopy(self.row)
        row.update(session_id=sid, review_id=str(uuid4()), locked_session=session)
        atomic_json(folder/'result-review.json', row)
        self.run_browser('safety')

    def test_confirmed_chartless_history_renders_unknown_difficulty(self):
        from dance_results.library_service import LibraryService
        from dance_results.submissions import SubmissionService
        self.service.scoring_policy = 'custom-judged-v1'
        body = fixture.ReviewPersistenceTests.body(self)
        body['values'].update(difficulty=None, total_notes=None)
        body.pop('max_basis', None)
        saved = self.service.save(body)
        body.update(revision=saved['revision'], play_mode='single')
        store = SubmissionService(LibraryService(self.db), self.service)
        preview = store.prepare(body)
        store.submit(dict(body, submission_token=preview['submission_token'], submit_acknowledge=True))
        self.run_browser('chartless')


if __name__ == '__main__':
    unittest.main()
