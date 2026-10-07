from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent
DOCS_DIR = ROOT / "docs"
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DATABASE = DATA_DIR / "recroom.sqlite3"
MAX_UPLOAD_SIZE = 12 * 1024 * 1024
ALLOWED_TYPES = {
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/avif",
    "video/mp4",
    "video/webm",
    "video/quicktime",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
}
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_db() -> sqlite3.Connection:
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
    with connect_db() as connection:
        connection.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def safe_name(value: str) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]", "", value).strip()
    return value[:32] or "Guest"


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class RecRoomHandler(BaseHTTPRequestHandler):
    server_version = "RecRoom/1.0"

    def log_message(self, format: str, *args: object) -> None:
        print("%s - %s" % (self.address_string(), format % args))

    def send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self, limit: int = 1024 * 1024) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError(400, "Invalid content length.") from exc
        if length < 0 or length > limit:
            raise ApiError(413, "Request is too large.")
        return self.rfile.read(length)

    def read_json(self) -> dict:
        try:
            payload = json.loads(self.read_body())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ApiError(400, "Send a valid JSON request.") from exc
        if not isinstance(payload, dict):
            raise ApiError(400, "Send a JSON object.")
        return payload

    def member_for(self, connection: sqlite3.Connection, code: str, token: str) -> sqlite3.Row:
        if not token:
            raise ApiError(401, "Join this space to continue.")
        member = connection.execute(
            """SELECT members.*, rooms.code AS room_code
               FROM members JOIN rooms ON rooms.id = members.room_id
               WHERE rooms.code = ? AND members.token_hash = ? AND members.left_at IS NULL""",
            (code, token_hash(token)),
        ).fetchone()
        if member is None:
            raise ApiError(401, "Your space session has ended. Join the space again.")
        connection.execute("UPDATE members SET last_seen = ? WHERE id = ?", (utc_now(), member["id"]))
        return member

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path.startswith("/api/rooms/") and parsed.path.endswith("/sync"):
                self.room_sync(parsed.path.split("/")[3], parse_qs(parsed.query))
            elif parsed.path == "/media":
                self.serve_media(parse_qs(parsed.query))
            elif parsed.path == "/":
                self.serve_static("index.html")
            elif parsed.path.startswith("/static/"):
                self.serve_static(unquote(parsed.path.removeprefix("/static/")))
            else:
                self.send_json(404, {"error": "Not found."})
        except ApiError as error:
            self.send_json(error.status, {"error": str(error)})
        except sqlite3.Error:
            self.send_json(500, {"error": "The space service is temporarily unavailable."})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/rooms":
                self.create_room()
            elif path == "/api/rooms/join":
                self.join_room()
            elif path.startswith("/api/rooms/"):
                segments = path.strip("/").split("/")
                if len(segments) == 4 and segments[3] == "messages":
                    self.create_message(segments[2])
                elif len(segments) == 4 and segments[3] == "uploads":
                    self.create_upload(segments[2])
                elif len(segments) == 4 and segments[3] == "leave":
                    self.leave_room(segments[2])
                elif len(segments) == 4 and segments[3] == "end":
                    self.end_room(segments[2])
                else:
                    self.send_json(404, {"error": "Not found."})
            else:
                self.send_json(404, {"error": "Not found."})
        except ApiError as error:
            self.send_json(error.status, {"error": str(error)})
        except sqlite3.Error:
            self.send_json(500, {"error": "The space service is temporarily unavailable."})

    def create_room(self) -> None:
        name = safe_name(str(self.read_json().get("name", "")))
        member_token = secrets.token_urlsafe(32)
        with connect_db() as connection:
            for _ in range(10):
                code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(6))
                try:
                    cursor = connection.execute(
                        "INSERT INTO rooms (code, created_at) VALUES (?, ?)", (code, utc_now())
                    )
                    break
                except sqlite3.IntegrityError:
                    continue
            else:
                raise ApiError(503, "Could not create a space. Please try again.")
            connection.execute(
                """INSERT INTO members (room_id, name, token_hash, is_host, joined_at, last_seen)
                   VALUES (?, ?, ?, 1, ?, ?)""",
                (cursor.lastrowid, name, token_hash(member_token), utc_now(), utc_now()),
            )
        self.send_json(201, {"code": code, "token": member_token, "name": name, "is_host": True})

    def join_room(self) -> None:
        payload = self.read_json()
        code = str(payload.get("code", "")).strip().upper()
        if not re.fullmatch(r"[A-Z2-9]{6}", code):
            raise ApiError(400, "Enter a valid 6-character room code.")
        name = safe_name(str(payload.get("name", "")))
        member_token = secrets.token_urlsafe(32)
        with connect_db() as connection:
            room = connection.execute("SELECT id FROM rooms WHERE code = ?", (code,)).fetchone()
            if room is None:
                raise ApiError(404, "We couldn't find that space. Check the code and try again.")
            connection.execute(
                """INSERT INTO members (room_id, name, token_hash, is_host, joined_at, last_seen)
                   VALUES (?, ?, ?, 0, ?, ?)""",
                (room["id"], name, token_hash(member_token), utc_now(), utc_now()),
            )
        self.send_json(200, {"code": code, "token": member_token, "name": name, "is_host": False})

    def room_sync(self, code: str, query: dict[str, list[str]]) -> None:
        token = query.get("member", [""])[0]
        try:
            after = max(0, int(query.get("after", ["0"])[0]))
        except ValueError as exc:
            raise ApiError(400, "Invalid message cursor.") from exc
        with connect_db() as connection:
            member = self.member_for(connection, code, token)
            room = connection.execute("SELECT id FROM rooms WHERE code = ?", (code,)).fetchone()
            if room is None:
                raise ApiError(404, "This space has ended.")
            active_after = datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() - 75, timezone.utc
            ).isoformat(timespec="seconds")
            messages = connection.execute(
                """SELECT messages.id, messages.text, messages.created_at, members.name AS author,
                          attachments.id AS attachment_id, attachments.original_name,
                          attachments.mime_type, attachments.size
                   FROM messages JOIN members ON members.id = messages.member_id
                   LEFT JOIN attachments ON attachments.id = messages.attachment_id
                   WHERE messages.room_id = ? AND messages.id > ?
                   ORDER BY messages.id ASC LIMIT 100""",
                (room["id"], after),
            ).fetchall()
            members = connection.execute(
                """SELECT name, is_host FROM members
                   WHERE room_id = ? AND left_at IS NULL AND last_seen >= ?
                   ORDER BY is_host DESC, joined_at ASC""",
                (room["id"], active_after),
            ).fetchall()
        self.send_json(
            200,
            {
                "code": code,
                "is_host": bool(member["is_host"]),
                "members": [dict(row) for row in members],
                "messages": [
                    {
                        "id": row["id"],
                        "text": row["text"],
                        "created_at": row["created_at"],
                        "author": row["author"],
                        "attachment": (
                            {
                                "id": row["attachment_id"],
                                "name": row["original_name"],
                                "type": row["mime_type"],
                                "size": row["size"],
                                "url": f"/media?room={code}&member={token}&id={row['attachment_id']}",
                            }
                            if row["attachment_id"]
                            else None
                        ),
                    }
                    for row in messages
                ],
            },
        )

    def create_message(self, code: str) -> None:
        payload = self.read_json()
        text = str(payload.get("text", "")).strip()
        if not text or len(text) > 2000:
            raise ApiError(400, "Messages must be between 1 and 2,000 characters.")
        with connect_db() as connection:
            member = self.member_for(connection, code, str(payload.get("member", "")))
            room = connection.execute("SELECT id FROM rooms WHERE code = ?", (code,)).fetchone()
            connection.execute(
                "INSERT INTO messages (room_id, member_id, text, created_at) VALUES (?, ?, ?, ?)",
                (room["id"], member["id"], text, utc_now()),
            )
        self.send_json(201, {"ok": True})

    def create_upload(self, code: str) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ApiError(400, "Invalid content length.") from exc
        if length <= 0 or length > MAX_UPLOAD_SIZE + 64 * 1024:
            raise ApiError(413, "Files must be smaller than 12 MB.")
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            raise ApiError(400, "Upload a file using the provided form.")
        body = self.rfile.read(length)
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + content_type.encode("ascii", "replace") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body
        )
        if not message.is_multipart():
            raise ApiError(400, "Could not read the uploaded file.")
        token = ""
        file_part = None
        for part in message.iter_parts():
            if part.get_param("name", header="content-disposition") == "member":
                token = (part.get_payload(decode=True) or b"").decode("utf-8", "replace")
            elif part.get_param("name", header="content-disposition") == "file":
                file_part = part
        if file_part is None or not file_part.get_filename():
            raise ApiError(400, "Choose a photo, video, or audio file to share.")
        data = file_part.get_payload(decode=True) or b""
        if not data or len(data) > MAX_UPLOAD_SIZE:
            raise ApiError(413, "Files must be smaller than 12 MB.")
        mime_type = file_part.get_content_type().lower()
        if mime_type not in ALLOWED_TYPES:
            raise ApiError(415, "Only photos, videos, and audio files can be shared.")
        original_name = re.sub(
            r"[\x00-\x1f\x7f\"\\]", "", Path(file_part.get_filename()).name
        )[:180] or "shared-file"
        storage_name = uuid.uuid4().hex
        with connect_db() as connection:
            member = self.member_for(connection, code, token)
            room = connection.execute("SELECT id FROM rooms WHERE code = ?", (code,)).fetchone()
            try:
                cursor = connection.execute(
                    """INSERT INTO attachments
                       (room_id, member_id, original_name, storage_name, mime_type, size, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (room["id"], member["id"], original_name, storage_name, mime_type, len(data), utc_now()),
                )
                connection.execute(
                    "INSERT INTO messages (room_id, member_id, created_at, attachment_id) VALUES (?, ?, ?, ?)",
                    (room["id"], member["id"], utc_now(), cursor.lastrowid),
                )
                (UPLOAD_DIR / storage_name).write_bytes(data)
                connection.commit()
            except OSError as exc:
                connection.rollback()
                (UPLOAD_DIR / storage_name).unlink(missing_ok=True)
                raise ApiError(500, "Could not save that file. Please try again.") from exc
            except Exception:
                connection.rollback()
                (UPLOAD_DIR / storage_name).unlink(missing_ok=True)
                raise
        self.send_json(201, {"ok": True})

    def leave_room(self, code: str) -> None:
        payload = self.read_json()
        with connect_db() as connection:
            member = self.member_for(connection, code, str(payload.get("member", "")))
            connection.execute("UPDATE members SET left_at = ? WHERE id = ?", (utc_now(), member["id"]))
        self.send_json(200, {"ok": True})

    def end_room(self, code: str) -> None:
        payload = self.read_json()
        with connect_db() as connection:
            member = self.member_for(connection, code, str(payload.get("member", "")))
            if not member["is_host"]:
                raise ApiError(403, "Only the host can close this space.")
            room = connection.execute("SELECT id FROM rooms WHERE code = ?", (code,)).fetchone()
            files = connection.execute(
                "SELECT storage_name FROM attachments WHERE room_id = ?", (room["id"],)
            ).fetchall()
            connection.execute("DELETE FROM rooms WHERE id = ?", (room["id"],))
        for file in files:
            (UPLOAD_DIR / file["storage_name"]).unlink(missing_ok=True)
        self.send_json(200, {"ok": True})

    def serve_media(self, query: dict[str, list[str]]) -> None:
        code = query.get("room", [""])[0]
        token = query.get("member", [""])[0]
        attachment_id = query.get("id", [""])[0]
        with connect_db() as connection:
            member = self.member_for(connection, code, token)
            attachment = connection.execute(
                """SELECT storage_name, original_name, mime_type
                   FROM attachments WHERE id = ? AND room_id = ?""",
                (attachment_id, member["room_id"]),
            ).fetchone()
        if attachment is None:
            raise ApiError(404, "Shared file not found.")
        path = UPLOAD_DIR / attachment["storage_name"]
        if not path.is_file():
            raise ApiError(404, "Shared file is no longer available.")
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", attachment["mime_type"])
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", 'inline; filename="shared-file"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def serve_static(self, filename: str) -> None:
        path = (DOCS_DIR / filename).resolve()
        if not path.is_relative_to(DOCS_DIR.resolve()) or not path.is_file():
            self.send_json(404, {"error": "Not found."})
            return
        data = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    initialize_database()
    host = os.environ.get("RECROOM_HOST", "127.0.0.1")
    port = int(os.environ.get("RECROOM_PORT", "8000"))
    server = ThreadingHTTPServer((host, port), RecRoomHandler)
    print(f"RecRoom is running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down RecRoom.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
