import datasets
from datasets import load_dataset
from transformers import AutoTokenizer
from torch.utils.data import DataLoader
from transformers import AutoModelForSequenceClassification
from torch.cuda.amp import autocast, GradScaler
from torch.optim import AdamW
from transformers import get_scheduler
import contextlib
import torch
from tqdm.auto import tqdm
import evaluate
import random
import argparse
from utils import *
import os
import time

# Set seed
random.seed(0)
torch.manual_seed(0)
torch.cuda.manual_seed(0)
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False


# Tokenize the input
def tokenize_function(examples):
    return tokenizer(examples["text"], padding="max_length", truncation=True)


# Debug train: 
# python main.py --train --eval --debug_train
# Full train:
# python main.py --train --eval

# Core training function
def do_train(args, model, train_dataloader, save_dir="./out", use_grad_clip = False):
    # # ===== quick overfit check on one batch =====
    # check_iter = iter(train_dataloader)
    # small_batch = next(check_iter)
    # small_batch = {k: v.to(device) for k, v in small_batch.items()}
    # model.train()
    # opt = AdamW(model.parameters(), lr=args.learning_rate)
    # for i in tqdm(range(400), desc="Overfit Check"):
    #     out = model(**small_batch)
    #     loss = out.loss
    #     loss.backward()
    #     torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
    #     opt.step()
    #     opt.zero_grad(set_to_none=True)
    #     if (i+1) % 50 == 0:
    #         with torch.no_grad():
    #             pred = out.logits.argmax(-1)
    #             acc = (pred == small_batch["labels"]).float().mean().item()
    #         print(f"[overfit] step {i+1}: loss={loss.item():.4f}, acc={acc:.3f}")
    # # After overfit check, stop here
    # raise SystemExit
    # # ===== end check =====
    
    # optimizer = AdamW(model.parameters(), lr=args.learning_rate)
    optimizer = AdamW(model.parameters(), lr=args.learning_rate, weight_decay=0.01)
    
    use_amp = torch.cuda.is_available()
    scaler = GradScaler(enabled=use_amp)
    
    num_epochs = args.num_epochs
    # num_training_steps = num_epochs * len(train_dataloader)
    # lr_scheduler = get_scheduler(
    #     name="linear", optimizer=optimizer, num_warmup_steps=0, num_training_steps=num_training_steps
    # )
    num_training_steps = num_epochs * len(train_dataloader)
    num_warmup = int(0.1 * num_training_steps)  # 10% warmup
    lr_scheduler = get_scheduler(
        name="linear",
        optimizer=optimizer,
        num_warmup_steps=num_warmup,
        num_training_steps=num_training_steps
    )
    model.train()
    model.to(device)
    progress_bar = tqdm(range(num_training_steps))

    ################################
    ##### YOUR CODE BEGINGS HERE ###

    # Implement the training loop --- make sure to use the optimizer and lr_sceduler (learning rate scheduler)
    # Remember that pytorch uses gradient accumumlation so you need to use zero_grad (https://pytorch.org/tutorials/recipes/recipes/zeroing_out_gradients.html)
    # You can use progress_bar.update(1) to see the progress during training
    # You can refer to the pytorch tutorial covered in class for reference

    # raise NotImplementedError
        # Standard fine-tuning loop for sequence classification (BERT).
    # Optimizer / scheduler / progress_bar are already defined above.
    
    total_steps = num_training_steps  # for clarity
    max_grad_norm = 1.0               # safe default for clipping
    log_every = 20                   # log every N steps
    
    amp_ctx = autocast(dtype=torch.float16) if use_amp else contextlib.nullcontext()

    step_count = 0
    for epoch in range(num_epochs):
        running_loss = 0.0
        running_correct = 0
        running_seen = 0

        for step, batch in enumerate(train_dataloader):
            # 1) move batch tensors to device
            batch = {k: v.to(device) for k, v in batch.items()}

            # 2) forward pass (HF returns loss and logits when labels are provided)
            # outputs = model(**batch)
            # loss = outputs.loss
            with amp_ctx:
                outputs = model(**batch)
                loss = outputs.loss
            
            # Compute training accuracy
            with torch.no_grad():
                preds = outputs.logits.argmax(dim=-1)
                running_correct += (preds == batch["labels"]).sum().item()
                running_seen += batch["labels"].size(0)

            # 3) backward
            # loss.backward()
            if use_amp:
                scaler.scale(loss).backward()
                # 4) (optional but recommended) clip gradients to avoid exploding gradients
                if use_grad_clip or (step+1) % log_every == 0:
                    scaler.unscale_(optimizer)
                if use_grad_clip:
                    torch.nn.utils.clip_grad_norm_(model.parameters(), max_grad_norm)
                # 5) optimizer & scheduler step
                scaler.step(optimizer)
                scaler.update()
            else:
                loss.backward()
                # 4) (optional but recommended) clip gradients to avoid exploding gradients
                if use_grad_clip:
                    torch.nn.utils.clip_grad_norm_(model.parameters(), max_grad_norm)
                # 5) optimizer & scheduler step
                optimizer.step()
            
            lr_scheduler.step()
            
            if (step + 1) % log_every == 0:
                avg_loss = running_loss / (step + 1)
                train_acc = running_correct / max(1, running_seen)
                with torch.no_grad():
                    # the gradient norm for checking gradient disappearance/explosion
                    total_norm = 0.0
                    for p in model.parameters():
                        if p.grad is not None:
                            n = p.grad.data.norm(2)
                            total_norm += n.item() ** 2
                    total_norm **= 0.5
                    curr_lr = lr_scheduler.get_last_lr()[0]
                progress_bar.set_postfix(loss=f"{avg_loss:.4f}",
                                        acc=f"{train_acc:.4f}",
                                        grad=f"{total_norm:.2e}",
                                        lr=f"{curr_lr:.2e}")

            # 6) zero grads (IMPORTANT in PyTorch)
            optimizer.zero_grad(set_to_none=True)

            # bookkeeping
            running_loss += loss.item()
            progress_bar.update(1)

        # optional: epoch-level logging
        avg_loss = running_loss / max(1, len(train_dataloader))
        epoch_acc = running_correct / max(1, running_seen)
        tqdm.write(f"Epoch [{epoch + 1}/{num_epochs}] - train_loss: {avg_loss:.4f} - train_acc: {epoch_acc:.4f}")


    ##### YOUR CODE ENDS HERE ######

    print("Training completed...")
    print("Saving Model....")
    model.save_pretrained(save_dir)

    return


