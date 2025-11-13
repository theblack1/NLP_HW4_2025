import os, random, re, string
from collections import Counter
from tqdm import tqdm
import pickle

from torch.utils.data import Dataset, DataLoader
from torch.nn.utils.rnn import pad_sequence

import nltk
# nltk.download('punkt')
from transformers import T5TokenizerFast
import torch


TOKENIZER = T5TokenizerFast.from_pretrained("google-t5/t5-small")
PAD_IDX = TOKENIZER.pad_token_id  # should be 0
PAD_VAL = -100  # for ignoring in loss
BOS_ID = TOKENIZER.convert_tokens_to_ids("<extra_id_0>")

class T5Dataset(Dataset):

    def __init__(self, data_folder, split):
        '''
        Skeleton for the class for performing data processing for the T5 model.

        Some tips for implementation:
            * You should be using the 'google-t5/t5-small' tokenizer checkpoint to tokenize both
              the encoder and decoder output. 
            * You want to provide the decoder some beginning of sentence token. Any extra-id on the
              T5Tokenizer should serve that purpose.
            * Class behavior should be different on the test set.
        '''
        # TODO
        assert split in {"train", "dev", "test"}
        self.data_folder = data_folder
        self.split = split

        # tokenizer
        self.tokenizer = TOKENIZER
        self.pad_id = PAD_IDX
        # We'll use one of T5's extra ids as a BOS token later in collate
        self.bos_id = BOS_ID

        # containers filled by process_data
        self.enc_ids = []   # list[Tensor]
        self.dec_ids = None # list[Tensor] for train/dev; None for test

        # load & tokenize data
        self.process_data(data_folder, split, self.tokenizer)

    def process_data(self, data_folder, split, tokenizer):
        # TODO
        """
        Read .nl/.sql files and tokenize.
        Encoder input format: "translate to SQL: {nl}"
        Decoder target: raw SQL tokens (only for train/dev).
        """
        import os
        def _load(p):
            with open(p, "r", encoding="utf-8") as f:
                return [ln.strip() for ln in f if ln.strip()]

        if split == "train":
            nl = _load(os.path.join(data_folder, "train.nl"))
            sql = _load(os.path.join(data_folder, "train.sql"))
        elif split == "dev":
            nl = _load(os.path.join(data_folder, "dev.nl"))
            sql = _load(os.path.join(data_folder, "dev.sql"))
        else:  # test
            nl = _load(os.path.join(data_folder, "test.nl"))
            sql = None

        if sql is not None:
            assert len(nl) == len(sql), "train/dev: NL and SQL line counts must match"

        # encoder side: add a task prefix (helps T5)
        src_texts = [f"translate to SQL: {x}" for x in nl]
        src_tok = tokenizer(src_texts, add_special_tokens=True, truncation=True,
                            padding=False, max_length=256)
        self.enc_ids = [torch.tensor(x, dtype=torch.long) for x in src_tok["input_ids"]]

        # decoder side only for train/dev
        if sql is not None:
            tgt_tok = tokenizer(sql, add_special_tokens=True, truncation=True,
                                padding=False, max_length=256)
            self.dec_ids = [torch.tensor(x, dtype=torch.long) for x in tgt_tok["input_ids"]]
        else:
            self.dec_ids = None
    
    def __len__(self):
        # TODO
        return len(self.enc_ids)

    def __getitem__(self, idx):
        # TODO
        if self.dec_ids is None:
            return (self.enc_ids[idx],)
        else:
            return self.enc_ids[idx], self.dec_ids[idx]

def _make_pad_mask(ids: torch.Tensor, pad_id: int):
    # ids: (B, T) -> 1 for non-pad, 0 for pad
    return (ids != pad_id).long()

