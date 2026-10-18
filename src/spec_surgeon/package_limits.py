"""Configurable resource bounds for DOCX archive intake (never filename normalization)."""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass, fields


class ArchiveLimitError(ValueError):
    code = "archive-limit"


@dataclass(frozen=True)
class ArchiveLimits:
    """Positive ceilings; explicit instances override the environment for library callers."""

    max_members: int = 10000
    max_member_bytes: int = 128 * 1024 * 1024
    max_total_bytes: int = 512 * 1024 * 1024
    max_archive_bytes: int = 256 * 1024 * 1024

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"archive limit {field.name} must be a positive integer")

    @classmethod
    def from_environment(cls) -> "ArchiveLimits":
        values = {}
        for field in fields(cls):
            key = "SPEC_SURGEON_ARCHIVE_" + field.name.upper()
            if key in os.environ:
                try:
                    values[field.name] = int(os.environ[key])
                except ValueError as exc:
                    raise ValueError(f"{key} must be a positive integer") from exc
        return cls(**values)


def check_container(data: bytes, limits: ArchiveLimits) -> None:
    """Bound ZIP metadata before ZipFile allocates a ZipInfo for every member.

    Count central-directory records without decoding names or constructing objects.
    ZipFile remains responsible for full ZIP validation. Read ZIP64 counts/sizes
    from its fixed end record when present; deriving the directory from its end
    also supports archives with a prepended self-extracting stub.
    """
    if len(data) > limits.max_archive_bytes:
        raise ArchiveLimitError(f"DOCX archive is {len(data)} bytes; limit is {limits.max_archive_bytes}")
    end = data.rfind(b"PK\x05\x06", max(0, len(data) - 65557))
    if end < 0 or end + 22 > len(data):
        return  # ZipFile supplies the malformed-archive diagnosis.
    record = struct.unpack_from("<4s4H2LH", data, end)
    declared, directory_size = record[4], record[5]
    directory_end = end
    if end >= 76 and data[end - 20:end - 16] == b"PK\x06\x07" and data[end - 76:end - 72] == b"PK\x06\x06":
        extended = struct.unpack_from("<4sQ2H2L4Q", data, end - 76)
        declared, directory_size, directory_end = extended[7], extended[8], end - 76
    if declared > limits.max_members:
        raise ArchiveLimitError(f"DOCX declares {declared} members; limit is {limits.max_members}")
    position = directory_end - directory_size
    count = 0
    if position < 0:
        return
    while position + 46 <= directory_end and data[position:position + 4] == b"PK\x01\x02":
        count += 1
        if count > limits.max_members:
            raise ArchiveLimitError(f"DOCX central directory exceeds {limits.max_members} members")
        name, extra, comment = struct.unpack_from("<3H", data, position + 28)
        position += 46 + name + extra + comment


def check_archive(archive, limits: ArchiveLimits) -> None:
    """Check declarations before inflation, then count actual bytes in bounded reads.

    Every occurrence is checked, including duplicate names and inert members. No file is
    extracted and no name is rewritten or removed. CRC/header errors remain ZIP errors.
    This is an intake resource check, not the independent document verifier.
    """
    infos = archive.infolist()
    if len(infos) > limits.max_members:
        raise ArchiveLimitError(f"DOCX contains {len(infos)} members; limit is {limits.max_members}")
    declared_total = 0
    for info in infos:
        if info.file_size > limits.max_member_bytes:
            raise ArchiveLimitError(f"DOCX member {info.filename!r} declares {info.file_size} bytes; "
                                    f"limit is {limits.max_member_bytes}")
        declared_total += info.file_size
        if declared_total > limits.max_total_bytes:
            raise ArchiveLimitError(f"DOCX declares {declared_total} expanded bytes; "
                                    f"limit is {limits.max_total_bytes}")
    total = 0
    for info in infos:
        used = 0
        with archive.open(info) as stream:
            while True:
                # The extra byte proves an exceeded limit without reading an unbounded member.
                allowance = min(limits.max_member_bytes - used, limits.max_total_bytes - total)
                chunk = stream.read(min(1024 * 1024, allowance + 1))
                if not chunk:
                    break
                used += len(chunk)
                total += len(chunk)
                if used > limits.max_member_bytes or total > limits.max_total_bytes:
                    raise ArchiveLimitError(f"DOCX expansion exceeds its configured limit at {info.filename!r}")
        if used != info.file_size:
            raise ArchiveLimitError(f"DOCX member {info.filename!r} expanded to {used} bytes, "
                                    f"not its declared {info.file_size}")
