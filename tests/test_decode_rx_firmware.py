import unittest

from tools.decode_rx_firmware import additive_checksum, decode_words, validate_v020


class DecoderTests(unittest.TestCase):
    def test_known_vector_words(self):
        encoded = bytes.fromhex("83b87c83 c3b37c83 f7b37c83 1bb87c83 cdb37c83 e5b37c83")
        expected = bytes.fromhex("10090000 50100000 5c100000 88090000 56100000 6e100000")
        self.assertEqual(decode_words(encoded), expected)

    def test_known_image_marker_and_startup(self):
        # Original bytes at file 0x100..0x12F; expected C-Sky movi r0..r7,0.
        encoded = bytes.fromhex("c18129d6 d9ff0dfa a3d42a83 93837c83 "
                                "93d37cf2 93d17cf0 93d77cf6 93d57cf4 "
                                "f6b37e43 b0e77c43 b1e394f9 91431c27")
        expected = bytes.fromhex("52025555 4a4c3139 30355600 00000000 "
                                 "00300031 00320033 00340035 00360037 "
                                 "5b1002c0 216400c0 2260a83a 02c02064")
        self.assertEqual(decode_words(encoded), expected)
        self.assertEqual(decode_words(bytes.fromhex("92837c83")), b"\xff" * 4)

    def test_checksum_little_endian_and_unsigned_wrap(self):
        self.assertEqual(additive_checksum(bytes.fromhex("ffffffff 02000000 00010000")), 257)
        self.assertEqual(additive_checksum(bytes.fromhex("01020304")), 0x04030201)

    def test_alignment_and_unknown_identity_rejected(self):
        for data in (b"", b"abc", b"abcde"):
            with self.assertRaises(ValueError):
                decode_words(data)
            with self.assertRaises(ValueError):
                additive_checksum(data)
        for data in (b"", b"\x00" * 30720):
            with self.assertRaises(ValueError):
                validate_v020(data)


if __name__ == "__main__":
    unittest.main()
