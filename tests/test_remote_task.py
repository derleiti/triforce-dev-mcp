from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.remote_task import RemoteHost, RemoteTask, RemoteTaskService, TaskStatus, TaskType


class FakeStdout:
    def __init__(self, lines=()):
        self._lines = [line.encode() + b"\n" for line in lines]

    async def readline(self):
        return self._lines.pop(0) if self._lines else b""


class FakeProcess:
    def __init__(self, returncode: int, lines=()):
        self.returncode = None
        self._final_returncode = returncode
        self.stdout = FakeStdout(lines)
        self.terminated = False

    async def wait(self):
        if self.terminated:
            self.returncode = -15
        else:
            self.returncode = self._final_returncode
        return self.returncode

    def terminate(self):
        self.terminated = True


def fresh_service() -> RemoteTaskService:
    service = RemoteTaskService()
    service.hosts.clear()
    service.tasks.clear()
    service.running_processes.clear()
    return service


@pytest.mark.asyncio
async def test_execute_task_marks_nonzero_exit_failed(monkeypatch):
    service = fresh_service()
    process = FakeProcess(7, ["boom"])
    async def fake_create(*args, **kwargs):
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    host = RemoteHost(host_id="h", hostname="host", username="user")
    task = RemoteTask(task_id="t", host_id="h", task_type=TaskType.CUSTOM, description="x", agent_id="claude-mcp")
    template = {"prompt_template": "Task {description} on {hostname} {username} {password} {port}"}

    await service._execute_task(task, host, template)

    assert task.status is TaskStatus.FAILED
    assert task.result == {"exit_code": 7, "output_lines": 1}
    assert task.error == "Agent exited with code 7"
    assert "t" not in service.running_processes


@pytest.mark.asyncio
async def test_execute_task_marks_zero_exit_completed(monkeypatch):
    service = fresh_service()
    process = FakeProcess(0, ["ok"])
    async def fake_create(*args, **kwargs):
        return process
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create)
    host = RemoteHost(host_id="h", hostname="host", username="user")
    task = RemoteTask(task_id="t", host_id="h", task_type=TaskType.CUSTOM, description="x", agent_id="claude-mcp")
    template = {"prompt_template": "Task {description} on {hostname} {username} {password} {port}"}

    await service._execute_task(task, host, template)

    assert task.status is TaskStatus.COMPLETED
    assert task.result["exit_code"] == 0


def test_cancel_task_terminates_running_process():
    service = fresh_service()
    task = RemoteTask(task_id="t", host_id="h", task_type=TaskType.CUSTOM, description="x", status=TaskStatus.RUNNING)
    process = FakeProcess(0)
    service.tasks[task.task_id] = task
    service.running_processes[task.task_id] = process

    assert service.cancel_task("t") is True
    assert task.status is TaskStatus.CANCELLED
    assert process.terminated is True
