import datasets
from datasets import load_dataset
from transformers import AutoTokenizer
from torch.utils.data import DataLoader
from transformers import AutoModelForSequenceClassification
from torch.optim import AdamW
from transformers import get_scheduler
import torch
from tqdm.auto import tqdm
import evaluate
import random
import argparse
from nltk.corpus import wordnet
from nltk import word_tokenize
from nltk.tokenize.treebank import TreebankWordDetokenizer
from typing import Optional

random.seed(0)

import os
import nltk

def _ensure_nltk_resources():
    """
    Make NLTK look into a project-local nltk_data folder first,
    then fall back to environment variable NLTK_DATA, then defaults.
    Also auto-download missing packages into the first writable path.
    """
    # 1) Project-local nltk_data (e.g., <project_root>/nltk_data)
    project_root = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
    local_nltk = os.path.join(project_root, "nltk_data")

    # Insert highest priority search path(s)
    if os.path.isdir(local_nltk):
        nltk.data.path.insert(0, local_nltk)

    # 2) Respect NLTK_DATA env var as well (if set)
    env_path = os.environ.get("NLTK_DATA")
    if env_path and os.path.isdir(env_path):
        nltk.data.path.insert(0, env_path)

    # Helper to check & download if missing (downloads to first path)
    def _need(pkg, locator):
        try:
            nltk.data.find(locator)
            return False
        except LookupError:
            return True

    # Decide where to download if needed
    # Prefer local_nltk if exists or is creatable; else use env; else default.
    download_dir = None
    for candidate in [local_nltk, env_path]:
        if candidate:
            try:
                os.makedirs(candidate, exist_ok=True)
                download_dir = candidate
                break
            except Exception:
                pass

    # Packages we need for the transform
    requirements = [
        ("punkt",       "tokenizers/punkt"),
        ("wordnet",     "corpora/wordnet"),
        ("stopwords",   "corpora/stopwords"),
    ]

    for pkg, locator in requirements:
        if _need(pkg, locator):
            nltk.download(pkg, download_dir=download_dir)

# Call once on import
_ensure_nltk_resources()

STOPWORDS = set(nltk.corpus.stopwords.words('english'))
DETOK = TreebankWordDetokenizer()

QWERTY_NEIGHBORS = {
    'q': 'w', 'w': 'qes', 'e': 'wrd', 'r': 'etf', 't': 'ryg', 'y': 'tuh',
    'u': 'yij', 'i': 'uoj', 'o': 'ipk', 'p': 'o',
    'a': 'sqwz', 's': 'awedxz', 'd': 'serfcx', 'f': 'drtgv', 'g': 'ftyhb',
    'h': 'gyujn', 'j': 'huikm', 'k': 'jiol', 'l': 'kop',
    'z': 'asx', 'x': 'zsdc', 'c': 'xdfv', 'v': 'cfgb', 'b': 'vghn',
    'n': 'bhjm', 'm': 'njk'
}

def example_transform(example):
    example["text"] = example["text"].lower()
    return example

from functools import lru_cache
@lru_cache(maxsize=100_000)
def _cached_synonyms(lower_word: str):
    synsets = wordnet.synsets(lower_word)
    lemmas = []
    for syn in synsets:
        for l in syn.lemmas():
            cand = l.name().replace("_", " ")
            if cand.isalpha() and cand.lower() != lower_word:
                lemmas.append(cand)
    
    return tuple(sorted(set(lemmas)))

### Rough guidelines --- typos
# For typos, you can try to simulate nearest keys on the QWERTY keyboard for some of the letter (e.g. vowels)
# You can randomly select each word with some fixed probability, and replace random letters in that word with one of the
# nearest keys on the keyboard. You can vary the random probablity or which letters to use to achieve the desired accuracy.


### Rough guidelines --- synonym replacement
# For synonyms, use can rely on wordnet (already imported here). Wordnet (https://www.nltk.org/howto/wordnet.html) includes
# something called synsets (which stands for synonymous words) and for each of them, lemmas() should give you a possible synonym word.
# You can randomly select each word with some fixed probability to replace by a synonym.