def normal_collate_fn(batch):
    '''
    Collation function to perform dynamic padding for training and evaluation with the
    development or validation set.

    Inputs:
        * batch (List[Any]): batch is a list of length batch_size, where each index contains what
                             the dataset __getitem__ function returns.

    Returns: To be compatible with the provided training loop, you should be returning
        * encoder_ids: The input ids of shape BxT to be fed into the T5 encoder.
        * encoder_mask: Mask of shape BxT associated with padding tokens in the encoder input
        * decoder_inputs: Decoder input ids of shape BxT' to be fed into T5 decoder.
        * decoder_targets: The target tokens with which to train the decoder (the tokens following each decoder input)
        * initial_decoder_inputs: The very first input token to be decoder (only to be used in evaluation)
    '''
    # TODO
    # return [], [], [], [], []
    """
    Collate for train/dev:
      - pad encoder ids
      - build decoder inputs (BOS + target[:-1])
      - set decoder target pad positions to PAD_VAL (-100)
    """
    # batch: list of tuples (enc_ids, tgt_ids)
    enc_list = [b[0] for b in batch]
    tgt_list = [b[1] for b in batch]

    enc_padded = pad_sequence(enc_list, batch_first=True, padding_value=PAD_IDX)
    enc_mask = _make_pad_mask(enc_padded, PAD_IDX)

    tgt_padded = pad_sequence(tgt_list, batch_first=True, padding_value=PAD_IDX)

    # BOS token id（和数据集里保持一致）
    bos_id = BOS_ID
    B, T = tgt_padded.size()
    bos = torch.full((B, 1), fill_value=bos_id, dtype=torch.long)

    # decoder_inputs = BOS + target[:-1]
    dec_inputs = torch.cat([bos, tgt_padded[:, :-1]], dim=1)

    # targets: pad -> PAD_VAL (-100)
    dec_targets = tgt_padded.clone()
    dec_targets[dec_targets == PAD_IDX] = PAD_VAL

    initial_decoder_inputs = bos  # (B, 1)
    return enc_padded, enc_mask, dec_inputs, dec_targets, initial_decoder_inputs

def test_collate_fn(batch):
    '''
    Collation function to perform dynamic padding for inference on the test set.

    Inputs:
        * batch (List[Any]): batch is a list of length batch_size, where each index contains what
                             the dataset __getitem__ function returns.

    Recommended returns: 
        * encoder_ids: The input ids of shape BxT to be fed into the T5 encoder.
        * encoder_mask: Mask of shape BxT associated with padding tokens in the encoder input
        * initial_decoder_inputs: The very first input token to be decoder (only to be used in evaluation)
    '''
    # TODO
    # return [], [], []
    '''
    Collation for test (no targets):
      - dynamic pad encoder ids to (B, T)
      - build encoder_mask where 1=non-pad, 0=pad
      - provide initial BOS token as (B, 1) for generation start
    '''
    # batch: list of tuples like (enc_ids,)
    enc_list = [b[0] for b in batch]

    # Pad encoder side
    enc_padded = pad_sequence(enc_list, batch_first=True, padding_value=PAD_IDX)
    enc_mask = _make_pad_mask(enc_padded, PAD_IDX)

    # BOS for decoder start
    bos_id = BOS_ID
    initial_decoder_inputs = torch.full((enc_padded.size(0), 1), fill_value=bos_id, dtype=torch.long)

    return enc_padded, enc_mask, initial_decoder_inputs

def get_dataloader(batch_size, split):
    data_folder = 'data'
    dset = T5Dataset(data_folder, split)
    shuffle = split == "train"
    collate_fn = normal_collate_fn if split != "test" else test_collate_fn

    dataloader = DataLoader(dset, batch_size=batch_size, shuffle=shuffle, collate_fn=collate_fn)
    return dataloader

def load_t5_data(batch_size, test_batch_size):
    train_loader = get_dataloader(batch_size, "train")
    dev_loader = get_dataloader(test_batch_size, "dev")
    test_loader = get_dataloader(test_batch_size, "test")
    
    return train_loader, dev_loader, test_loader


def load_lines(path):
    with open(path, 'r') as f:
        lines = f.readlines()
        lines = [line.strip() for line in lines]
    return lines

def load_prompting_data(data_folder):
    # TODO
    # return train_x, train_y, dev_x, dev_y, test_x
    """
    Load raw NL/SQL strings for prompting or analysis.
    Returns:
        train_x: list of train NL queries
        train_y: list of train SQL queries
        dev_x:   list of dev NL queries
        dev_y:   list of dev SQL queries
        test_x:  list of test NL queries
    """
    train_x = load_lines(os.path.join(data_folder, "train.nl"))
    train_y = load_lines(os.path.join(data_folder, "train.sql"))
    dev_x   = load_lines(os.path.join(data_folder, "dev.nl"))
    dev_y   = load_lines(os.path.join(data_folder, "dev.sql"))
    test_x  = load_lines(os.path.join(data_folder, "test.nl"))
    return train_x, train_y, dev_x, dev_y, test_x