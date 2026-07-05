#!/usr/bin/env python3
"""Run a local SMTP/IMAP mailbox for Arbiter media recordings."""

from __future__ import annotations

import argparse
import json
import shlex
import signal
import socket
import sys
import threading
import warnings
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from aiosmtpd.controller import Controller


DEFAULT_USERNAME = "bot@example.test"
DEFAULT_PASSWORD = "bot-password"
DEFAULT_FROM_EMAIL = "bot@example.test"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_CONTAINER_HOST = "host.docker.internal"


class MailLabError(RuntimeError):
    pass


@dataclass
class StoredMessage:
    uid: int
    content: bytes
    flags: set[str] = field(default_factory=set)


class MailboxStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_uid = 1
        self._folders: dict[str, list[StoredMessage]] = {
            "INBOX": [],
            "Sent": [],
            "Trash": [],
        }

    def folders(self) -> list[str]:
        with self._lock:
            return list(self._folders)

    def append(self, folder: str, content: bytes, flags: set[str] | None = None) -> int:
        with self._lock:
            messages = self._folders.setdefault(folder, [])
            uid = self._next_uid
            self._next_uid += 1
            messages.append(
                StoredMessage(uid=uid, content=content, flags=flags or set())
            )
            return uid

    def messages(self, folder: str) -> list[StoredMessage]:
        with self._lock:
            return list(self._folders.get(folder, []))

    def get(self, folder: str, uid: int) -> StoredMessage | None:
        with self._lock:
            for message in self._folders.get(folder, []):
                if message.uid == uid:
                    return StoredMessage(
                        uid=message.uid,
                        content=message.content,
                        flags=set(message.flags),
                    )
            return None

    def search(self, folder: str, query: str | None = None) -> list[int]:
        query_bytes = query.encode("utf-8").lower() if query else None
        with self._lock:
            result: list[int] = []
            for message in self._folders.get(folder, []):
                if query_bytes is None or query_bytes in message.content.lower():
                    result.append(message.uid)
            return result


class DeliveringSMTPHandler:
    def __init__(self, *, store: MailboxStore, recipient: str) -> None:
        self.store = store
        self.recipient = recipient.lower()

    async def handle_MAIL(
        self,
        server: object,
        session: object,
        envelope: Any,
        address: str,
        options: list[str],
    ) -> str:
        envelope.mail_from = address
        return "250 OK"

    async def handle_RCPT(
        self,
        server: object,
        session: object,
        envelope: Any,
        address: str,
        options: list[str],
    ) -> str:
        if address.lower() != self.recipient:
            return "550 Recipient rejected"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server: object, session: object, envelope: Any) -> str:
        content = getattr(envelope, "original_content", None)
        if content is None:
            content = envelope.content
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.store.append("INBOX", bytes(content))
        return "250 Message accepted for delivery"


class MailLabSMTPController(Controller):
    def factory(self) -> Any:
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Requiring AUTH while not requiring TLS can lead to security vulnerabilities!",
                category=UserWarning,
                module="aiosmtpd.smtp",
            )
            return super().factory()


