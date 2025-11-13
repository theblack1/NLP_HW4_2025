# stats_both.py
from pathlib import Path
from statistics import mean
from transformers import T5TokenizerFast

# --------------------
# Config
# --------------------
DATA_DIR = Path("data")
MODEL_NAME = "google-t5/t5-small"
MAX_SRC_LEN = 256
MAX_TGT_LEN = 256
ADD_PREFIX = True
PREFIX = "translate to SQL: "

# --------------------
# IO helpers
# --------------------
def load_lines(p: Path):
    # 不丢行，避免 NL/SQL 错位；仅去掉换行符
    with open(p, "r", encoding="utf-8", newline="") as f:
        return [ln.rstrip("\r\n") for ln in f.readlines()]

def gather(split):
    if split == "train":
        nl = load_lines(DATA_DIR / "train.nl")
        sql = load_lines(DATA_DIR / "train.sql")
    elif split == "dev":
        nl = load_lines(DATA_DIR / "dev.nl")
        sql = load_lines(DATA_DIR / "dev.sql")
    else:
        raise ValueError(split)
    assert len(nl) == len(sql), f"Line mismatch in {split}: {len(nl)} vs {len(sql)}"
    return nl, sql

# --------------------
# BEFORE (plain text) stats
# --------------------
def word_tokens_plain(s: str):
    # “未预处理”：按空格切分，保留大小写与标点
    return s.split()

def before_stats(split):
    nl, sql = gather(split)
    n_examples = len(nl)
    nl_lens = [len(word_tokens_plain(x)) for x in nl]
    sql_lens = [len(word_tokens_plain(y)) for y in sql]
    vocab_nl  = set(t for x in nl  for t in word_tokens_plain(x))
    vocab_sql = set(t for y in sql for t in word_tokens_plain(y))
    return {
        "n_examples": n_examples,
        "mean_sentence_len": mean(nl_lens) if nl_lens else 0.0,
        "mean_sql_len": mean(sql_lens) if sql_lens else 0.0,
        "vocab_nl": len(vocab_nl),
        "vocab_sql": len(vocab_sql),
    }

# --------------------
# AFTER (tokenized) stats
# --------------------
def after_stats(tokenizer: T5TokenizerFast, split):
    nl, sql = gather(split)
    src_texts = [f"{PREFIX}{x}" if ADD_PREFIX else x for x in nl]
    enc = tokenizer(src_texts, add_special_tokens=True, truncation=True,
                    padding=False, max_length=MAX_SRC_LEN)
    dec = tokenizer(sql,       add_special_tokens=True, truncation=True,
                    padding=False, max_length=MAX_TGT_LEN)
    enc_lens = [len(x) for x in enc["input_ids"]]
    dec_lens = [len(y) for y in dec["input_ids"]]
    vocab_enc = len(set(t for seq in enc["input_ids"] for t in seq))
    vocab_dec = len(set(t for seq in dec["input_ids"] for t in seq))
    return {
        "mean_enc_len": mean(enc_lens) if enc_lens else 0.0,
        "mean_dec_len": mean(dec_lens) if dec_lens else 0.0,
        "vocab_enc": vocab_enc,
        "vocab_dec": vocab_dec,
    }

# --------------------
# Pretty-print as LaTeX
# --------------------
def print_table_before(tr, dv):
    print(r"\begin{table}[h!]")
    print(r"\centering")
    print(r"\begin{tabular}{lcc}")
    print(r"\toprule")
    print(r"Statistics Name & Train & Dev \\")
    print(r"\midrule")
    print(f"Number of examples & {tr['n_examples']} & {dv['n_examples']} \\\\")
    print(f"Mean sentence length & {tr['mean_sentence_len']:.2f} & {dv['mean_sentence_len']:.2f} \\\\")
    print(f"Mean SQL query length & {tr['mean_sql_len']:.2f} & {dv['mean_sql_len']:.2f} \\\\")
    print(f"Vocabulary size (natural language) & {tr['vocab_nl']} & {dv['vocab_nl']} \\\\")
    print(f"Vocabulary size (SQL) & {tr['vocab_sql']} & {dv['vocab_sql']} \\\\")
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print(r"\caption{Data statistics before any pre-processing.}")
    print(r"\label{tab:data_stats_before}")
    print(r"\end{table}")
    print()

def print_table_after(model_name, tr, dv):
    # 这里沿用表1的结构（去掉“Number of examples”），也可根据你的模板改列名
    print(r"\begin{table}[h!]")
    print(r"\centering")
    print(r"\begin{tabular}{lcc}")
    print(r"\toprule")
    print(r"Statistics Name & Train & Dev \\")
    print(r"\midrule")
    print(rf"\multicolumn{{3}}{{l}}{{\textbf{{{model_name} (tokenized)}}}} \\")
    print(f"Mean tokenized sentence length & {tr['mean_enc_len']:.2f} & {dv['mean_enc_len']:.2f} \\\\")
    print(f"Mean tokenized SQL length & {tr['mean_dec_len']:.2f} & {dv['mean_dec_len']:.2f} \\\\")
    print(f"Observed tokenizer vocab (NL side) & {tr['vocab_enc']} & {dv['vocab_enc']} \\\\")
    print(f"Observed tokenizer vocab (SQL side) & {tr['vocab_dec']} & {dv['vocab_dec']} \\\\")
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print(r"\caption{Data statistics after pre-processing (tokenized by T5).}")
    print(r"\label{tab:data_stats_after}")
    print(r"\end{table}")

# --------------------
# Main
# --------------------
if __name__ == "__main__":
    # BEFORE
    tr_b = before_stats("train")
    dv_b = before_stats("dev")
    # AFTER
    tok = T5TokenizerFast.from_pretrained(MODEL_NAME)
    tr_a = after_stats(tok, "train")
    dv_a = after_stats(tok, "dev")

    # 输出 LaTeX
    print_table_before(tr_b, dv_b)
    print_table_after("T5 fine-tuned model", tr_a, dv_a)
