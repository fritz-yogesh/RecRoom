# RecRoom

RecRoom is a small, self-hosted hangout: create a space, invite people with its six-character code, chat in real time (through lightweight polling), and share photos, videos, and audio. Switch between light and dark appearance from the header; your choice is saved in the browser.

## Run it

Requires Python 3.9 or newer. The server and SQLite database use only Python's standard library.

```sh
python3 server.py
```

Open <http://127.0.0.1:8000>. Set `RECROOM_HOST` and `RECROOM_PORT` to change the bind address and port. SQLite data and uploaded files are stored in `data/`.

Do not open `docs/index.html` directly for the full app. A `file://` page cannot connect to the Python/SQLite backend; it displays a launch notice and disables space actions. Run `python3 server.py` from the project folder, then open the local URL above.

## Spaces and sharing

- A space host can copy an invite link or share the six-character space code.
- Visitors choose a display name and join with that code. Existing `?room=CODE` invite links continue to work.
- Messages, space membership, and uploaded-file metadata are stored in SQLite; uploaded media is stored under `data/uploads/`.
- The host can end the space, which removes its messages, members, and uploaded media.
- Photos, videos, and audio are supported up to 12 MB per file.

This is a lightweight starter app intended for trusted, small groups. Put it behind HTTPS and an appropriate production web server before exposing it to the public internet; space codes are invitations, not a substitute for account-based access control or abuse protection.