class LocalIMAPServer:
    def __init__(
        self,
        *,
        store: MailboxStore,
        username: str,
        password: str,
        host: str,
        port: int,
    ) -> None:
        self.store = store
        self.username = username
        self.password = password
        self.host = host
        self.port = port
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._socket: socket.socket | None = None

    def start(self) -> None:
        self._thread.start()
        if not self._ready.wait(timeout=5):
            raise MailLabError("IMAP server failed to start")

    def stop(self) -> None:
        self._stop.set()
        try:
            with socket.create_connection((self.host, self.port), timeout=1):
                pass
        except OSError:
            pass
        self._thread.join(timeout=5)
        if self._socket is not None:
            self._socket.close()

    def _serve(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            self._socket = listener
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.host, self.port))
            self.port = listener.getsockname()[1]
            listener.listen()
            listener.settimeout(0.2)
            self._ready.set()
            while not self._stop.is_set():
                try:
                    connection, _addr = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                with connection:
                    self._handle_connection(connection)

    def _handle_connection(self, connection: socket.socket) -> None:
        connection.sendall(b"* OK Arbiter media IMAP server ready\r\n")
        reader = connection.makefile("rb")
        state: dict[str, str | None] = {"folder": None}
        while not self._stop.is_set():
            raw_line = reader.readline()
            if not raw_line:
                return
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            tag, command, rest = self._split_command(line)
            if command == "APPEND":
                self._handle_append(connection, reader, tag, rest)
                continue
            response = self._response(tag, command, rest, state)
            connection.sendall(response)
            if command == "LOGOUT":
                return

    def _split_command(self, line: str) -> tuple[str, str, str]:
        parts = line.split(" ", 2)
        tag = parts[0]
        command = parts[1].upper() if len(parts) >= 2 else ""
        rest = parts[2] if len(parts) >= 3 else ""
        return tag, command, rest

    def _response(
        self, tag: str, command: str, rest: str, state: dict[str, str | None]
    ) -> bytes:
        if command == "CAPABILITY":
            return (
                b"* CAPABILITY IMAP4rev1 UIDPLUS MOVE\r\n"
                + f"{tag} OK CAPABILITY completed\r\n".encode()
            )
        if command == "LOGIN":
            if self._login_ok(rest):
                return f"{tag} OK LOGIN completed\r\n".encode()
            return f"{tag} NO LOGIN failed\r\n".encode()
        if command == "NOOP":
            return f"{tag} OK NOOP completed\r\n".encode()
        if command == "LIST":
            lines = [
                f'* LIST (\\HasNoChildren) "/" "{folder}"\r\n'
                for folder in self.store.folders()
            ]
            lines.append(f"{tag} OK LIST completed\r\n")
            return "".join(lines).encode()
        if command in {"EXAMINE", "SELECT"}:
            folder = self._mailbox_name(rest)
            if folder not in self.store.folders():
                return f"{tag} NO no such mailbox\r\n".encode()
            state["folder"] = folder
            count = len(self.store.messages(folder))
            uid_next = max(self.store.search(folder), default=0) + 1
            access = "READ-ONLY" if command == "EXAMINE" else "READ-WRITE"
            return (
                b"* FLAGS (\\Seen \\Answered \\Flagged \\Deleted \\Draft)\r\n"
                + f"* {count} EXISTS\r\n".encode()
                + b"* OK [UIDVALIDITY 1] UIDs valid\r\n"
                + f"* OK [UIDNEXT {uid_next}] Predicted next UID\r\n".encode()
                + f"{tag} OK [{access}] {command} completed\r\n".encode()
            )
        if command == "UID":
            return self._uid_response(tag, rest, state.get("folder") or "INBOX")
        if command == "EXPUNGE":
            return f"{tag} OK EXPUNGE completed\r\n".encode()
        if command == "LOGOUT":
            return b"* BYE logging out\r\n" + f"{tag} OK LOGOUT completed\r\n".encode()
        return f"{tag} BAD unsupported command\r\n".encode()

    def _uid_response(self, tag: str, rest: str, folder: str) -> bytes:
        subcommand, _, args = rest.partition(" ")
        subcommand = subcommand.upper()
        if subcommand == "SEARCH":
            query = self._search_query(args)
            uids = self.store.search(folder, query)
            uid_list = " ".join(str(uid) for uid in uids)
            return f"* SEARCH {uid_list}\r\n{tag} OK SEARCH completed\r\n".encode()
        if subcommand == "FETCH":
            uid_text, _, fetch_items = args.partition(" ")
            try:
                uid = int(uid_text)
            except ValueError:
                return f"{tag} BAD invalid UID\r\n".encode()
            message = self.store.get(folder, uid)
            if message is None:
                return f"{tag} OK FETCH completed\r\n".encode()
            fetch_items = fetch_items.upper()
            if "FLAGS" in fetch_items and "RFC822" not in fetch_items:
                flags = " ".join(sorted(message.flags))
                return (
                    f"* {uid} FETCH (UID {uid} FLAGS ({flags}))\r\n"
                    f"{tag} OK FETCH completed\r\n"
                ).encode()
            return (
                (
                    f"* {uid} FETCH "
                    f"(UID {uid} RFC822 {{{len(message.content)}}}\r\n"
                ).encode()
                + message.content
                + (b")\r\n" + f"{tag} OK FETCH completed\r\n".encode())
            )
        if subcommand == "STORE":
            uid, _, _flags = args.partition(" ")
            return (
                f"* {uid} FETCH (UID {uid} FLAGS (\\Seen))\r\n"
                f"{tag} OK STORE completed\r\n"
            ).encode()
        if subcommand == "COPY":
            return f"{tag} OK COPY completed\r\n".encode()
        if subcommand == "MOVE":
            return f"{tag} OK MOVE completed\r\n".encode()
        if subcommand == "EXPUNGE":
            return f"{tag} OK UID EXPUNGE completed\r\n".encode()
        return f"{tag} BAD unsupported UID command\r\n".encode()

    def _handle_append(
        self,
        connection: socket.socket,
        reader: Any,
        tag: str,
        rest: str,
    ) -> None:
        literal_size = self._literal_size(rest)
        if literal_size is None:
            connection.sendall(f"{tag} BAD APPEND requires literal data\r\n".encode())
            return
        folder = self._mailbox_name(rest.rsplit("{", 1)[0])
        connection.sendall(b"+ Ready for literal data\r\n")
        content = reader.read(literal_size)
        reader.readline()
        self.store.append(folder, content, flags={"\\Seen"})
        connection.sendall(f"{tag} OK APPEND completed\r\n".encode())

    def _login_ok(self, rest: str) -> bool:
        try:
            values = shlex.split(rest)
        except ValueError:
            return False
        if len(values) < 2:
            return False
        return values[0] == self.username and values[1] == self.password

    def _mailbox_name(self, value: str) -> str:
        try:
            values = shlex.split(value)
        except ValueError:
            values = []
        if not values:
            return value.strip().strip('"')
        return values[0]

    def _literal_size(self, value: str) -> int | None:
        if "{" not in value or not value.endswith("}"):
            return None
        raw_size = value.rsplit("{", 1)[1][:-1]
        try:
            return int(raw_size)
        except ValueError:
            return None

    def _search_query(self, args: str) -> str | None:
        normalized = args.strip()
        if not normalized or normalized.upper() == "ALL":
            return None
        if normalized.upper().startswith("TEXT "):
            try:
                values = shlex.split(normalized[5:])
            except ValueError:
                return normalized[5:].strip('"')
            return values[0] if values else None
        return None


