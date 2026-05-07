"""ppdeep — pure-Python ssdeep fuzzy hashing.

Vendored from https://github.com/elceef/ppdeep (upstream version 20260221).

Original copyright:
    Created by Marcin Ulikowski <marcin@ulikowski.pl>
    Based on SpamSum by Dr. Andrew Tridgell
    Licensed under the Apache License, Version 2.0
    http://www.apache.org/licenses/LICENSE-2.0

Curator-local modifications relative to upstream:

  1. **Module → package**: original was a single ``ppdeep.py`` file; we
     vendor it under ``curator._vendored.ppdeep/__init__.py`` for namespace
     hygiene.
  2. **``hash_buf`` alias added**: the upstream public function ``hash``
     shadows Python's builtin. We expose the same function as ``hash_buf``
     so callers can use a non-shadowing name. ``hash`` is still exported
     for compatibility with the upstream API.
  3. **CLI ``__main__`` block removed**: irrelevant inside a vendored
     package; users invoke via ``curator.cli`` instead.

Otherwise the algorithm is unchanged.

Public API:
    hash(buf)             — fuzzy hash of bytes-or-str. Returns "<bs>:<s1>:<s2>".
    hash_buf(buf)         — alias for hash() that doesn't shadow the builtin.
    hash_from_file(path)  — fuzzy hash of a file's contents.
    compare(h1, h2)       — similarity 0..100 between two hashes.
"""

from __future__ import annotations

import os
from io import BytesIO
from itertools import cycle


__version__ = "20260221"
__author__ = "Marcin Ulikowski"
__license__ = "Apache-2.0"


BLOCKSIZE_MIN = 3
SPAMSUM_LENGTH = 64

f_table = (
    0x00, 0x13, 0x26, 0x39, 0x0c, 0x1f, 0x32, 0x05,  # 0x00-0x07
    0x18, 0x2b, 0x3e, 0x11, 0x24, 0x37, 0x0a, 0x1d,  # 0x08-0x0f
    0x30, 0x03, 0x16, 0x29, 0x3c, 0x0f, 0x22, 0x35,  # 0x10-0x17
    0x08, 0x1b, 0x2e, 0x01, 0x14, 0x27, 0x3a, 0x0d,  # 0x18-0x1f
    0x20, 0x33, 0x06, 0x19, 0x2c, 0x3f, 0x12, 0x25,  # 0x20-0x27
    0x38, 0x0b, 0x1e, 0x31, 0x04, 0x17, 0x2a, 0x3d,  # 0x28-0x2f
    0x10, 0x23, 0x36, 0x09, 0x1c, 0x2f, 0x02, 0x15,  # 0x30-0x37
    0x28, 0x3b, 0x0e, 0x21, 0x34, 0x07, 0x1a, 0x2d,  # 0x38-0x3f
)

# pre-computed partial FNV hash table
sum_table = [
    [f_table[a] ^ b for b in range(0, 64)] for a in range(0, 64)
]

byte_table = [
    [sum_table[h][b & 0x3F] for h in range(0, 64)] for b in range(0, 256)
]


