from scripts.future_labs_ci_evidence import collect


def test_ci_evidence_preserves_startup_failure_without_certifying_tests():
    def fake(_):
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
