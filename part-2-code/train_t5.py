import os
os.environ["TOKENIZERS_PARALLELISM"] = "false"
import argparse
from tqdm import tqdm
import time

import torch
import torch.nn as nn
from torch.amp import autocast, GradScaler
import numpy as np
import wandb

from t5_utils import initialize_model, initialize_optimizer_and_scheduler, save_model, load_model_from_checkpoint, setup_wandb
from load_data import load_t5_data
from utils import compute_metrics, save_queries_and_records

from transformers import GenerationConfig, T5TokenizerFast
from load_data import BOS_ID


MAX_TGT_LEN = 512
TOKENIZER = T5TokenizerFast.from_pretrained("google-t5/t5-small")
DEVICE = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
# PAD_IDX = 0
PAD_IDX = TOKENIZER.pad_token_id
PAD_VAL = -100  # for ignoring in loss

USE_AMP = (DEVICE.type == "cuda")

def get_args():
    '''
    Arguments for training. You may choose to change or extend these as you see fit.
    '''
    parser = argparse.ArgumentParser(description='T5 training loop')

    # Model hyperparameters
    parser.add_argument('--finetune', action='store_true', help="Whether to finetune T5 or not")
    
    # Training hyperparameters
    parser.add_argument('--optimizer_type', type=str, default="AdamW", choices=["AdamW"],
                        help="What optimizer to use")
    parser.add_argument('--learning_rate', type=float, default=3e-4)
    parser.add_argument('--weight_decay', type=float, default=0)

    parser.add_argument('--scheduler_type', type=str, default="cosine", choices=["none", "cosine", "linear"],
                        help="Whether to use a LR scheduler and what type to use if so")
    parser.add_argument('--num_warmup_epochs', type=int, default=0,
                        help="How many epochs to warm up the learning rate for if using a scheduler")
    parser.add_argument('--max_n_epochs', type=int, default=100,
                        help="How many epochs to train the model for")
    parser.add_argument('--patience_epochs', type=int, default=5,
                        help="If validation performance stops improving, how many epochs should we wait before stopping?")

    parser.add_argument('--use_wandb', action='store_true',
                        help="If set, we will use wandb to keep track of experiments")
    parser.add_argument('--experiment_name', type=str, default='experiment',
                        help="How should we name this experiment?")

    # Data hyperparameters
    parser.add_argument('--batch_size', type=int, default=16)
    parser.add_argument('--test_batch_size', type=int, default=32)
    parser.add_argument(
        '--init_from_checkpoint',
        type=str,
        default='',
        help="If provided, initialize model weights from this .pt checkpoint before training."
    )
    # How often to run dev evaluation (in epochs)
    parser.add_argument(
        '--eval_every',
        type=int,
        default=4,
        help="Run evaluation on the dev set every N epochs (default: 1, i.e., every epoch)."
    )

    args = parser.parse_args()
    return args

