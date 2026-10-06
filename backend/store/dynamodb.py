"""DynamoDB implementation of StateStore (single table, on-demand capacity, no indexes).

The state machine reads its own writes immediately (create a package, then look for the open
package), so every read is a strongly consistent read on the base table; GSIs can't do that.

Layout (one partition per entity kind; a home has tens of items a day, far below partition limits):
    pk               sk                              item
    EVENT            <sim_ts>#<event_id>             event attributes
    PACKAGE          <arrived_sim_ts>#<id>           package attributes
    NOTIFICATION     <sim_ts>#<insert ns>#<id>       notification attributes
    ID#<kind>#<id>   ID                              {"ref_sk": <sk above>}   (lookup by id)

Query on pk returns items in sim-time order (sim_ts strings share the home-timezone offset,
so they sort lexically), matching the SQLite store. Nested values are stored as JSON strings.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import fields
from decimal import Decimal
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from backend.store.base import EventRecord, Notification, Package, PackageStatus, StateStore

DEFAULT_TABLE = "doorsight-state"
_INT_FIELDS = {"sim_hour", "frame_count"}
_JSON_FIELDS = {"arrival_view", "extra"}
_KINDS = ("EVENT", "PACKAGE", "NOTIFICATION")


def _to_item(data: dict[str, Any]) -> dict[str, Any]:
    item = {}
    for k, v in data.items():
        if v is None:
            continue  # absent attribute == None on read
        if k in _JSON_FIELDS:
            item[k] = json.dumps(v)
        elif isinstance(v, float):
            item[k] = Decimal(str(v))
        else:
            item[k] = v
    return item


def _from_item(item: dict[str, Any], cls: type) -> dict[str, Any]:
    names = {f.name for f in fields(cls)}
    out: dict[str, Any] = {}
    for k, v in item.items():
        if k not in names:
            continue  # pk / sk
        if k in _JSON_FIELDS:
            v = json.loads(v)
        elif isinstance(v, Decimal):
            v = int(v) if k in _INT_FIELDS else float(v)
        out[k] = v
    return out


class DynamoDBStore(StateStore):
    def __init__(self, table_name: str | None = None, region: str | None = None, resource: Any = None):
        self.table_name = table_name or os.getenv("DYNAMODB_TABLE", DEFAULT_TABLE)
        resource = resource or boto3.resource(
            "dynamodb",
            region_name=region or os.getenv("AWS_REGION", "us-east-1"),
            config=Config(connect_timeout=5, read_timeout=10, retries={"max_attempts": 3}),
        )
        self.table = resource.Table(self.table_name)

    # --- helpers -------------------------------------------------------------

    def _query(self, kind: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {"KeyConditionExpression": Key("pk").eq(kind), "ConsistentRead": True}
        while True:
            resp = self.table.query(**kwargs)
            items.extend(resp["Items"])
            if "LastEvaluatedKey" not in resp:
                return items
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    def _get_by_id(self, kind: str, id_: str) -> dict[str, Any] | None:
        ptr = self.table.get_item(Key={"pk": f"ID#{kind}#{id_}", "sk": "ID"}, ConsistentRead=True).get("Item")
        if not ptr:
            return None
        return self.table.get_item(Key={"pk": kind, "sk": ptr["ref_sk"]}, ConsistentRead=True).get("Item")

    def _put(self, kind: str, id_: str, sk: str, data: dict[str, Any], must_be_new: bool = False) -> None:
        """Write the item and its id pointer; drop the old item if the sort key changed."""
        ptr_key = {"pk": f"ID#{kind}#{id_}", "sk": "ID"}
        old = self.table.get_item(Key=ptr_key, ConsistentRead=True).get("Item")
        if old and must_be_new:
            raise ValueError(f"{kind.lower()} {id_} already exists")
        self.table.put_item(Item={"pk": kind, "sk": sk, **_to_item(data)})
        self.table.put_item(Item={**ptr_key, "ref_sk": sk})
        if old and old["ref_sk"] != sk:
            self.table.delete_item(Key={"pk": kind, "sk": old["ref_sk"]})

    # --- events --------------------------------------------------------------

    def add_event(self, event: EventRecord) -> None:
        self._put("EVENT", event.event_id, f"{event.sim_ts}#{event.event_id}", event.to_dict())

    def get_event(self, event_id: str) -> EventRecord | None:
        item = self._get_by_id("EVENT", event_id)
        return EventRecord(**_from_item(item, EventRecord)) if item else None

    def list_events(self, event_types: list[str] | None = None) -> list[EventRecord]:
        events = [EventRecord(**_from_item(i, EventRecord)) for i in self._query("EVENT")]
        return [e for e in events if not event_types or e.event_type in event_types]

    # --- packages ------------------------------------------------------------

    def create_package(self, package: Package) -> None:
        self._put("PACKAGE", package.id, f"{package.arrived_sim_ts}#{package.id}", package.to_dict(), must_be_new=True)

    def update_package(self, package: Package) -> None:
        self._put("PACKAGE", package.id, f"{package.arrived_sim_ts}#{package.id}", package.to_dict())

    @staticmethod
    def _package(item: dict[str, Any]) -> Package:
        d = _from_item(item, Package)
        d["status"] = PackageStatus(d["status"])
        return Package(**d)

    def get_package(self, package_id: str) -> Package | None:
        item = self._get_by_id("PACKAGE", package_id)
        return self._package(item) if item else None

    def list_packages(self, statuses: list[PackageStatus] | None = None) -> list[Package]:
        packages = [self._package(i) for i in self._query("PACKAGE")]
        return [p for p in packages if not statuses or p.status in statuses]

    # --- notifications -------------------------------------------------------

    def add_notification(self, notification: Notification) -> None:
        sk = f"{notification.sim_ts}#{time.time_ns():020d}#{notification.id}"
        self._put("NOTIFICATION", notification.id, sk, notification.to_dict(), must_be_new=True)

    def list_notifications(self, audience: str | None = None) -> list[Notification]:
        notes = [Notification(**_from_item(i, Notification)) for i in self._query("NOTIFICATION")]
        return [n for n in notes if not audience or n.audience == audience]

    def clear(self) -> None:
        with self.table.batch_writer() as batch:
            for kind in _KINDS:
                for item in self._query(kind):
                    batch.delete_item(Key={"pk": kind, "sk": item["sk"]})
                    batch.delete_item(Key={"pk": f"ID#{kind}#{_item_id(kind, item)}", "sk": "ID"})


def _item_id(kind: str, item: dict[str, Any]) -> str:
    return item["event_id"] if kind == "EVENT" else item["id"]


def create_table(resource: Any, table_name: str = DEFAULT_TABLE) -> Any:
    """Create the table (on-demand). Used by tests; scripts/aws_setup.sh does the same via the CLI."""
    return resource.create_table(
        TableName=table_name,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"},
                              {"AttributeName": "sk", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
    )
