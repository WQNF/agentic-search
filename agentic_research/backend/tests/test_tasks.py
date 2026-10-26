import asyncio
from pathlib import Path


async def _wait_for_terminal_status(client, task_id, headers, timeout=10):
    """轮询任务，直到状态不再是 pending/running"""
    terminal = {"success", "partial_success", "failed"}
    elapsed = 0
    while elapsed < timeout:
        resp = await client.get(f"/api/tasks/{task_id}", headers=headers)
        status = resp.json()["status"]
        if status in terminal:
            return resp.json()
        await asyncio.sleep(0.3)
        elapsed += 0.3
    return resp.json()


async def test_create_task_success(client, auth_headers):
    response = await client.post(
        "/api/tasks",
        json={"query": "AAPL"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    data = response.json()
    assert data["query"] == "AAPL"
    assert data["run_id"]
    assert data["id"]


async def test_create_task_invalid_query(client, auth_headers):
    response = await client.post(
        "/api/tasks",
        json={"query": ""},
        headers=auth_headers,
    )
    assert response.status_code == 422  # Pydantic 校验失败


async def test_create_task_no_auth(client):
    response = await client.post("/api/tasks", json={"query": "AAPL"})
    assert response.status_code == 401


async def test_task_lifecycle(client, auth_headers):
    """完整生命周期：创建 → 后台执行 → 终态"""
    create_resp = await client.post(
        "/api/tasks", json={"query": "AAPL"}, headers=auth_headers
    )
    task_id = create_resp.json()["id"]

    # 等待终态
    final = await _wait_for_terminal_status(client, task_id, auth_headers)
    assert final["status"] == "success"
    assert final["final_status"] == "success"
    assert final["duration_ms"] is not None
    assert final["finished_at"] is not None
    assert final["entity"]["symbol"] == "AAPL"


async def test_get_task_status_endpoint(client, auth_headers):
    create_resp = await client.post(
        "/api/tasks", json={"query": "AAPL"}, headers=auth_headers
    )
    task_id = create_resp.json()["id"]
    await _wait_for_terminal_status(client, task_id, auth_headers)

    resp = await client.get(f"/api/tasks/{task_id}/status", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "success"
    # 由于 mock 了缓存写入，这里会回退到 database
    assert data["source"] in ("redis", "database")


async def test_list_tasks(client, auth_headers):
    # 创建两个任务
    for query in ["AAPL", "MSFT"]:
        await client.post("/api/tasks", json={"query": query}, headers=auth_headers)

    response = await client.get("/api/tasks", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


async def test_get_task_not_found(client, auth_headers):
    fake_id = "00000000-0000-0000-0000-000000000000"
    response = await client.get(f"/api/tasks/{fake_id}", headers=auth_headers)
    assert response.status_code == 404


async def test_get_task_other_user_forbidden(client, auth_headers, second_user_headers):
    create_resp = await client.post(
        "/api/tasks", json={"query": "AAPL"}, headers=auth_headers
    )
    task_id = create_resp.json()["id"]

    response = await client.get(f"/api/tasks/{task_id}", headers=second_user_headers)
    assert response.status_code == 404  # 对另一用户不可见


async def test_list_artifacts(client, auth_headers):
    create_resp = await client.post(
        "/api/tasks", json={"query": "AAPL"}, headers=auth_headers
    )
    task_id = create_resp.json()["id"]
    task_run_id = create_resp.json()["run_id"]
    await _wait_for_terminal_status(client, task_id, auth_headers)

    # 手动创建假产物文件（因为 mock graph 不会写文件）
    run_dir = Path("runs") / task_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "00_input.json").write_text('{"query": "AAPL"}', encoding="utf-8")
    (run_dir / "04_metrics.json").write_text('{"pe": 28.5}', encoding="utf-8")

    resp = await client.get(f"/api/tasks/{task_id}/artifacts", headers=auth_headers)
    assert resp.status_code == 200
    files = resp.json()["files"]
    assert "00_input.json" in files
    assert "04_metrics.json" in files


async def test_get_single_artifact(client, auth_headers):
    create_resp = await client.post(
        "/api/tasks", json={"query": "AAPL"}, headers=auth_headers
    )
    task_id = create_resp.json()["id"]
    task_run_id = create_resp.json()["run_id"]
    await _wait_for_terminal_status(client, task_id, auth_headers)

    run_dir = Path("runs") / task_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "04_metrics.json").write_text('{"pe": 28.5, "pb": 12.0}', encoding="utf-8")

    resp = await client.get(
        f"/api/tasks/{task_id}/artifacts/04_metrics.json",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["content_type"] == "json"
    assert data["content"]["pe"] == 28.5


async def test_artifact_path_traversal_blocked(client, auth_headers):
    create_resp = await client.post(
        "/api/tasks", json={"query": "AAPL"}, headers=auth_headers
    )
    task_id = create_resp.json()["id"]
    await _wait_for_terminal_status(client, task_id, auth_headers)

    resp = await client.get(
        f"/api/tasks/{task_id}/artifacts/..%2Fetc%2Fpasswd",
        headers=auth_headers,
    )
    assert resp.status_code in (400, 404)


async def test_get_report(client, auth_headers):
    create_resp = await client.post(
        "/api/tasks", json={"query": "AAPL"}, headers=auth_headers
    )
    task_id = create_resp.json()["id"]
    task_run_id = create_resp.json()["run_id"]
    await _wait_for_terminal_status(client, task_id, auth_headers)

    # 写入报告文件（mock graph 不实际写文件）
    run_dir = Path("runs") / task_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "05_report.md").write_text(
        "# Apple Inc.\n\n## 1. 公司概览\n...", encoding="utf-8"
    )

    resp = await client.get(f"/api/tasks/{task_id}/report", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["final_status"] == "success"
    assert "Apple Inc." in data["report_markdown"]


async def test_get_report_not_ready(client, auth_headers):
    create_resp = await client.post(
        "/api/tasks", json={"query": "AAPL"}, headers=auth_headers
    )
    task_id = create_resp.json()["id"]
    await _wait_for_terminal_status(client, task_id, auth_headers)

    # 不写文件，报告接口应返回 404
    resp = await client.get(f"/api/tasks/{task_id}/report", headers=auth_headers)
    assert resp.status_code == 404


async def test_download_report(client, auth_headers):
    create_resp = await client.post(
        "/api/tasks", json={"query": "AAPL"}, headers=auth_headers
    )
    task_id = create_resp.json()["id"]
    task_run_id = create_resp.json()["run_id"]
    await _wait_for_terminal_status(client, task_id, auth_headers)

    run_dir = Path("runs") / task_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "05_report.md").write_text("# Report", encoding="utf-8")

    resp = await client.get(
        f"/api/tasks/{task_id}/report/download", headers=auth_headers
    )
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["content-type"]