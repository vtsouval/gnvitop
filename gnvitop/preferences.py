"""Durable display names and groups, separate from SSH connection settings."""
import json
import os
from pathlib import Path
import re
import tempfile
import threading

LOCK = threading.RLock()


def config_path():
    return Path(os.environ.get("GNVITOP_PREFERENCES", Path.home() / ".config/gnvitop/dashboard.json"))


def validate(data):
    if not isinstance(data, dict) or set(data) - {"version", "groups", "hosts"}:
        raise ValueError("Expected groups and hosts settings")
    groups, hosts = data.get("groups", []), data.get("hosts", {})
    if not isinstance(groups, list) or len(groups) > 100 or not isinstance(hosts, dict) or len(hosts) > 1000:
        raise ValueError("Invalid groups or hosts")
    ids = set()
    clean_groups = []
    def name(value):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 100 or any(ord(c) < 32 for c in value):
            raise ValueError("Names must contain 1–100 printable characters")
        return value.strip()
    for group in groups:
        if not isinstance(group, dict) or set(group) != {"id", "name"}:
            raise ValueError("Each group needs an id and name")
        identifier = group["id"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", identifier) or identifier in ids:
            raise ValueError("Group IDs must be unique")
        ids.add(identifier)
        clean_groups.append({"id": identifier, "name": name(group["name"])})
    clean_hosts = {}
    for alias, settings in hosts.items():
        if not isinstance(alias, str) or not alias or len(alias) > 255 or any(ord(c) < 32 for c in alias):
            raise ValueError("Invalid SSH alias")
        if not isinstance(settings, dict) or set(settings) - {"name", "group"}:
            raise ValueError("Invalid server settings")
        group = settings.get("group") or None
        if group is not None and group not in ids:
            raise ValueError("A server refers to a group that does not exist")
        clean_hosts[alias] = {"name": name(settings.get("name", alias)), "group": group}
    return {"version": 1, "groups": clean_groups, "hosts": clean_hosts}


def load():
    with LOCK:
        try:
            return validate(json.loads(config_path().read_text()))
        except FileNotFoundError:
            return {"version": 1, "groups": [], "hosts": {}}


def save(data):
    clean = validate(data)
    with LOCK:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".dashboard-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(clean, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return clean
