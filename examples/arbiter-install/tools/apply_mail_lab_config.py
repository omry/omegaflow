#!/usr/bin/env python3
"""Apply local mail-lab settings to staged Arbiter recording config."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    pass


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if value is None or value == "":
        raise ConfigError(f"missing required environment variable: {name}")
    return value


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"config file must contain a mapping: {path}")
    return data


def write_yaml(path: Path, data: dict[str, Any], *, package: str | None = None) -> None:
    content = yaml.safe_dump(data, sort_keys=False, default_flow_style=False)
    if package is not None:
        content = f"# @package {package}\n" + content
    path.write_text(content, encoding="utf-8")


def account_env_suffix(account: str) -> str:
    suffix = account.upper().replace("-", "_")
    if not suffix.endswith("_ACCOUNT"):
        suffix = f"{suffix}_ACCOUNT"
    return suffix


def update_account_files(config_dir: Path, *, account: str, plugins: set[str]) -> None:
    env_suffix = account_env_suffix(account)
    imap_account = config_dir / "arbiter" / "account" / "imap" / f"{account}.yaml"
    smtp_account = config_dir / "arbiter" / "account" / "smtp" / f"{account}.yaml"

    if "imap" in plugins:
        imap = load_yaml(imap_account)
        imap.update(
            {
                "host": require_env("MAIL_LAB_IMAP_HOST"),
                "port": int(require_env("MAIL_LAB_IMAP_PORT")),
                "username": f"${{oc.env:IMAP_{env_suffix}_USERNAME}}",
                "password": f"${{oc.env:IMAP_{env_suffix}_PASSWORD}}",
                "tls": "none",
                "verify_peer": False,
                "default_folder": "INBOX",
                "folders": {
                    "INBOX": {
                        "description": "Local recording inbox.",
                        "kind": "INBOX",
                    },
                    "Sent": {
                        "description": "Local recording sent mail.",
                        "kind": "SENT",
                    },
                    "Trash": {
                        "description": "Local recording trash.",
                        "kind": "TRASH",
                    },
                },
            }
        )
        write_yaml(imap_account, imap, package=f"arbiter.account.imap.{account}")

    if "smtp" in plugins:
        smtp = load_yaml(smtp_account)
        smtp.update(
            {
                "host": require_env("MAIL_LAB_SMTP_HOST"),
                "port": int(require_env("MAIL_LAB_SMTP_PORT")),
                "authenticate": True,
                "username": f"${{oc.env:SMTP_{env_suffix}_USERNAME}}",
                "password": f"${{oc.env:SMTP_{env_suffix}_PASSWORD}}",
                "from_email": os.environ.get("BOT_FROM_EMAIL")
                or require_env("BOT_EMAIL"),
                "from_name": "Arbiter",
                "tls": "none",
                "verify_peer": False,
            }
        )
        write_yaml(smtp_account, smtp, package=f"arbiter.account.smtp.{account}")


def update_policy_files(config_dir: Path, *, account: str, plugins: set[str]) -> None:
    imap_policy = config_dir / "arbiter" / "policy" / "imap" / f"{account}_policy.yaml"
    smtp_policy = config_dir / "arbiter" / "policy" / "smtp" / f"{account}_policy.yaml"

    if "imap" in plugins:
        imap = load_yaml(imap_policy)
        imap.update(
            {
                "folder_access": {"rules": [{"allow_glob": "*"}]},
                "operation_defaults": {
                    "read": "allow",
                    "search": "allow",
                    "move": True,
                    "mark_read": "allow",
                    "delete": "allow",
                    "folder_append": "allow",
                    "system_flags": {
                        "SEEN": "read_write",
                        "FLAGGED": "read_write",
                        "ANSWERED": "read_write",
                        "DELETED": "read_write",
                        "DRAFT": "read_write",
                    },
                    "user_flags": {},
                },
                "folders": {},
            }
        )
        write_yaml(imap_policy, imap, package=f"arbiter.policy.imap.{account}_policy")

    if "smtp" in plugins:
        smtp = load_yaml(smtp_policy)
        smtp.update(
            {
                "limits": {
                    "max_messages_per_minute": None,
                    "max_recipients_per_message": None,
                },
                "recipient_policy": {
                    "allowed_recipients": [],
                    "blocked_recipients": [],
                    "allowed_domain_patterns": [],
                    "blocked_domain_patterns": [],
                },
                "sent_copy": {
                    "enabled": True,
                    "on_failure": "warn",
                },
            }
        )
        write_yaml(smtp_policy, smtp, package=f"arbiter.policy.smtp.{account}_policy")


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def write_env_file(path: Path, values: dict[str, str], *, account: str) -> None:
    env_suffix = account_env_suffix(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    imap_keys = [
        f"IMAP_{env_suffix}_USERNAME",
        f"IMAP_{env_suffix}_PASSWORD",
    ]
    smtp_keys = [
        f"SMTP_{env_suffix}_USERNAME",
        f"SMTP_{env_suffix}_PASSWORD",
    ]
    ordered_keys = imap_keys + smtp_keys
    lines = []
    if any(key in values for key in imap_keys):
        lines.append("# arbiter-imap")
    for key in imap_keys:
        if key in values:
            lines.append(f"{key}={values[key]}")
    if any(key in values for key in smtp_keys):
        if lines:
            lines.append("")
        lines.append("# arbiter-smtp")
    for key in smtp_keys:
        if key in values:
            lines.append(f"{key}={values[key]}")
    extra_keys = sorted(key for key in values if key not in ordered_keys)
    if extra_keys:
        if lines:
            lines.append("")
        lines.append("# misc")
    for key in extra_keys:
        lines.append(f"{key}={values[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_env_file(config_dir: Path, *, account: str, plugins: set[str]) -> None:
    env_suffix = account_env_suffix(account)
    imap_username = f"IMAP_{env_suffix}_USERNAME"
    imap_password = f"IMAP_{env_suffix}_PASSWORD"
    smtp_username = f"SMTP_{env_suffix}_USERNAME"
    smtp_password = f"SMTP_{env_suffix}_PASSWORD"
    env_file = config_dir.parent / ".arbiter.env"
    values = read_env_file(env_file)
    if "imap" in plugins:
        values.update(
            {
                imap_username: require_env("IMAP_BOT_ACCOUNT_USERNAME"),
                imap_password: require_env("IMAP_BOT_ACCOUNT_PASSWORD"),
            }
        )
    if "smtp" in plugins:
        values.update(
            {
                smtp_username: require_env("SMTP_BOT_ACCOUNT_USERNAME"),
                smtp_password: require_env("SMTP_BOT_ACCOUNT_PASSWORD"),
            }
        )
    write_env_file(env_file, values, account=account)


def parse_plugins(value: str) -> set[str]:
    plugins = {item.strip() for item in value.split(",") if item.strip()}
    unknown = plugins - {"imap", "smtp"}
    if unknown:
        raise ConfigError(f"unknown plugin(s): {', '.join(sorted(unknown))}")
    return plugins or {"imap", "smtp"}


def apply_mail_lab_config(
    config_dir: Path,
    *,
    account: str = "bot",
    plugins: set[str] | None = None,
    update_env: bool,
) -> None:
    if plugins is None:
        plugins = {"imap", "smtp"}
    update_account_files(config_dir, account=account, plugins=plugins)
    update_policy_files(config_dir, account=account, plugins=plugins)
    if update_env:
        update_env_file(config_dir, account=account, plugins=plugins)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--account", default="bot")
    parser.add_argument("--plugins", default="imap,smtp")
    parser.add_argument(
        "--update-env",
        action="store_true",
        help="Also write mail-lab credentials into the staged .arbiter.env file.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        apply_mail_lab_config(
            args.config_dir,
            account=args.account,
            plugins=parse_plugins(args.plugins),
            update_env=args.update_env,
        )
    except (ConfigError, ValueError) as exc:
        parser.exit(1, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
