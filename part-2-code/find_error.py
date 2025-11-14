import pickle
from utils import load_queries_and_records

# ====== Paths and options ======
gt_sql_path = "data/dev.sql"
gt_record_path = "records/ground_truth_dev.pkl"

choice = "beforefinetune"  # Options: "cur_test", "beforefinetune"

model_sql_path = f"submission/{choice}/t5_ft_experiment_dev.sql"
model_record_path = f"submission/{choice}/t5_ft_experiment_dev.pkl"

# ====== Load gold & model SQL + records + error messages ======
gt_qs, gt_recs, gt_errs = load_queries_and_records(gt_sql_path, gt_record_path)
md_qs, md_recs, md_errs = load_queries_and_records(model_sql_path, model_record_path)

assert len(gt_qs) == len(md_qs) == len(gt_recs) == len(md_recs)
n = len(gt_qs)

sql_error_indices = []
record_mismatch_indices = []
perfect_match_indices = []

# Fine-grained categories for SQL execution errors
sql_error_types = {
    "syntax_error": [],              # e.g., near "900": syntax error / near ")": syntax error
    "invalid_column_or_table": [],   # e.g., no such column: airport_service_2.airport_code
    "ambiguous_column": [],          # e.g., ambiguous column name: airport_2.airport_code
    "other_sql_error": [],           # any other execution error
}

# Fine-grained categories for record mismatches
record_error_types = {
    "empty_pred_nonempty_gold": [],    # prediction has no records, gold has records
    "nonempty_pred_empty_gold": [],    # prediction has records, gold has none
    "both_nonempty_but_different": []  # both have records but they differ
}

# ====== Main loop: classify into coarse and fine-grained error types ======
for i in range(n):
    err_msg = md_errs[i]

    # A. SQL execution errors (non-empty error message)
    if err_msg is not None and err_msg != "":
        sql_error_indices.append(i)

        msg_lower = err_msg.lower()

        # ---- SQL execution error subtypes ----
        if "syntax error" in msg_lower:
            # case: OperationalError: near "900": syntax error
            #       OperationalError: near ")": syntax error
            sql_error_types["syntax_error"].append(i)

        elif "no such column" in msg_lower or "no such table" in msg_lower:
            # case: OperationalError: no such column: airport_service_2.airport_code
            sql_error_types["invalid_column_or_table"].append(i)

        elif "ambiguous column name" in msg_lower:
            # case: OperationalError: ambiguous column name: airport_2.airport_code
            sql_error_types["ambiguous_column"].append(i)

        else:
            sql_error_types["other_sql_error"].append(i)

        continue  # skip record comparison if SQL execution failed

    # B. SQL executed successfully: compare records
    if gt_recs[i] != md_recs[i]:
        record_mismatch_indices.append(i)

        # ---- Record mismatch subtypes ----
        gold_len = len(gt_recs[i])
        pred_len = len(md_recs[i])

        if pred_len == 0 and gold_len > 0:
            # model returned no records but gold has records
            record_error_types["empty_pred_nonempty_gold"].append(i)
        elif pred_len > 0 and gold_len == 0:
            # model returned records but gold has none
            record_error_types["nonempty_pred_empty_gold"].append(i)
        else:
            # both sides have records but they differ
            record_error_types["both_nonempty_but_different"].append(i)
    else:
        perfect_match_indices.append(i)

# ====== Global statistics ======
print(f"Total samples: {n}")
print(f"SQL execution errors: {len(sql_error_indices)}")
print(f"Record mismatches: {len(record_mismatch_indices)}")
print(f"Perfect matches: {len(perfect_match_indices)}")
print()

# ====== SQL execution error subtype statistics ======
print("=== SQL Execution Error Subtypes ===")
print(f"  Syntax errors: {len(sql_error_types['syntax_error'])}/{n}")
print(f"  Invalid column/table: {len(sql_error_types['invalid_column_or_table'])}/{n}")
print(f"  Ambiguous column: {len(sql_error_types['ambiguous_column'])}/{n}")
print(f"  Other SQL errors: {len(sql_error_types['other_sql_error'])}/{n}")
print()

print("  Example indices for syntax_error:",
      sql_error_types["syntax_error"][:10])
print("  Example indices for invalid_column_or_table:",
      sql_error_types["invalid_column_or_table"][:10])
print("  Example indices for ambiguous_column:",
      sql_error_types["ambiguous_column"][:10])
print("  Example indices for other_sql_error:",
      sql_error_types["other_sql_error"][:10])
print()

# ====== Record mismatch subtype statistics ======
print("=== Record Mismatch Subtypes ===")
print(f"  empty_pred_nonempty_gold: {len(record_error_types['empty_pred_nonempty_gold'])}/{n}")
print(f"  nonempty_pred_empty_gold: {len(record_error_types['nonempty_pred_empty_gold'])}/{n}")
print(f"  both_nonempty_but_different: {len(record_error_types['both_nonempty_but_different'])}/{n}")
print()

print("  Example indices for empty_pred_nonempty_gold:",
      record_error_types["empty_pred_nonempty_gold"][:10])
print("  Example indices for nonempty_pred_empty_gold:",
      record_error_types["nonempty_pred_empty_gold"][:10])
print("  Example indices for both_nonempty_but_different:",
      record_error_types["both_nonempty_but_different"][:10])
print()

# ====== Original index lists for reference ======
print("All SQL error indices:", sql_error_indices)
print("Some record mismatch indices:", record_mismatch_indices[:20])
print("Some perfect match indices:", perfect_match_indices[:20])
