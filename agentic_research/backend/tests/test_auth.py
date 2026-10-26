async def test_register_success(client):
    response = await client.post("/api/register", json={
        "username": "alice",
        "email": "alice@example.com",
        "password": "secret123",
    })
    assert response.status_code == 201
    data = response.json()
    assert data["token_type"] == "bearer"
    assert data["access_token"]
    assert data["user"]["username"] == "alice"


async def test_register_duplicate_username(client, registered_user):
    response = await client.post("/api/register", json={
        "username": registered_user["user"]["username"],
        "email": "new@example.com",
        "password": "secret123",
    })
    assert response.status_code == 400
    assert "already registered" in response.json()["detail"].lower()


async def test_register_duplicate_email(client, registered_user):
    response = await client.post("/api/register", json={
        "username": "newuser",
        "email": registered_user["user"]["email"],
        "password": "secret123",
    })
    assert response.status_code == 400


async def test_login_success(client, registered_user):
    response = await client.post("/api/login", data={
        "username": registered_user["user"]["username"],
        "password": "password123",
    })
    assert response.status_code == 200
    assert response.json()["access_token"]


async def test_login_wrong_password(client, registered_user):
    response = await client.post("/api/login", data={
        "username": registered_user["user"]["username"],
        "password": "wrong_password",
    })
    assert response.status_code == 401


async def test_login_nonexistent_user(client):
    response = await client.post("/api/login", data={
        "username": "ghost",
        "password": "whatever",
    })
    assert response.status_code == 401


async def test_get_me_success(client, auth_headers, registered_user):
    response = await client.get("/api/me", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["username"] == registered_user["user"]["username"]


async def test_get_me_no_token(client):
    response = await client.get("/api/me")
    assert response.status_code == 401


async def test_get_me_invalid_token(client):
    response = await client.get(
        "/api/me",
        headers={"Authorization": "Bearer invalid.token.here"},
    )
    assert response.status_code == 401