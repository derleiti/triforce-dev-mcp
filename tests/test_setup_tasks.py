from app.setup_tasks import TASKS, get_task


def test_catalog_has_fixed_expected_tasks():
    assert {"system-check", "profile-server", "profile-node", "runtime-init", "config-init", "service-install", "docker-install", "redis-check", "memory-worker", "agents-check", "diagnose"} <= set(TASKS)


def test_mutating_tasks_are_narrow_admin_helper_actions():
    for task in TASKS.values():
        if task.mutating:
            assert task.requires_admin
            assert task.helper_action in {"runtime-init", "config-init", "service-install", "docker-install", "profile-server", "profile-node"}


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


def test_system_check_does_not_require_global_python3_shim(monkeypatch):
    import app.setup_tasks as setup_tasks
    real_which = setup_tasks.shutil.which
    monkeypatch.setattr(setup_tasks.shutil, "which", lambda name: None if name == "python3" else real_which(name))
    result = setup_tasks.check_system()
    assert result.ok, result.message


def test_docker_install_task_is_fixed_and_does_not_grant_socket_privileges():
    task = TASKS["docker-install"]
    assert task.mutating and task.requires_admin
    assert task.dependencies == ("system-check",)
    assert task.helper_action == "docker-install"
    text = repr(task.plan()).lower()
    assert "docker.io" in text
    assert "docker-compose-v2" in text
    assert "docker-gruppe" in text


def test_service_install_task_repairs_package_managed_unit():
    task = TASKS["service-install"]
    assert task.title == "TriForce-Dienst installieren/reparieren"
    assert "/usr/lib/systemd/system/triforce.service" in task.changes
    assert not any("/etc/systemd/system/triforce.service" == change for change in task.changes)


def test_profiles_have_distinct_docker_policy():
    server = TASKS["profile-server"]
    node = TASKS["profile-node"]
    assert server.helper_action == "profile-server"
    assert node.helper_action == "profile-node"
    assert any("Docker Engine" in change for change in server.changes)
    assert any("Docker-Installation unverändert" in change for change in node.changes)
    assert any("triforce.service aktivieren und starten" in change for change in server.changes)
    assert any("triforce.service aktivieren und starten" in change for change in node.changes)
