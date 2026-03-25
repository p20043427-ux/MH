import re
import json

text = open("column_desc_raw.txt", encoding="utf-8").read()

pattern = re.compile(
    r"([가-힣A-Za-z0-9_\-\(\) ]+)\s+([a-z0-9]+)\s+([CN])\s+([0-9\.]+)\s*(NN)?\s*(.*)"
)

columns = []

for line in text.splitlines():
    line = line.strip()
    if not line:
        continue

    m = pattern.match(line)

    if m:
        column = {
            "column_name": m.group(1).strip(),
            "column_id": m.group(2).strip(),
            "type": m.group(3),
            "length": m.group(4),
            "nullable": m.group(5) if m.group(5) else "",
            "description": m.group(6).strip(),
        }

        columns.append(column)

json_result = json.dumps(columns, ensure_ascii=False, indent=2)

print(json_result)
