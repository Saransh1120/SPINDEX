"""Static server for handbook development: never cache, so edits take effect.

Threaded on purpose. A single-threaded server serialises the browser's
parallel connections and stalls on the first keep-alive socket, which looks
exactly like a broken page.
"""
import base64, functools, pathlib, sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class H(SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()


    def do_POST(self):
        """Dev-only capture sink: the page POSTs a rendered frame, we save it."""
        if self.path != "/__capture":
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(n)
        name = self.headers.get("X-Capture-Name", "capture.jpg")
        name = "".join(c for c in name if c.isalnum() or c in "._-")
        root = pathlib.Path(__file__).resolve().parent
        out = (root.parent / "handbook" / "assets" / "img" / name
               if name != "capture.jpg" else root / "capture.jpg")
        out.write_bytes(base64.b64decode(data))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, fmt, *a):
        sys.stderr.write("%s\n" % (fmt % a))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8778
    handler = functools.partial(H, directory="handbook")
    ThreadingHTTPServer.allow_reuse_address = True
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as s:
        print("serving handbook on %d" % port)
        s.serve_forever()
