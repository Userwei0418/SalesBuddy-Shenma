import csv
import io
import json
from datetime import date, datetime
from fastapi import HTTPException, Response


def csv_response(rows, columns, filename):
    if len(rows) > 10000:
        raise HTTPException(422, "导出超过一万条，请缩小筛选范围")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in columns])
    for row in rows:
        cells = []
        for key, _ in columns:
            value = row.get(key)
            if value is None:
                cells.append("")
                continue
            text = (
                value.isoformat()
                if isinstance(value, (date, datetime))
                else json.dumps(value, ensure_ascii=False, default=str)
                if isinstance(value, (dict, list))
                else str(value)
            )
            if isinstance(value, str) and text.lstrip().startswith(("=", "+", "-", "@")):
                text = "'" + text
            cells.append(text)
        writer.writerow(cells)
    return Response(
        buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}.csv"',
            "Cache-Control": "no-store",
            "X-Export-Count": str(len(rows)),
        },
    )