# Core evaluation function
def do_eval(eval_dataloader, output_dir, out_file):
    model = AutoModelForSequenceClassification.from_pretrained(output_dir)
    model.to(device)
    model.eval()

    metric = evaluate.load("accuracy")
    out_file = open(out_file, "w")
    
    # --- additions for live accuracy ---
    running_correct = 0
    running_seen = 0
    log_every = 10
    # ------------------------------------------------
    
    bar = tqdm(eval_dataloader, desc="Eval")
    for step, batch in enumerate(bar):
        batch = {k: v.to(device) for k, v in batch.items()}
        with torch.no_grad():
            outputs = model(**batch)

        logits = outputs.logits
        predictions = torch.argmax(logits, dim=-1)
        metric.add_batch(predictions=predictions, references=batch["labels"])
        
        # --- additions for live accuracy ---
        running_correct += (predictions == batch["labels"]).sum().item()
        running_seen += batch["labels"].size(0)
        if (step + 1) % log_every == 0:
            curr_acc = running_correct / max(1, running_seen)
            bar.set_postfix(acc=f"{curr_acc:.4f}")
        # ------------------------------------------------

        # write to output file
        for pred, label in zip(predictions, batch["labels"]):
                out_file.write(f"{pred.item()}\n")
                out_file.write(f"{label.item()}\n")
    out_file.close()
    score = metric.compute()
    
    tqdm.write(f"Eval accuracy (final): {score['accuracy']:.4f}")

    return score

