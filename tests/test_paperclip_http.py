import io
import json
import unittest

from xmux_multiplexer.paperclip_http import PaperclipHttpClient


class Opener:
    def __init__(self): self.requests = []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return io.BytesIO(json.dumps({"id": "issue-1", "status": "todo"}).encode())


class ClientTests(unittest.TestCase):
    def test_post_and_get_with_injected_credential(self):
        opener = Opener()
        client = PaperclipHttpClient("https://paperclip.example", lambda: "key", opener=opener)
        self.assertEqual(client.post("/api/companies/c/issues", {"title": "task"})["id"], "issue-1")
        self.assertEqual(opener.requests[0][0].get_header("Authorization"), "Bearer key")
        self.assertEqual(json.loads(opener.requests[0][0].data)["title"], "task")
        client.get("/api/issues/issue-1")
        self.assertEqual(opener.requests[1][0].get_method(), "GET")

    def test_invalid_endpoint_and_path_are_rejected(self):
        with self.assertRaises(ValueError):
            PaperclipHttpClient("https://user:pass@example.com", lambda: "key")
        client = PaperclipHttpClient("https://example.com", lambda: "key", opener=Opener())
        with self.assertRaises(ValueError):
            client.get("//other.example/")


if __name__ == "__main__":
    unittest.main()