class MailLab:
    def __init__(
        self,
        *,
        username: str = DEFAULT_USERNAME,
        password: str = DEFAULT_PASSWORD,
        host: str = DEFAULT_HOST,
        smtp_port: int = 0,
        imap_port: int = 0,
    ) -> None:
        self.username = username
        self.password = password
        self.host = host
        self.smtp_port = smtp_port
        self.imap_port = imap_port
        self.store = MailboxStore()
        self._smtp_controller: Controller | None = None
        self._imap_server: LocalIMAPServer | None = None

    def start(self) -> None:
        smtp_port = self.smtp_port or free_port(self.host)
        handler = DeliveringSMTPHandler(store=self.store, recipient=self.username)
        self._smtp_controller = MailLabSMTPController(
            handler,
            hostname=self.host,
            port=smtp_port,
            ready_timeout=5.0,
            auth_required=True,
            auth_require_tls=False,
            auth_callback=self._auth_callback,
        )
        self._smtp_controller.start()
        self.smtp_port = smtp_port

        self._imap_server = LocalIMAPServer(
            store=self.store,
            username=self.username,
            password=self.password,
            host=self.host,
            port=self.imap_port,
        )
        self._imap_server.start()
        self.imap_port = self._imap_server.port

    def stop(self) -> None:
        if self._smtp_controller is not None:
            self._smtp_controller.stop()
            self._smtp_controller = None
        if self._imap_server is not None:
            self._imap_server.stop()
            self._imap_server = None

    def _auth_callback(self, mechanism: str, login: bytes, password: bytes) -> bool:
        return (
            login.decode("utf-8", errors="replace") == self.username
            and password.decode("utf-8", errors="replace") == self.password
        )

    def env_values(
        self, *, container_host: str = DEFAULT_CONTAINER_HOST
    ) -> dict[str, str]:
        return {
            "MAIL_LAB_HOST": self.host,
            "MAIL_LAB_CONTAINER_HOST": container_host,
            "MAIL_LAB_SMTP_HOST": container_host,
            "MAIL_LAB_SMTP_PORT": str(self.smtp_port),
            "MAIL_LAB_IMAP_HOST": container_host,
            "MAIL_LAB_IMAP_PORT": str(self.imap_port),
            "BOT_EMAIL": self.username,
            "BOT_USERNAME": self.username,
            "BOT_PASSWORD": self.password,
            "BOT_FROM_EMAIL": DEFAULT_FROM_EMAIL,
            "SMTP_BOT_ACCOUNT_USERNAME": self.username,
            "SMTP_BOT_ACCOUNT_PASSWORD": self.password,
            "IMAP_BOT_ACCOUNT_USERNAME": self.username,
            "IMAP_BOT_ACCOUNT_PASSWORD": self.password,
        }


def free_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def write_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in sorted(values.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def seed_message(store: MailboxStore, *, to_addr: str = DEFAULT_USERNAME) -> int:
    message = EmailMessage()
    message["From"] = "Ops <ops@example.test>"
    message["To"] = to_addr
    message["Subject"] = "Welcome to the Arbiter mail lab"
    message.set_content("This local message is available through the IMAP bot account.")
    return store.append("INBOX", message.as_bytes())


def run_lab(args: argparse.Namespace) -> int:
    lab = MailLab(
        username=args.username,
        password=args.password,
        host=args.host,
        smtp_port=args.smtp_port,
        imap_port=args.imap_port,
    )
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    lab.start()
    try:
        if args.seed:
            seed_message(lab.store, to_addr=args.username)
        values = lab.env_values(container_host=args.container_host)
        if args.env_file is not None:
            write_env_file(args.env_file, values)
        if args.json_file is not None:
            args.json_file.parent.mkdir(parents=True, exist_ok=True)
            args.json_file.write_text(
                json.dumps(values, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        if args.ready_file is not None:
            args.ready_file.parent.mkdir(parents=True, exist_ok=True)
            args.ready_file.write_text("ready\n", encoding="utf-8")
        print(
            "mail lab ready: "
            f"smtp={lab.host}:{lab.smtp_port} imap={lab.host}:{lab.imap_port}",
            flush=True,
        )
        while not stop.wait(timeout=3600):
            pass
    finally:
        lab.stop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--container-host", default=DEFAULT_CONTAINER_HOST)
    parser.add_argument("--smtp-port", type=int, default=0)
    parser.add_argument("--imap-port", type=int, default=0)
    parser.add_argument("--username", default=DEFAULT_USERNAME)
    parser.add_argument("--password", default=DEFAULT_PASSWORD)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--json-file", type=Path)
    parser.add_argument("--ready-file", type=Path)
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Seed the mailbox with one welcome message before reporting ready.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run_lab(args)
    except MailLabError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