# python main.py --train_augmented --eval_transformed
# python main.py --eval --model_dir out_augmented
# python main.py --eval_transformed --model_dir out_augmented

# Created a dataladoer for the augmented training dataset
def create_augmented_dataloader(args, dataset):
    ################################
    ##### YOUR CODE BEGINGS HERE ###

    # Here, 'dataset' is the original dataset. You should return a dataloader called 'train_dataloader' -- this
    # dataloader will be for the original training split augmented with 5k random transformed examples from the training set.
    # You may find it helpful to see how the dataloader was created at other place in this code.

    # raise NotImplementedError
    # 1) Start from the *raw* training split (has "text" + "label")
    train_raw = dataset["train"]

    # 2) Randomly sample 5,000 examples from the training split
    sampled_raw = train_raw.shuffle(seed=42).select(range(5000))

    # 3) Apply your Q2 transformation to the sampled subset
    transformed_raw = sampled_raw.map(custom_transform, load_from_cache_file=False)

    # 4) Concatenate original + transformed to form the augmented training set
    #    (use the high-level `datasets` module that is already imported at the top)
    augmented_raw = datasets.concatenate_datasets([train_raw, transformed_raw])

    # 5) Tokenize the augmented set (same pipeline as elsewhere in this file)
    tokenized_aug = augmented_raw.map(tokenize_function, batched=True, load_from_cache_file=False)
    tokenized_aug = tokenized_aug.remove_columns(["text"])
    tokenized_aug = tokenized_aug.rename_column("label", "labels")
    tokenized_aug.set_format("torch")

    # 6) Wrap into a DataLoader for training
    train_dataloader = DataLoader(tokenized_aug, shuffle=True, batch_size=args.batch_size)

    # (Optional) small log for sanity check
    print(f"Augmented train size: {len(tokenized_aug)} "
          f"(original {len(train_raw)} + transformed {len(transformed_raw)})")
    ##### YOUR CODE ENDS HERE ######

    return train_dataloader


# Create a dataloader for the transformed test set
def create_transformed_dataloader(args, dataset, debug_transformation):
    # Print 5 random transformed examples
    if debug_transformation:
        small_dataset = dataset["test"].shuffle(seed=42).select(range(5))
        small_transformed_dataset = small_dataset.map(custom_transform, load_from_cache_file=False)
        for k in range(5):
            print("Original Example ", str(k))
            print(small_dataset[k])
            print("\n")
            print("Transformed Example ", str(k))
            print(small_transformed_dataset[k])
            print('=' * 30)

        exit()

    transformed_dataset = dataset["test"].map(custom_transform, load_from_cache_file=False)
    transformed_tokenized_dataset = transformed_dataset.map(tokenize_function, batched=True, load_from_cache_file=False)
    transformed_tokenized_dataset = transformed_tokenized_dataset.remove_columns(["text"])
    transformed_tokenized_dataset = transformed_tokenized_dataset.rename_column("label", "labels")
    transformed_tokenized_dataset.set_format("torch")

    transformed_val_dataset = transformed_tokenized_dataset
    eval_dataloader = DataLoader(transformed_val_dataset, batch_size=args.batch_size)

    return eval_dataloader


