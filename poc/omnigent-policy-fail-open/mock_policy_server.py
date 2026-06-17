#!/usr/bin/env python3
"""Mock Omnigent policy server for the fail-open PoC.

Handles POST /v1/sessions/{id}/policies/evaluate and behaves per the mode
given on argv, to simulate the failure conditions that make the native hook
fail open:

  ok       -> 200 with a DENY verdict (healthy server)
  503      -> 503 (any non-2xx; upstream calls resp.raise_for_status())
  nonjson  -> 200 with a non-JSON body (upstream resp.json() raises)
  empty    -> 200 with an empty body (upstream: `if not resp.content`)
  hang     -> sleeps past the client timeout (upstream: httpx timeout)

Pure stdlib so it runs anywhere with python3.
"""
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
MODE = sys.argv[2] if len(sys.argv) > 2 else "ok"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):  # silence
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        _ = self.rfile.read(length)

        if MODE == "hang":
            time.sleep(5)  # client timeout is shorter; it gives up first

        if MODE == "503":
            self.send_response(503)
            self.end_headers()
            self.wfile.write(b"service unavailable")
            return
        if MODE == "nonjson":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<html>not json</html>")
            return
        if MODE == "empty":
            self.send_response(200)
            self.end_headers()
            return

        # ok / hang(after sleep): healthy DENY verdict
        body = json.dumps({"action": "DENY", "reason": "policy: command denied"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