def train(args, model, train_loader, dev_loader, optimizer, scheduler):
    best_f1 = -1
    epochs_since_improvement = 0

    model_type = 'ft' if args.finetune else 'scr'
    checkpoint_dir = os.path.join('checkpoints', f'{model_type}_experiments', args.experiment_name)
    os.makedirs(checkpoint_dir, exist_ok=True)
    args.checkpoint_dir = checkpoint_dir
    
    # Metrics file path for this run
    metrics_path = os.path.join(checkpoint_dir, f"metrics_{args.run_id}.txt")
    # Initialize metrics file
    with open(metrics_path, "w") as f:
        f.write("epoch,train_loss,dev_loss,record_f1,record_em,sql_em,error_rate,best_f1\n")
    
    experiment_name = 'experiment'
    gt_sql_path = os.path.join(f'data/dev.sql')
    # gt_record_path = os.path.join(f'records/dev_gt_records.pkl') 
    gt_record_path = os.path.join('records', 'ground_truth_dev.pkl') # Corrected path   
    model_sql_path = os.path.join(f'results/t5_{model_type}_{experiment_name}_dev.sql')
    model_record_path = os.path.join(f'records/t5_{model_type}_{experiment_name}_dev.pkl')
    
    for epoch in range(args.max_n_epochs):
        tr_loss = train_epoch(args, model, train_loader, optimizer, scheduler)
        print(f"Epoch {epoch}: Average train loss was {tr_loss}")
        
        do_eval = ((epoch + 1) % args.eval_every == 0) or (epoch == args.max_n_epochs - 1)

        if not do_eval:
            # if not do_eval: only record train loss, save last_model, do not update best_f1 / early stopping
            print(f"Epoch {epoch}: skipping dev evaluation (eval_every={args.eval_every}/{(epoch + 1) % args.eval_every})")

            if args.use_wandb:
                wandb.log({'train/loss': tr_loss}, step=epoch)

            # write nan to other metrics
            with open(metrics_path, "a") as f:
                f.write(
                    f"{epoch},"
                    f"{tr_loss:.6f},"
                    f"nan,nan,nan,nan,nan,"
                    f"{best_f1:.6f}\n"
                )

            # Refresh last model
            save_model(checkpoint_dir, model, best=False)
            # Skip the rest of the loop
            continue
        
        eval_loss, record_f1, record_em, sql_em, error_rate = eval_epoch(args, model, dev_loader,
                                                                         gt_sql_path, model_sql_path,
                                                                         gt_record_path, model_record_path)
        print(f"Epoch {epoch}: Dev loss: {eval_loss}, Record F1: {record_f1}, Record EM: {record_em}, SQL EM: {sql_em}")
        print(f"Epoch {epoch}: {error_rate*100:.2f}% of the generated outputs led to SQL errors")

        if args.use_wandb:
            result_dict = {
                'train/loss' : tr_loss,
                'dev/loss' : eval_loss,
                'dev/record_f1' : record_f1,
                'dev/record_em' : record_em,
                'dev/sql_em' : sql_em,
                'dev/error_rate' : error_rate,
            }
            wandb.log(result_dict, step=epoch)

        if record_f1 > best_f1:
            best_f1 = record_f1
            epochs_since_improvement = 0
        else:
            epochs_since_improvement += 1

        # Log metrics to file
        with open(metrics_path, "a") as f:
            f.write(
                f"{epoch},"
                f"{tr_loss:.6f},"
                f"{eval_loss:.6f},"
                f"{record_f1:.6f},"
                f"{record_em:.6f},"
                f"{sql_em:.6f},"
                f"{error_rate:.6f},"
                f"{best_f1:.6f}\n"
            )
        
        # Write last model every epoch
        save_model(checkpoint_dir, model, best=False)
        if epochs_since_improvement == 0:
            # Refresh best model
            save_model(checkpoint_dir, model, best=True)
            
            # Refresh best model with timestamped copy
            timestamped_best_path = os.path.join(
                checkpoint_dir,
                f"best_model_{args.run_id}.pt"
            )
            torch.save(model.state_dict(), timestamped_best_path)
            print(f"[train_t5] Saved timestamped best checkpoint to: {timestamped_best_path}")

        if epochs_since_improvement >= args.patience_epochs:
            print(f"[train_t5] Early stopping triggered after {epochs_since_improvement} eval steps without improvement.")
            break

def train_epoch(args, model, train_loader, optimizer, scheduler):
    model.train()
    total_loss = 0
    total_tokens = 0
    criterion = nn.CrossEntropyLoss(ignore_index=PAD_VAL)
    
    # if using cuda, set up for mixed precision training
    scaler = GradScaler(enabled=USE_AMP)

    for encoder_input, encoder_mask, decoder_input, decoder_targets, _ in tqdm(train_loader, desc="Training"):
        optimizer.zero_grad()
        encoder_input = encoder_input.to(DEVICE)
        encoder_mask = encoder_mask.to(DEVICE)
        decoder_input = decoder_input.to(DEVICE)
        decoder_targets = decoder_targets.to(DEVICE)

        # outputs = model(
        #     input_ids=encoder_input,
        #     attention_mask=encoder_mask,
        #     decoder_input_ids=decoder_input,
        # )
        # logits = outputs["logits"]  # (B, T_dec, V)

        # # non_pad = decoder_targets != PAD_IDX
        # # loss = criterion(logits[non_pad], decoder_targets[non_pad])
        # vocab_size = logits.size(-1)
        # loss = criterion(
        #     logits.view(-1, vocab_size),          # (B*T_dec, V)
        #     decoder_targets.view(-1),             # (B*T_dec,)
        # )
        
        with autocast(device_type='cuda', enabled=USE_AMP):
            outputs = model(
                input_ids=encoder_input,
                attention_mask=encoder_mask,
                decoder_input_ids=decoder_input,
            )
            logits = outputs["logits"]  # (B, T_dec, V)
            vocab_size = logits.size(-1)
            loss = criterion(
                logits.view(-1, vocab_size),          # (B*T_dec, V)
                decoder_targets.view(-1),             # (B*T_dec,)
            )
        
        if USE_AMP:
            scaler.scale(loss).backward()
            scaler.step(optimizer)
            scaler.update()
        else:
            loss.backward()
            optimizer.step()
            
        if scheduler is not None: 
            scheduler.step()

        with torch.no_grad():
            non_pad = decoder_targets != PAD_VAL
            num_tokens = torch.sum(non_pad).item()
            total_loss += loss.item() * num_tokens
            total_tokens += num_tokens

    return total_loss / total_tokens
        
