import os

import torch
import time
import transformers
from transformers import T5ForConditionalGeneration, T5Config
from transformers.pytorch_utils import ALL_LAYERNORM_LAYERS
import wandb

DEVICE = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
print(f"[t5_utils] Using device: {DEVICE}")

def setup_wandb(args):
    # Implement this if you wish to use wandb in your experiments
    # pass
    """
    Optional helper to initialize Weights & Biases logging.

    We keep it very defensive so that training still works even if some
    attributes do not exist on `args`.
    """
    # You can control these by adding attributes to args in your argument parser,
    # but we provide reasonable fallbacks so it doesn't crash.
    project = getattr(args, "wandb_project", "nlp_hw4_t5")
    default_run_name = time.strftime("run_%Y%m%d_%H%M%S")
    run_name = getattr(args, "run_name", default_run_name)
    entity = getattr(args, "wandb_entity", None)

    # Log all arguments as config if possible
    if hasattr(args, "__dict__"):
        config = vars(args)
    else:
        config = {}

    wandb.init(
        project=project,
        name=run_name,
        entity=entity,
        config=config,
    )

def initialize_model(args):
    '''
    Helper function to initialize the model. You should be either finetuning
    the pretrained model associated with the 'google-t5/t5-small' checkpoint
    or training a T5 model initialized with the 'google-t5/t5-small' config
    from scratch.
    '''
    # pass
    '''
    Helper function to initialize the model.

    Two modes:
      1) Finetune the pretrained 'google-t5/t5-small' checkpoint
      2) (Optional) Train from scratch using the same config, if args.from_scratch / args.train_from_scratch is True
    '''
    # Which checkpoint / config to use
    model_name = getattr(args, "model_name", "google-t5/t5-small")

    # Whether to start from random initialization (for extra credit)
    train_from_scratch = getattr(args, "from_scratch", False) or getattr(args, "train_from_scratch", False)

    if train_from_scratch:
        # Same architecture as t5-small, but random weights
        config = T5Config.from_pretrained(model_name)
        model = T5ForConditionalGeneration(config)
    else:
        # Standard HW setting: finetune the pretrained T5-small
        model = T5ForConditionalGeneration.from_pretrained(model_name)

    # Optional: freeze some parts of the model if args asks for it
    # (You can ignore these flags if you never set them in your args)
    if getattr(args, "freeze_encoder", False):
        for p in model.encoder.parameters():
            p.requires_grad = False

    if getattr(args, "freeze_embeddings", False):
        # Shared token embedding
        if hasattr(model, "shared"):
            model.shared.weight.requires_grad = False
        # Encoder / decoder input embeddings
        if hasattr(model, "encoder") and hasattr(model.encoder, "embed_tokens"):
            for p in model.encoder.embed_tokens.parameters():
                p.requires_grad = False
        if hasattr(model, "decoder") and hasattr(model.decoder, "embed_tokens"):
            for p in model.decoder.embed_tokens.parameters():
                p.requires_grad = False

    # Move to GPU / CPU
    model.to(DEVICE)
    return model

def mkdir(dirpath):
    if not os.path.exists(dirpath):
        try:
            os.makedirs(dirpath)
        except FileExistsError:
            pass

def save_model(checkpoint_dir, model, best):
    """
    Save model checkpoint to be able to load the model later.

    Args:
        checkpoint_dir (str): directory where checkpoints are stored
        model (nn.Module): T5 model
        best (bool): if True, save as the "best" checkpoint, otherwise as "last"
    """
    mkdir(checkpoint_dir)

    # Decide file name: best vs last
    filename = "best_model.pt" if best else "last_model.pt"
    ckpt_path = os.path.join(checkpoint_dir, filename)

    # In case model is wrapped (e.g. DataParallel), unwrap to get actual module
    model_to_save = model.module if hasattr(model, "module") else model
    state_dict = model_to_save.state_dict()

    torch.save(state_dict, ckpt_path)
    print(f"[save_model] Saved checkpoint to {ckpt_path}")

def load_model_from_checkpoint(args, best):
    # Load model from a checkpoint
    # pass
    """
    Load model from a checkpoint saved by `save_model`.

    Args:
        args: argument namespace (should contain checkpoint_dir / model_name etc.)
        best (bool): if True, load "best" checkpoint, else load "last"

    Returns:
        model (nn.Module): T5 model loaded with checkpoint weights, moved to DEVICE
    """
    # Where checkpoints are stored; fall back to "checkpoints" if not specified
    checkpoint_dir = getattr(args, "checkpoint_dir", "checkpoints")
    filename = "best_model.pt" if best else "last_model.pt"
    ckpt_path = os.path.join(checkpoint_dir, filename)

    if not os.path.exists(ckpt_path):
        raise FileNotFoundError(f"Checkpoint file not found: {ckpt_path}")

    # First build a fresh model with correct architecture/config
    model = initialize_model(args)

    # Load weights
    state_dict = torch.load(ckpt_path, map_location=DEVICE)
    model.load_state_dict(state_dict)

    model.to(DEVICE)
    model.eval()  # usually for evaluation; training script可以再手动model.train()
    print(f"[load_model_from_checkpoint] Loaded checkpoint from {ckpt_path}")
    return model

def initialize_optimizer_and_scheduler(args, model, epoch_length):
    optimizer = initialize_optimizer(args, model)
    scheduler = initialize_scheduler(args, optimizer, epoch_length)
    return optimizer, scheduler

def initialize_optimizer(args, model):
    # decay_parameters = get_parameter_names(model, transformers.pytorch_utils.ALL_LAYERNORM_LAYERS)
    decay_parameters = get_parameter_names(model, ALL_LAYERNORM_LAYERS)
    decay_parameters = [name for name in decay_parameters if "bias" not in name]
    optimizer_grouped_parameters = [
        {
            "params": [
                p for n, p in model.named_parameters() if (n in decay_parameters and p.requires_grad)
            ],
            "weight_decay": args.weight_decay,
        },
        {
            "params": [
                p for n, p in model.named_parameters() if (n not in decay_parameters and p.requires_grad)
            ],
            "weight_decay": 0.0,
        },
    ]

    if args.optimizer_type == "AdamW":
        optimizer = torch.optim.AdamW(
            optimizer_grouped_parameters, lr=args.learning_rate, eps=1e-8, betas=(0.9, 0.999)
        )
    else:
        pass

    return optimizer
        
def initialize_scheduler(args, optimizer, epoch_length):
    num_training_steps = epoch_length * args.max_n_epochs
    num_warmup_steps = epoch_length * args.num_warmup_epochs

    if args.scheduler_type == "none":
        return None
    elif args.scheduler_type == "cosine":
        return transformers.get_cosine_schedule_with_warmup(optimizer, num_warmup_steps, num_training_steps)
    elif args.scheduler_type == "linear":
        return transformers.get_linear_schedule_with_warmup(optimizer, num_warmup_steps, num_training_steps)
    else:
        raise NotImplementedError

def get_parameter_names(model, forbidden_layer_types):
    result = []
    for name, child in model.named_children():
        result += [
            f"{name}.{n}"
            for n in get_parameter_names(child, forbidden_layer_types)
            if not isinstance(child, tuple(forbidden_layer_types))
        ]
    # Add model specific parameters (defined with nn.Parameter) since they are not in any child.
    result += list(model._parameters.keys())
    return result

