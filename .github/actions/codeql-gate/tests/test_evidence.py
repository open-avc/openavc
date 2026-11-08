"""Synthetic GitHub evidence exercises release refusal boundaries."""
import copy
import unittest
from unittest.mock import patch
import subprocess
from codeql_gate.api import GitHub, Unavailable
from codeql_gate.evidence import Findings, Pending, verify

SHA, PARENT, TIP = 'a' * 40, 'b' * 40, 'c' * 40
RUN = dict(id=10, path='dynamic/github-code-scanning/codeql', created_at='2026-01-01T00:00:00Z',
           run_started_at='2026-01-01T00:00:00Z', updated_at='2026-01-01T00:10:00Z',
           run_attempt=1, status='completed', conclusion='success', html_url='https://example.test/run')
ANALYSIS = dict(id=20, commit_sha=SHA, environment='{"language":"python"}',
                analysis_key='dynamic/github-code-scanning/codeql:analyze',
                created_at='2026-01-01T00:05:00Z', error='', warning='', rules_count=10, results_count=0)
RESULT = dict(properties={'github/alertNumber': 1}, ruleId='py/example', locations=[{
    'physicalLocation': {'artifactLocation': {'uri': 'example.py'}, 'region': {'startLine': 5}}}])
ALERT = dict(number=1, state='open', html_url='https://example.test/alert',
             dismissed_reason=None, dismissed_comment=None)


class FakeGitHub:
    def __init__(self):
        self.runs = {SHA: [copy.deepcopy(RUN)]}
        self.analyses = [copy.deepcopy(ANALYSIS)]
        self.results = []
        self.alerts = [copy.deepcopy(ALERT)]
        self.parents = []
        self.parent_tree = 'different'
        self.comparison = 'ahead'

    def get(self, endpoint, sarif=False):
        if sarif:
            return {'version': '2.1.0', 'runs': [{'results': self.results}]}
        if endpoint.startswith('compare/'):
            return {'status': self.comparison}
        sha = endpoint.split('/')[-1]
        if sha == 'main':
            sha = TIP
        tree = self.parent_tree if sha == PARENT else 'tree'
        return {'sha': sha, 'parents': self.parents if sha == SHA else [], 'commit': {'tree': {'sha': tree}}}

    def pages(self, endpoint, key=None, **query):
        if endpoint == 'actions/runs':
            return iter(self.runs.get(query['head_sha'], []))
        if endpoint == 'code-scanning/analyses':
            return iter(self.analyses)
        return iter(self.alerts)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()

    def check(self, **options):
        return verify(self.api, SHA, 'main', {'python'}, **options)

    def add_finding(self):
        self.api.results = [copy.deepcopy(RESULT)]
        self.api.analyses[0]['results_count'] = 1

    def test_complete_zero_findings(self):
        self.assertIn(SHA, self.check())

    def test_pending_missing_failed_cancelled_and_newer_rerun_block(self):
        for status, conclusion in [('queued', None), ('completed', 'failure'), ('completed', 'cancelled')]:
            with self.subTest(status=status, conclusion=conclusion):
                self.api.runs[SHA] = [dict(RUN), dict(RUN, id=11, status=status, conclusion=conclusion)]
                with self.assertRaises(Pending):
                    self.check()
        self.api.runs = {}
        with self.assertRaises(Pending):
            self.check()

    def test_missing_language_wrong_sha_and_stale_analysis_block(self):
        with self.assertRaises(Pending):
            verify(self.api, SHA, 'main', {'python', 'actions'})
        self.api.analyses[0]['commit_sha'] = TIP
        with self.assertRaises(Pending):
            self.check()
        self.api.analyses[0] = dict(ANALYSIS, created_at='2025-12-31T00:00:00Z')
        with self.assertRaises(Pending):
            self.check()

    def test_bad_analysis_blocks(self):
        for change in [dict(error='failed'), dict(warning='partial'), dict(rules_count=0)]:
            with self.subTest(change=change):
                self.api.analyses[0] = dict(ANALYSIS, **change)
                with self.assertRaises(Unavailable):
                    self.check()

    def test_open_and_fixed_later_findings_block_candidate(self):
        self.add_finding()
        for state in ['open', 'fixed']:
            self.api.alerts[0]['state'] = state
            with self.assertRaisesRegex(Findings, 'example.py:5'):
                self.check()

    def test_only_documented_dismissal_passes(self):
        self.add_finding()
        self.api.alerts[0].update(state='dismissed', dismissed_reason='false positive')
        with self.assertRaises(Findings):
            self.check()
        self.api.alerts[0]['dismissed_comment'] = 'Synthetic test data; no production credential.'
        self.assertIn('verified', self.check())

    def test_missing_alert_and_truncated_snapshot_block(self):
        self.add_finding()
        self.api.alerts = []
        with self.assertRaises(Unavailable):
            self.check()
        self.api.results = []
        with self.assertRaises(Unavailable):
            self.check()

    def test_identical_merge_parent_only_if_trees_match(self):
        self.api.runs = {PARENT: [dict(RUN)]}
        self.api.analyses[0]['commit_sha'] = PARENT
        self.api.parents = [{'sha': PARENT}]
        with self.assertRaises(Pending):
            self.check()
        self.api.parent_tree = 'tree'
        self.assertIn(f'using {PARENT}', self.check())

    def test_descendant_only_after_landing_with_ancestry_proof(self):
        self.api.runs = {TIP: [dict(RUN)]}
        self.api.analyses[0]['commit_sha'] = TIP
        with self.assertRaises(Pending):
            self.check()
        self.assertIn(f'using {TIP}', self.check(allow_descendant=True))
        self.api.comparison = 'diverged'
        with self.assertRaises(Pending):
            self.check(allow_descendant=True)


class ApiTests(unittest.TestCase):
    def test_pagination_does_not_hide_later_alerts(self):
        api = GitHub('example/repo')
        with patch.object(api, 'get', side_effect=[list(range(100)), [101]]) as read:
            self.assertEqual(len(list(api.pages('code-scanning/alerts'))), 101)
            self.assertIn('page=2', read.call_args.args[0])

    def test_network_auth_and_invalid_json_never_become_clear(self):
        for failure in [OSError('no gh'), subprocess.TimeoutExpired('gh', 60),
                        subprocess.CalledProcessError(1, 'gh')]:
            with patch('subprocess.run', side_effect=failure), self.assertRaises(Unavailable):
                GitHub('example/repo').get('code-scanning/analyses')
        with patch('subprocess.run') as run, self.assertRaises(Unavailable):
            run.return_value.stdout = 'not json'
            GitHub('example/repo').get('code-scanning/analyses')

    def test_sarif_requested_explicitly(self):
        with patch('subprocess.run') as run:
            run.return_value.stdout = '{"runs": []}'
            GitHub('example/repo').get('code-scanning/analyses/1', sarif=True)
            self.assertIn('Accept: application/sarif+json', run.call_args.args[0])
