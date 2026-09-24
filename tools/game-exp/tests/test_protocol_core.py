from __future__ import annotations

import base64
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parents[1]))

from protocol_core import (  # noqa: E402
    ProtocolError,
    build_operation_payload,
    canonical_json_bytes,
    decode_payload_b64,
    digest_object,
    encode_payload_b64,
    strict_json_loads,
)


class ProtocolCoreTests(unittest.TestCase):
    def test_canonical_vector_matches_phase1(self):
        vector = {
            "z": "末",
            "a": ["Alpha", True, None, 42],
            "unicode": "é/游戏/Ω",
            "nested": {"β": "two", "A": "one"},
        }
        self.assertEqual(
            digest_object(vector),
            "sha256:d3cd00d2c795f2d6569b07cb38f311841b0e9d5d4f534f02059784d30d936f25",
        )

    def test_crlf_and_lf_parse_to_same_semantics(self):
        a = strict_json_loads('{"a":"line\\nend","b":1}')
        b = strict_json_loads('{"b":1,"a":"line\\nend"}')
        self.assertEqual(canonical_json_bytes(a), canonical_json_bytes(b))

    def test_rejects_float_duplicate_and_large_int(self):
        with self.assertRaises(ProtocolError):
            strict_json_loads('{"x":1.5}')
        with self.assertRaises(ProtocolError):
            strict_json_loads('{"x":1,"x":2}')
        with self.assertRaises(ProtocolError):
            strict_json_loads('{"x":9007199254740992}')

    def test_payload_base64_round_trip(self):
        value = {"a": "游戏", "n": 7}
        encoded = encode_payload_b64(value)
        self.assertEqual(decode_payload_b64(encoded), value)
        self.assertEqual(
            base64.b64decode(encoded),
            canonical_json_bytes(value),
        )

    def test_operation_envelope(self):
        payload = build_operation_payload(
            "experiment.create",
            {"hypothesis": "three roles"},
            preconditions={"base_sha": "a" * 40},
            actor_claim="alice",
        )
        self.assertEqual(payload["kind"], "operation_request")
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["operation"], "experiment.create")
        self.assertEqual(payload["actor_claim"], "alice")

        with self.assertRaises(ProtocolError):
            build_operation_payload("Experiment Create", {})


if __name__ == "__main__":
    unittest.main()
