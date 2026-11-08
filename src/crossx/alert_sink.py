"""Optional local-only Alertmanager webhook receiver; writes bounded JSON to stdout."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if self.path != "/alerts" or not 0 < length <= 65536:
            self.send_error(400)
            return
        try:
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or not isinstance(payload.get("alerts"), list):
                raise ValueError("invalid webhook")
        except (ValueError, TypeError):
            self.send_error(400)
            return
        print(json.dumps({"event": "local_alert_notification", "payload": payload}), flush=True)
        self.send_response(200)
        self.end_headers()


def main() -> None:
    HTTPServer(("0.0.0.0", 8002), Handler).serve_forever()


if __name__ == "__main__":
    main()
