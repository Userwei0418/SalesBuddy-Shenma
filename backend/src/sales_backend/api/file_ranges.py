"""Single byte ranges over bytes already authorized and integrity checked.

Range parsing happens after authorization. This never creates a public URL or
retains an authorization decision between requests.
"""

import hashlib
import re

from fastapi import Response

FILE_RANGE_RESPONSES = {
    206: {"description": "Single authorized byte range; Content-Range and strong ETag identify exact bytes."},
    412: {"description": "If-Match no longer identifies the authorized file; discard partial content."},
    416: {"description": "Invalid, multiple or unsatisfiable byte range; no file bytes returned."},
}


def authorized_file_response(content, *, media_type, headers, range_header=None, if_match=None):
    etag = '"' + hashlib.sha256(content).hexdigest() + '"'
    headers = {**headers, "Accept-Ranges": "bytes", "ETag": etag}
    if if_match is not None and if_match.strip() != "*" and etag not in [tag.strip() for tag in if_match.split(",")]:
        return Response(status_code=412, headers=headers)
    if range_header is None:
        return Response(content, media_type=media_type, headers=headers)
    # Multiple ranges are deliberately not supported. Bound parsing even when
    # called outside the HTTP route (Header also bounds the wire input).
    match = re.fullmatch(r"bytes=([0-9]{0,18})-([0-9]{0,18})", range_header.strip())
    size = len(content)
    if match and size and any(match.groups()):
        first, last = match.groups()
        if first:
            start, end = int(first), min(int(last), size - 1) if last else size - 1
        else:
            suffix = int(last)
            start, end = max(0, size - suffix), size - 1
        if 0 <= start <= end < size:
            return Response(
                content[start : end + 1],
                status_code=206,
                media_type=media_type,
                headers={**headers, "Content-Range": f"bytes {start}-{end}/{size}"},
            )
    return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