def _spamsum(stream, slen):
    STREAM_BUFF_SIZE = 65536
    HASH_INIT = 0x27
    ROLL_WINDOW = 7
    B64 = tuple(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    )

    bs = BLOCKSIZE_MIN
    while (bs * SPAMSUM_LENGTH) < slen:
        bs = bs * 2
    block_size = bs
    rh = 0

    while True:
        if block_size < BLOCKSIZE_MIN:
            raise RuntimeError("Calculated block size is too small")

        roll_win = [0] * ROLL_WINDOW
        roll_h1 = roll_h2 = roll_h3 = int()
        roll_n = int()
        roll_c = cycle(range(ROLL_WINDOW))

        block_hash1 = block_hash2 = int(HASH_INIT)
        hash_string1 = hash_string2 = str()
        last_char1 = last_char2 = str()

        stream.seek(0)
        buf = stream.read(STREAM_BUFF_SIZE)
        while buf:
            for b in buf:
                block_hash1 = byte_table[b][block_hash1]
                block_hash2 = byte_table[b][block_hash2]

                roll_n = next(roll_c)
                roll_h2 = roll_h2 - roll_h1 + (ROLL_WINDOW * b)
                roll_h1 = roll_h1 + b - roll_win[roll_n]
                roll_win[roll_n] = b
                roll_h3 = (roll_h3 << 5) & 0xFFFFFFFF
                roll_h3 ^= b

                rh = (roll_h1 + roll_h2 + roll_h3) & 0xFFFFFFFF

                if (rh % block_size) == (block_size - 1):
                    last_char1 = B64[block_hash1]
                    if len(hash_string1) < (SPAMSUM_LENGTH - 1):
                        hash_string1 += B64[block_hash1]
                        block_hash1 = HASH_INIT
                        last_char1 = str()
                    if (rh % (block_size * 2)) == ((block_size * 2) - 1):
                        last_char2 = B64[block_hash2]
                        if len(hash_string2) < ((SPAMSUM_LENGTH // 2) - 1):
                            hash_string2 += B64[block_hash2]
                            block_hash2 = HASH_INIT
                            last_char2 = str()

            buf = stream.read(STREAM_BUFF_SIZE)

        if block_size > BLOCKSIZE_MIN and len(hash_string1) < (SPAMSUM_LENGTH // 2):
            block_size = (block_size // 2)
        else:
            if rh != 0:
                hash_string1 += B64[block_hash1]
                hash_string2 += B64[block_hash2]
            else:
                hash_string1 += last_char1
                hash_string2 += last_char2
            break

    return f"{block_size}:{hash_string1}:{hash_string2}"


def hash(buf):  # noqa: A001 — name preserved for upstream API compat
    """Compute a fuzzy hash of ``buf`` (bytes or str). Returns ``"<bs>:<s1>:<s2>"``.

    NOTE: this name shadows the Python builtin ``hash()``. Prefer
    :func:`hash_buf` in new code.
    """
    if isinstance(buf, bytes):
        pass
    elif isinstance(buf, str):
        buf = buf.encode()
    else:
        raise TypeError(
            f"Argument must be of bytes or string type, not {type(buf)!r}"
        )
    return _spamsum(BytesIO(buf), len(buf))


# Curator-preferred name (doesn't shadow ``builtins.hash``).
hash_buf = hash


def hash_from_file(filename):
    """Compute a fuzzy hash of the file at ``filename``."""
    if not isinstance(filename, str):
        raise TypeError(
            f"Argument must be of string type, not {type(filename)!r}"
        )
    if not os.path.isfile(filename):
        raise IOError("File not found")
    if not os.access(filename, os.R_OK):
        raise IOError("File is not readable")
    fsize = os.stat(filename).st_size
    return _spamsum(open(filename, "rb"), fsize)


def _levenshtein(s, t):
    # Implementation by Christopher P. Matthews
    if s == t:
        return 0
    if len(s) == 0:
        return len(t)
    if len(t) == 0:
        return len(s)
    v0 = [None] * (len(t) + 1)
    v1 = [None] * (len(t) + 1)
    for i in range(len(v0)):
        v0[i] = i
    for i in range(len(s)):
        v1[0] = i + 1
        for j in range(len(t)):
            cost = 0 if s[i] == t[j] else 1
            v1[j + 1] = min(v1[j] + 1, v0[j + 1] + 1, v0[j] + cost)
        for j in range(len(v0)):
            v0[j] = v1[j]
    return v1[len(t)]


def _common_substring(s1, s2):
    ROLL_WINDOW = 7
    m = len(s1)
    n = len(s2)
    res = 0

    for i in range(m):
        for j in range(n):
            cur = 0
            while (i + cur) < m and (j + cur) < n and s1[i + cur] == s2[j + cur]:
                cur += 1
            res = max(res, cur)

    return res >= ROLL_WINDOW


def _score_strings(s1, s2, block_size):
    if not _common_substring(s1, s2):
        return 0
    score = _levenshtein(s1, s2)
    score = (score * SPAMSUM_LENGTH) // (len(s1) + len(s2))
    score = (100 * score) // SPAMSUM_LENGTH
    score = 100 - score
    if score > (block_size // BLOCKSIZE_MIN * min([len(s1), len(s2)])):
        score = block_size // BLOCKSIZE_MIN * min([len(s1), len(s2)])
    return score


def _strip_sequences(s):
    r = s[:3]
    for i in range(3, len(s)):
        if s[i] != s[i - 1] or s[i] != s[i - 2] or s[i] != s[i - 3]:
            r += s[i]
    return r


def compare(hash1, hash2):
    """Compare two ppdeep hashes. Returns int 0..100 (higher = more similar)."""
    if not (isinstance(hash1, str) and isinstance(hash2, str)):
        raise TypeError("Arguments must be of string type")
    try:
        hash1_bs, hash1_s1, hash1_s2 = hash1.split(":")
        hash2_bs, hash2_s1, hash2_s2 = hash2.split(":")
        hash1_bs = int(hash1_bs)
        hash2_bs = int(hash2_bs)
    except ValueError:
        raise ValueError("Invalid hash format") from None

    if (
        hash1_bs != hash2_bs
        and hash1_bs != (hash2_bs * 2)
        and hash2_bs != (hash1_bs * 2)
    ):
        return 0

    hash1_s1 = _strip_sequences(hash1_s1)
    hash1_s2 = _strip_sequences(hash1_s2)
    hash2_s1 = _strip_sequences(hash2_s1)
    hash2_s2 = _strip_sequences(hash2_s2)

    if hash1_bs == hash2_bs and hash1_s1 == hash2_s1:
        return 100

    if hash1_bs == hash2_bs:
        score1 = _score_strings(hash1_s1, hash2_s1, hash1_bs)
        score2 = _score_strings(hash1_s2, hash2_s2, hash2_bs * 2)
        return int(max([score1, score2]))
    if hash1_bs == (hash2_bs * 2):
        return int(_score_strings(hash1_s1, hash2_s2, hash1_bs))
    return int(_score_strings(hash1_s2, hash2_s1, hash2_bs))


__all__ = ["hash", "hash_buf", "hash_from_file", "compare"]