# python main.py --eval_transformed --debug_transformation

# python main.py --eval_transformed

def custom_transform(example):
    ################################
    ##### YOUR CODE BEGINGS HERE ###
    """
    Mixed OOD transformation for sentiment analysis (IMDB):
    - Light synonym replacement (WordNet-based)
    - Light keyboard-typo injection (QWERTY-adjacent substitution)

    Design principles:
    1) "Reasonable" at test time: users often type small typos or use synonyms
       (movie/film, great/excellent). Human label should largely remain the same.
    2) Small magnitude: apply with low probabilities so text remains readable,
       grammatical in most cases, and sentiment label is preserved.
    3) Reproducible: uses Python's `random` (seed is fixed at the top-level of the file).

    Implementation details:
    - Tokenize with NLTK word_tokenize (works with Punkt).
    - For each alphabetic token (skip numbers/punct/contractions), with probability p_syn,
      try to replace it by a WordNet synonym (that is different from the original token).
    - Else, with probability p_typo_word, inject a single-character substitution typo,
      using a QWERTY-neighborhood mapping. We only modify alphabetic words and avoid
      changing the first/last char too aggressively to keep readability.
    - Detokenize using TreebankWordDetokenizer to reconstruct the sentence.

    Notes:
    - This is intentionally conservative: it avoids stopwords-like very short tokens,
      avoids OOV-like drastic changes, and preserves capitalization format.
    """

    # ------------------------------ #
    # Config: tune these probabilities/magnitudes if needed
    # ------------------------------ #
    p_syn = 0.5           # probability to attempt synonym replacement for an eligible token
    p_typo_word = 0.2     # probability to attempt a single-character typo for an eligible token
    min_token_len = 3      # do not modify very short tokens (e.g., "an", "I", "ok")
    # ------------------------------ #

    text = example.get("text", "")

    # Short-circuit: empty text or extremely short text — return as-is
    if not isinstance(text, str) or len(text) == 0:
        return example

    # Tokenize to word-level for controlled edits
    tokens = word_tokenize(text)

    # A very small QWERTY adjacency mapping for typo simulation.
    # Coverage is intentionally limited; it's enough to create realistic single-char noise.

    

    # ------------------------------ #
    # Helper: preserve original capitalization style in replacement
    # ------------------------------ #
    def match_case(src: str, tgt: str) -> str:
        """
        If src is title-cased ('Great') -> capitalize tgt
        If src is uppercase ('GREAT') -> uppercase tgt
        Else -> return tgt as lower/mixed as given
        """
        if src.isupper():
            return tgt.upper()
        if src[0].isupper():
            return tgt.capitalize()
        return tgt

    # ------------------------------ #
    # Helper: get a WordNet synonym different from the source token
    # ------------------------------ #
    def get_synonym(token: str) -> Optional[str]:
        lower = token.lower()
        lemmas = _cached_synonyms(lower)
        if not lemmas:
            return None

        # Plan A Radomly pick one of the top-k closest in length
        topk = sorted(lemmas, key=lambda w: abs(len(w) - len(lower)))[:5]
        # # Plan B Radomly pick one of them
        # topk = lemmas
        return random.choice(topk) if topk else None

    # ------------------------------ #
    # Helper: inject a single-character QWERTY-typo into a word
    # ------------------------------ #
    def inject_typo(word: str, min_token_len: int = 3) -> str:
        """
        Make a single-character typo by randomly choosing among:
        - delete:    remove one character
        - insert:    insert a QWERTY-neighbor character
        - substitute:replace one character by a QWERTY-neighbor

        Notes:
        - Prefer modifying interior positions to keep readability.
        - Preserve the original character's casing where applicable.
        - Fall back to a safe no-op if an operation is not feasible.
        """
        if not isinstance(word, str) or len(word) < min_token_len:
            return word

        # Choose an index likely inside the word body for better readability
        interior_positions = list(range(1, len(word) - 1))
        positions = interior_positions if interior_positions else list(range(len(word)))
        pos = random.choice(positions)

        op = random.choice(["delete", "insert", "substitute"])

        def _pick_neighbor_like(ch: str) -> Optional[str]:
            """Pick a QWERTY neighbor for the given character, matching casing."""
            lower = ch.lower()
            neigh = QWERTY_NEIGHBORS.get(lower)
            if not neigh:
                return None
            new_ch = random.choice(neigh)
            return new_ch.upper() if ch.isupper() else new_ch

        # --- DELETE ---
        if op == "delete":
            # Avoid deleting if it would create too-short words
            if len(word) > min_token_len:
                return word[:pos] + word[pos + 1:]

            # Fallback to another op if delete not advisable
            op = random.choice(["insert", "substitute"])

        # --- INSERT ---
        if op == "insert":
            # Base the inserted char on the current char (or a nearby position)
            base_pos = pos
            base_ch = word[base_pos]
            insert_ch = _pick_neighbor_like(base_ch)
            if insert_ch is None:
                # Try a nearby character as base
                if len(word) > 1:
                    alt_pos = max(0, min(len(word) - 1, base_pos + random.choice([-1, 1])))
                    insert_ch = _pick_neighbor_like(word[alt_pos])
            if insert_ch is None:
                # Fallback to substitution if we still cannot pick a neighbor
                op = "substitute"
            else:
                # Insert either before or after the position
                after = random.choice([True, False])
                if after:
                    return word[:pos + 1] + insert_ch + word[pos + 1:]
                else:
                    return word[:pos] + insert_ch + word[pos:]

        # --- SUBSTITUTE (default/fallback) ---
        # Replace the character at pos with a neighbor, preserving case.
        ch = word[pos]
        sub_ch = _pick_neighbor_like(ch)
        if sub_ch is None:
            # No neighbor known for this character -> no-op
            return word
        if sub_ch == ch:
            # Ensure an actual change; if same, try once more
            alt = _pick_neighbor_like(ch)
            if alt is not None:
                sub_ch = alt
            # If still same, just no-op
            if sub_ch == ch:
                return word

        return word[:pos] + sub_ch + word[pos + 1:]

    # ------------------------------ #
    # Main loop over tokens
    # ------------------------------ #
    new_tokens = []
    for tok in tokens:
        # Preserve html-like tokens as-is
        if tok.lower() in {"<", ">", "/>", "<br", "br", "br/"}:
            new_tokens.append(tok)
            continue
        # # Preserve capitalized tokens as-is (e.g., proper nouns, sentence starts)
        # if tok[0].isupper():
        #     new_tokens.append(tok)
        #     continue
        # Skip stopwords (common function words)
        if tok.lower() in STOPWORDS:
            new_tokens.append(tok)
            continue
        
        # We only consider alphabetic tokens for transformation; keep punctuation/numbers as-is.
        if tok.isalpha() and len(tok) >= min_token_len:
            # First try synonym replacement with probability p_syn
            if random.random() < p_syn:
                syn = get_synonym(tok)
                if syn:
                    syn = match_case(tok, syn)
                    tok = syn  # update tok for potential further typo injection

            # Else try a single-character typo with probability p_typo_word
            if random.random() < p_typo_word:
                tok = inject_typo(tok)
                # # the chance to have more than one typo is much lower, but still possible
                # if random.random() < p_typo_word/10:
                #     tok = inject_typo(tok)

            # Update the token
            new_tokens.append(tok)
        else:
            # Non-alphabetic or very short tokens: leave unchanged
            new_tokens.append(tok)

    # Detokenize back to a string
    new_text = DETOK.detokenize(new_tokens)
    
    # Pretty-print fix spacing for punctuations
    new_text = new_text.replace(" ,", ",").replace(" .", ".").replace(" !", "!").replace(" ?", "?")

    # Update the example dict in-place (label is intentionally unchanged)
    example["text"] = new_text

    ##### YOUR CODE ENDS HERE ######
    return example

