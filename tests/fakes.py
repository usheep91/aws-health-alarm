from __future__ import annotations

import copy
from collections import defaultdict

from aha.domain.repository import ConcurrentModification


class InMemoryRepository:
    def __init__(self):
        self.events = {}
        self.groups = {}
        self.tracking = {}
        self.entities = defaultdict(dict)
        self.audit = defaultdict(list)
        self.notices = {}
        self.cursors = {}

    def get_event(self, event_arn):
        return copy.deepcopy(self.events.get(event_arn))

    def put_event(self, record):
        self.events[record.event_arn] = copy.deepcopy(record)

    def get_group(self, group_id):
        return copy.deepcopy(self.groups.get(group_id))

    def put_group(self, group):
        existing = self.groups.get(group.group_id)
        if existing is None:
            self.groups[group.group_id] = copy.deepcopy(group)
        else:
            existing.event_arns |= group.event_arns

    def get_tracking(self, tracking_id):
        return copy.deepcopy(self.tracking.get(tracking_id))

    def create_tracking(self, item):
        if item.tracking_id in self.tracking:
            return False
        item.version = 1
        self.tracking[item.tracking_id] = copy.deepcopy(item)
        return True

    def save_tracking(self, item):
        stored = self.tracking.get(item.tracking_id)
        if stored is None or stored.version != item.version:
            raise ConcurrentModification(item.tracking_id)
        item.version += 1
        self.tracking[item.tracking_id] = copy.deepcopy(item)

    def list_group_tracking(self, group_id):
        items = [t for t in self.tracking.values() if t.group_id == group_id]
        return [copy.deepcopy(t) for t in sorted(items, key=lambda t: t.account_id)]

    def list_by_state(self, state):
        items = [t for t in self.tracking.values() if t.state is state]
        return [copy.deepcopy(t) for t in sorted(items, key=lambda t: (t.due_date, t.tracking_id))]

    def upsert_entities(self, tracking_id, entities, now):
        for entity in entities:
            self.entities[tracking_id][entity.value] = entity

    def list_entities(self, tracking_id):
        return [self.entities[tracking_id][k] for k in sorted(self.entities[tracking_id])]

    def append_audit(self, tracking_id, entry):
        self.audit[tracking_id].append(entry)

    def list_audit(self, tracking_id):
        return list(self.audit[tracking_id])

    def get_notices(self, group_id):
        return [copy.deepcopy(n) for (g, _), n in sorted(self.notices.items()) if g == group_id]

    def put_notice(self, notice):
        self.notices[(notice.group_id, notice.channel_id)] = copy.deepcopy(notice)

    def get_cursor(self, name):
        return self.cursors.get(name)

    def set_cursor(self, name, value):
        self.cursors[name] = value
