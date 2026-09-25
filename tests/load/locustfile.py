"""Нагрузочный сценарий OrderFlow Scale.

Файл НЕ собирается pytest'ом (не называется ``test_*``) — запускается вручную.

Web-UI::

    locust -f tests/load/locustfile.py --host http://localhost:8000
    # открыть http://localhost:8089

Headless, 200 пользователей::

    locust -f tests/load/locustfile.py --headless \
        -u 200 -r 50 -t 2m --host http://localhost:8000 \
        --csv=locust_report --html=locust_report.html
"""

import uuid

from locust import HttpUser, TaskSet, between, task


class CatalogTasks(TaskSet):
    """Каталог: список, фильтр, карточка, условный запрос с If-None-Match."""

    product_id = 1

    def on_start(self) -> None:
        resp = self.client.get("/api/v1/products?limit=1", name="/api/v1/products?limit")
        if resp.status_code == 200:
            payload = resp.json()
            if payload:
                self.product_id = payload[0]["id"]

    @task(10)
    def list_products(self) -> None:
        self.client.get("/api/v1/products", name="/api/v1/products")

    @task(3)
    def list_products_filtered(self) -> None:
        self.client.get(
            "/api/v1/products?category=books&limit=20",
            name="/api/v1/products?category=books",
        )

    @task(2)
    def get_product(self) -> None:
        self.client.get(
            f"/api/v1/products/{self.product_id}",
            name="/api/v1/products/{id}",
        )

    @task(2)
    def conditional_get(self) -> None:
        resp = self.client.get("/api/v1/products", name="/api/v1/products")
        etag = resp.headers.get("ETag")
        if etag:
            self.client.get(
                "/api/v1/products",
                headers={"If-None-Match": etag},
                name="/api/v1/products [304]",
            )


class CatalogUser(HttpUser):
    """Основной сценарий: чтение каталога (занимает ~95% трафика)."""

    wait_time = between(0.05, 0.3)
    tasks = [CatalogTasks]


class BuyerTasks(TaskSet):
    """Редкий сценарий: регистрация, логин, заказ — проверяет инвалидацию кэша."""

    def on_start(self) -> None:
        self.token = ""
        self.product_id = 1

    @task
    def browse_then_buy(self) -> None:
        listing = self.client.get("/api/v1/products?limit=1", name="/api/v1/products?limit")
        if listing.status_code == 200 and listing.json():
            self.product_id = listing.json()[0]["id"]

        listing = self.client.get("/api/v1/products", name="/api/v1/products")
        if not self.token and listing.status_code == 200:
            email = f"load-{uuid.uuid4().hex[:12]}@example.com"
            self.client.post(
                "/api/v1/auth/register",
                json={"email": email, "password": "load-test-pass", "full_name": "Load"},
                name="/api/v1/auth/register",
            )
            login = self.client.post(
                "/api/v1/auth/login",
                json={"email": email, "password": "load-test-pass"},
                name="/api/v1/auth/login",
            )
            if login.status_code == 200:
                self.token = login.json()["access_token"]

        if self.token:
            self.client.post(
                "/api/v1/orders",
                json={"items": [{"product_id": self.product_id, "quantity": 1}]},
                headers={"Authorization": f"Bearer {self.token}"},
                name="/api/v1/orders",
            )


class BuyerUser(HttpUser):
    """~5% трафика: создание заказов (инвалидирует кэш каталога)."""

    wait_time = between(1.0, 3.0)
    tasks = [BuyerTasks]
    weight = 5
