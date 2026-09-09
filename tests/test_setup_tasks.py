from app.setup_tasks import TASKS, get_task


def test_catalog_has_fixed_expected_tasks():
    assert {"system-check", "runtime-init", "config-init", "service-install", "redis-check", "memory-worker", "agents-check", "diagnose"} <= set(TASKS)


def test_mutating_tasks_are_narrow_admin_helper_actions():
    for task in TASKS.values():
        if task.mutating:
            assert task.requires_admin
            assert task.helper_action in {"runtime-init", "config-init", "service-install"}


def test_no_task_exposes_arbitrary_script_or_shell_parameter():
    for task in TASKS.values():
        plan = task.plan()
        text = repr(plan).lower()
        assert "script_path" not in text
        assert "shell_command" not in text


def test_unknown_task_is_rejected():
    try:
        get_task("../../bin/sh")
    except KeyError:
        pass
    else:
        raise AssertionError("unknown task must be rejected")
