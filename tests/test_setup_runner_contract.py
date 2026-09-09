from pathlib import Path

RUNNER = Path(__file__).parents[1] / "packaging" / "triforce-setup-runner.py"


def test_runner_has_fixed_tasks_and_no_shell_execution():
    text = RUNNER.read_text()
    assert 'TASK_ACTIONS = {' in text
    assert '"runtime-init": "runtime-init"' in text
    assert '"config-init": "config-init"' in text
    assert '"service-install": "service-install"' in text
    assert '"docker-install": "docker-install"' in text
    assert "shell=True" not in text
    assert "subprocess.run([str(HELPER), TASK_ACTIONS[task]]" in text


def test_runner_status_never_persists_helper_output():
    text = RUNNER.read_text()
    assert "Never persist helper stdout/stderr" in text
    assert '"verification": verification' in text


def test_runner_checks_declared_dependencies_before_mutation():
    text = RUNNER.read_text()
    dependency_pos = text.index("dependency_results =")
    mutation_pos = text.index("cp = subprocess.run([str(HELPER)")
    assert dependency_pos < mutation_pos
    assert "get_task(task).dependencies" in text
    assert '"state": "rejected"' in text
