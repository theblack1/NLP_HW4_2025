import os
import re

def read_schema(schema_path):
    '''
    Read the .schema file
    '''
    # TODO
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_text = f.read()
    return schema_text

def extract_sql_query(response):
    '''
    Extract the SQL query from the model's response
    '''
    # TODO
    '''
    Extract the SQL query from the model's response.

    Heuristics:
      1) If there is a ```sql ... ``` fenced block, take its content.
      2) Else if there is a ``` ... ``` fenced block, take its content.
      3) Else, find the first line that looks like starting with SELECT.
    '''
    if response is None:
        return ""

    text = response.strip()

    # 1) Try to find ```sql ... ``` block
    sql_block = re.search(r"```sql(.*?```)", text, flags=re.IGNORECASE | re.DOTALL)
    if sql_block:
        # remove trailing ``` and strip
        content = sql_block.group(1)
        content = content.rsplit("```", 1)[0]
        return content.strip()

    # 2) Try generic ``` ... ``` block
    code_block = re.search(r"```(.*?```)", text, flags=re.DOTALL)
    if code_block:
        content = code_block.group(1)
        content = content.rsplit("```", 1)[0]
        return content.strip()

    # 3) Fallback: look for first line starting with SELECT (case-insensitive)
    lines = text.splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped.upper().startswith("SELECT"):
            return stripped

    # If nothing found, just return the whole response (last resort)
    return text

def save_logs(output_path, sql_em, record_em, record_f1, error_msgs):
    '''
    Save the logs of the experiment to files.
    You can change the format as needed.
    '''
    with open(output_path, "w") as f:
        f.write(f"SQL EM: {sql_em}\nRecord EM: {record_em}\nRecord F1: {record_f1}\nModel Error Messages: {error_msgs}\n")