if __name__ == "__main__":
    start_time = time.time()

    parser = argparse.ArgumentParser()

    # Arguments
    parser.add_argument("--train", action="store_true", help="train a model on the training data")
    parser.add_argument("--train_augmented", action="store_true", help="train a model on the augmented training data")
    parser.add_argument("--eval", action="store_true", help="evaluate model on the test set")
    parser.add_argument("--eval_transformed", action="store_true", help="evaluate model on the transformed test set")
    parser.add_argument("--model_dir", type=str, default="./out")
    parser.add_argument("--debug_train", action="store_true",
                        help="use a subset for training to debug your training loop")
    parser.add_argument("--debug_transformation", action="store_true",
                        help="print a few transformed examples for debugging")
    parser.add_argument("--learning_rate", type=float, default=2e-5)
    parser.add_argument("--num_epochs", type=int, default=2)
    parser.add_argument("--batch_size", type=int, default=8)

    args = parser.parse_args()

    global device
    global tokenizer

    # Device
    device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")

    # Load the tokenizer
    tokenizer = AutoTokenizer.from_pretrained("bert-base-cased")

    # Tokenize the dataset
    dataset = load_dataset("imdb")
    tokenized_dataset = dataset.map(tokenize_function, batched=True)

    # Prepare dataset for use by model
    tokenized_dataset = tokenized_dataset.remove_columns(["text"])
    tokenized_dataset = tokenized_dataset.rename_column("label", "labels")
    tokenized_dataset.set_format("torch")

    small_train_dataset = tokenized_dataset["train"].shuffle(seed=42).select(range(4000))
    small_eval_dataset = tokenized_dataset["test"].shuffle(seed=42).select(range(1000))

    # Create dataloaders for iterating over the dataset
    if args.debug_train:
        train_dataloader = DataLoader(small_train_dataset, shuffle=True, batch_size=args.batch_size)
        eval_dataloader = DataLoader(small_eval_dataset, batch_size=args.batch_size)
        print(f"Debug training...")
        print(f"len(train_dataloader): {len(train_dataloader)}")
        print(f"len(eval_dataloader): {len(eval_dataloader)}")
    else:
        train_dataloader = DataLoader(tokenized_dataset["train"], 
                                      shuffle=True, 
                                      batch_size=args.batch_size,
                                      num_workers=4,
                                      pin_memory=True,
                                      persistent_workers=True
                                      )
        eval_dataloader = DataLoader(tokenized_dataset["test"], 
                                     batch_size=args.batch_size,
                                     num_workers=4,
                                      pin_memory=True,
                                      persistent_workers=True)
        print(f"Actual training...")
        print(f"len(train_dataloader): {len(train_dataloader)}")
        print(f"len(eval_dataloader): {len(eval_dataloader)}")

    # Train model on the original training dataset
    if args.train:
        model = AutoModelForSequenceClassification.from_pretrained("bert-base-cased", num_labels=2)
        model.to(device)
        do_train(args, model, train_dataloader, save_dir="./out")
        # Change eval dir
        args.model_dir = "./out"

    # Train model on the augmented training dataset
    if args.train_augmented:
        train_dataloader = create_augmented_dataloader(args, dataset)
        model = AutoModelForSequenceClassification.from_pretrained("bert-base-cased", num_labels=2)
        model.to(device)
        do_train(args, model, train_dataloader, save_dir="./out_augmented")
        # Change eval dir
        args.model_dir = "./out_augmented"

    # Evaluate the trained model on the original test dataset
    if args.eval:
        out_file = os.path.basename(os.path.normpath(args.model_dir))
        out_file = out_file + "_original.txt"
        score = do_eval(eval_dataloader, args.model_dir, out_file)
        print("Score: ", score)

    # Evaluate the trained model on the transformed test dataset
    if args.eval_transformed:
        out_file = os.path.basename(os.path.normpath(args.model_dir))
        out_file = out_file + "_transformed.txt"
        eval_transformed_dataloader = create_transformed_dataloader(args, dataset, args.debug_transformation)
        score = do_eval(eval_transformed_dataloader, args.model_dir, out_file)
        print("Score: ", score)

    end_time = time.time()
    elapsed_time = end_time - start_time
    print(f"Total elapsed time: {elapsed_time:.2f} seconds")