def eval_epoch(args, model, dev_loader, gt_sql_pth, model_sql_path, gt_record_path, model_record_path):
    '''
    You must implement the evaluation loop to be using during training. We recommend keeping track
    of the model loss on the SQL queries, the metrics compute_metrics returns (save_queries_and_records should be helpful)
    and the model's syntax error rate. 

    To compute non-loss metrics, you will need to perform generation with the model. Greedy decoding or beam search
    should both provide good results. If you find that this component of evaluation takes too long with your compute,
    we found the cross-entropy loss (in the evaluation set) to be well (albeit imperfectly) correlated with F1 performance.
    '''
    # TODO
    model.eval()
    # return 0, 0, 0, 0, 0
    '''
    Evaluation loop on the dev set.

    Returns:
        avg_loss, record_f1, record_em, sql_em, error_rate
    '''
    model.eval()
    criterion = nn.CrossEntropyLoss(ignore_index=PAD_VAL)

    total_loss = 0.0
    total_tokens = 0

    all_pred_sql = []

    gen_config = GenerationConfig(
        max_new_tokens=MAX_TGT_LEN,
        num_beams=4,
        do_sample=False,
        early_stopping=True,
    )
    
    with torch.no_grad():
        for encoder_input, encoder_mask, decoder_input, decoder_targets, initial_decoder_inputs in tqdm(dev_loader, desc="Evaluating"):
            encoder_input = encoder_input.to(DEVICE)
            encoder_mask = encoder_mask.to(DEVICE)
            decoder_input = decoder_input.to(DEVICE)
            decoder_targets = decoder_targets.to(DEVICE)

            # 1) Forward pass to compute loss
            
            with autocast(device_type='cuda', enabled=USE_AMP):
                outputs = model(
                    input_ids=encoder_input,
                    attention_mask=encoder_mask,
                    decoder_input_ids=decoder_input,
                )
                logits = outputs["logits"]  # (B, T_dec, V)
                vocab_size = logits.size(-1)
                loss = criterion(
                    logits.view(-1, vocab_size),
                    decoder_targets.view(-1),
                )

                non_pad = decoder_targets != PAD_VAL
                num_tokens = non_pad.sum().item()
                total_loss += loss.item() * max(num_tokens, 1)
                total_tokens += num_tokens

                # 2) Generate SQL queries
                gen_outputs = model.generate(
                    input_ids=encoder_input,
                    attention_mask=encoder_mask,
                    generation_config=gen_config,
                )  # (B, T_gen)

            # 3) Decode generated sequences into strings and append to list
            for seq in gen_outputs:
                text = TOKENIZER.decode(seq, skip_special_tokens=True)
                all_pred_sql.append(text.strip())

    avg_loss = total_loss / max(total_tokens, 1)

    # 4) Save the predicted SQL queries and corresponding records
    save_queries_and_records(all_pred_sql, model_sql_path, model_record_path)

    # 5) Compute metrics
    sql_em, record_em, record_f1, model_error_msgs = compute_metrics(
        gt_path=gt_sql_pth,
        model_path=model_sql_path,
        gt_query_records=gt_record_path,
        model_query_records=model_record_path
    )

    if len(model_error_msgs) > 0:
        num_errors = sum(1 for msg in model_error_msgs if msg is not None and msg != "")
        error_rate = num_errors / len(model_error_msgs)
    else:
        error_rate = 0.0

    return avg_loss, record_f1, record_em, sql_em, error_rate
        
