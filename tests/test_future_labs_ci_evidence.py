from scripts.future_labs_ci_evidence import collect


def test_ci_evidence_preserves_startup_failure_without_certifying_tests():
    def fake(_):
        if '/jobs?' in _:
            return {'total_count': 0}
        return {'workflow_runs': [
            {'name': 'CI', 'id': 3, 'conclusion': 'startup_failure', 'status': 'completed', 'head_sha': 'abc'},
            {'name': 'CI', 'id': 2, 'conclusion': 'success', 'status': 'completed'},
            {'name': 'Security', 'id': 1, 'conclusion': 'startup_failure', 'status': 'completed'},
        ]}
    data = collect(fake)
    assert len(data['runs']) == 2
    assert data['runs'][0]['conclusion'] == 'startup_failure'
    assert data['runs'][0]['id'] == 3
    assert data['scope'] == 'remote_master_only'


def test_ci_no_jobs_is_explicit_and_not_passed():
    def fake(url):
        if '/jobs?' in url:
            return {'total_count': 0}
        return {'workflow_runs': [{'name': 'CI', 'id': 42,
            'status': 'completed', 'conclusion': 'startup_failure'}]}
    entry = collect(fake)['runs'][0]
    assert entry['job_count'] == 0
    assert entry['evidence_class'] == 'workflow_startup_failed_no_jobs'
