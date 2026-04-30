from datetime import datetime, timezone

from sqlalchemy import select

from app.models import Post, PushRecord, SourceAccount, User


def login(client, openid="tester"):
    response = client.post("/api/wechat/login", json={"code": "code", "dev_openid": openid})
    assert response.status_code == 200
    return response.json()["token"]


def auth_header(token):
    return {"Authorization": f"Bearer {token}"}


def test_whitelist_required_for_accounts(client, db_session):
    token = login(client)
    response = client.get("/api/accounts", headers=auth_header(token))
    assert response.status_code == 403

    user = db_session.scalar(select(User).where(User.openid == "tester"))
    user.is_whitelisted = True
    db_session.add(SourceAccount(name="A", profile_url="https://xueqiu.com/u/1", xueqiu_user_id="1"))
    db_session.commit()

    response = client.get("/api/accounts", headers=auth_header(token))
    assert response.status_code == 200
    assert response.json()[0]["name"] == "A"


def test_free_subscription_limit(client, db_session):
    token = login(client)
    user = db_session.scalar(select(User).where(User.openid == "tester"))
    user.is_whitelisted = True
    for index in range(4):
        db_session.add(SourceAccount(name=f"A{index}", profile_url=f"https://xueqiu.com/u/{index}", xueqiu_user_id=str(index)))
    db_session.commit()

    for account_id in [1, 2, 3]:
        response = client.post("/api/subscriptions", json={"account_id": account_id}, headers=auth_header(token))
        assert response.status_code == 200

    response = client.post("/api/subscriptions", json={"account_id": 4}, headers=auth_header(token))
    assert response.status_code == 403
    assert response.json()["detail"] == "Subscription limit reached"


def test_post_detail_requires_successful_push(client, db_session):
    token = login(client)
    user = db_session.scalar(select(User).where(User.openid == "tester"))
    user.is_whitelisted = True
    account = SourceAccount(name="A", profile_url="https://xueqiu.com/u/1", xueqiu_user_id="1")
    db_session.add(account)
    db_session.flush()
    post = Post(
        source_account_id=account.id,
        xueqiu_post_id="p1",
        content="全文内容",
        content_hash="h1",
        publish_time=datetime.now(timezone.utc),
        original_url="https://xueqiu.com/1/p1",
        crawl_time=datetime.now(timezone.utc),
    )
    db_session.add(post)
    db_session.flush()
    db_session.commit()

    response = client.get(f"/api/posts/{post.id}", headers=auth_header(token))
    assert response.status_code == 404

    db_session.add(PushRecord(user_id=user.id, post_id=post.id, push_status="success", push_time=datetime.now(timezone.utc)))
    db_session.commit()

    response = client.get(f"/api/posts/{post.id}", headers=auth_header(token))
    assert response.status_code == 200
    assert response.json()["content"] == "全文内容"