def test_inference(args, model, test_loader, model_sql_path, model_record_path):
    '''
    You must implement inference to compute your model's generated SQL queries and its associated 
    database records. Implementation should be very similar to eval_epoch.
    '''
    # pass
    '''
    Inference on the test set.

    Generate SQL queries for all test NL inputs and save:
      - the SQL queries to `model_sql_path`
      - the corresponding DB records to `model_record_path`
    '''
    model.eval()
    all_pred_sql = []

    # Generation config: keep it the same/similar to eval_epoch
    gen_config = GenerationConfig(
        max_new_tokens=MAX_TGT_LEN,
        num_beams=4,
        do_sample=False,
        early_stopping=True,
    )

    with torch.no_grad():
        for encoder_input, encoder_mask, initial_decoder_inputs in tqdm(test_loader, desc="Testing Inference"):
            encoder_input = encoder_input.to(DEVICE)
            encoder_mask = encoder_mask.to(DEVICE)
            # initial_decoder_inputs currently we do not explicitly use it, T5 will use its own decoder_start_token_id

            gen_outputs = model.generate(
                input_ids=encoder_input,
                attention_mask=encoder_mask,
                generation_config=gen_config,
            )  # (B, T_gen)

            for seq in gen_outputs:
                text = TOKENIZER.decode(seq, skip_special_tokens=True)
                all_pred_sql.append(text.strip())

    # Save the generated SQL queries and corresponding records
    save_queries_and_records(all_pred_sql, model_sql_path, model_record_path)
    

def main():
    # Get key arguments
    args = get_args()
    
    args.run_id = time.strftime("%Y%m%d-%H%M%S")
    print(f"[train_t5] This run_id = {args.run_id}")
    
    if args.use_wandb:
        # Recommended: Using wandb (or tensorboard) for result logging can make experimentation easier
        setup_wandb(args)

    # Load the data and the model
    train_loader, dev_loader, test_loader = load_t5_data(args.batch_size, args.test_batch_size)
    model = initialize_model(args)
    model.config.decoder_start_token_id = BOS_ID
    
    if getattr(args, "init_from_checkpoint", ""):
        ckpt_path = args.init_from_checkpoint
        if not os.path.isfile(ckpt_path):
            raise FileNotFoundError(f"[train_t5] init_from_checkpoint not found: {ckpt_path}")
        state_dict = torch.load(ckpt_path, map_location=DEVICE, weights_only=True)
        model.load_state_dict(state_dict)
        print(f"[train_t5] Initialized model weights from checkpoint: {ckpt_path}")
    
    optimizer, scheduler = initialize_optimizer_and_scheduler(args, model, len(train_loader))

    # Train 
    train(args, model, train_loader, dev_loader, optimizer, scheduler)

    # Evaluate
    model = load_model_from_checkpoint(args, best=True)
    model.config.decoder_start_token_id = BOS_ID
    model.eval()
    
    # setup paths
    experiment_name = 'experiment'
    model_type = 'ft' if args.finetune else 'scr'
    
    # # Dev set
    # gt_sql_path = os.path.join(f'data/dev.sql')
    # gt_record_path = os.path.join(f'records/ground_truth_dev.pkl')
    # model_sql_path = os.path.join(f'results/t5_{model_type}_{experiment_name}_dev.sql')
    # model_record_path = os.path.join(f'records/t5_{model_type}_{experiment_name}_dev.pkl')
    # dev_loss, dev_record_f1, dev_record_em, dev_sql_em, dev_error_rate = eval_epoch(args, model, dev_loader,
    #                                                                                 gt_sql_path, model_sql_path,
    #                                                                                 gt_record_path, model_record_path)
    # print(f"Dev set results: Loss: {dev_loss}, Record F1: {dev_record_f1}, Record EM: {dev_record_em}, SQL EM: {dev_sql_em}")
    # print(f"Dev set results: {dev_error_rate*100:.2f}% of the generated outputs led to SQL errors")

    # Test set
    model_sql_path = os.path.join(f'results/t5_{model_type}_{experiment_name}_test.sql')
    model_record_path = os.path.join(f'records/t5_{model_type}_{experiment_name}_test.pkl')
    test_inference(args, model, test_loader, model_sql_path, model_record_path)

if __name__ == "__main__":
    start_time = time.time()
    print("Starting train_t5.py process...")
    main()
    end_time = time.time()
    elapsed = end_time - start_time
    elapsed_hours = elapsed / 3600
    print(f"train_t5.py Total execution time: {elapsed:.2f} seconds ({elapsed_hours:.2f} hours)")
