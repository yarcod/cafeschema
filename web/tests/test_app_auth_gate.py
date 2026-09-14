def test_mine_redirects_to_login_when_logged_out(client):
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_login_page_is_reachable_when_logged_out(client):
    response = client.get("/login")

    assert response.status_code == 200


def test_mine_reachable_after_session_login(client, session_factory, seeded):
    with client.session_transaction() as sess:
        sess["_user_id"] = str(seeded["person"].id)
        sess["_fresh"] = True

    response = client.get("/")

    assert response.status_code == 200
