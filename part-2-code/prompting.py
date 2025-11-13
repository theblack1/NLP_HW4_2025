import os, argparse, random
from tqdm import tqdm

import torch
from transformers import GemmaTokenizerFast, GemmaForCausalLM
from transformers import GemmaTokenizer, AutoModelForCausalLM
from transformers import BitsAndBytesConfig

from utils import set_random_seeds, compute_metrics, save_queries_and_records, compute_records
from prompting_utils import read_schema, extract_sql_query, save_logs
from load_data import load_prompting_data

DEVICE = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu') # you can add mps
# Global variables for few-shot prompting
TRAIN_X = None
TRAIN_Y = None
MAX_NEW_TOKENS = 256

def get_args():
    '''
    Arguments for prompting. You may choose to change or extend these as you see fit.
    '''
    parser = argparse.ArgumentParser(
        description='Text-to-SQL experiments with prompting.')

    parser.add_argument('-s', '--shot', type=int, default=0,
                        help='Number of examples for k-shot learning (0 for zero-shot)')
    parser.add_argument('-p', '--ptype', type=int, default=0,
                        help='Prompt type')
    parser.add_argument('-m', '--model', type=str, default='gemma',
                        help='Model to use for prompting: gemma (gemma-1.1-2b-it) or codegemma (codegemma-7b-it)')
    parser.add_argument('-q', '--quantization', action='store_true',
                        help='Use a quantized version of the model (e.g. 4bits)')

    parser.add_argument('--seed', type=int, default=42,
                        help='Random seed to help reproducibility')
    parser.add_argument('--experiment_name', type=str, default='experiment',
                        help="How should we name this experiment?")
    args = parser.parse_args()
    return args


def create_prompt(sentence, k):
    '''
    Function for creating a prompt for zero or few-shot prompting.

    Add/modify the arguments as needed.

    Inputs:
        * sentence (str): A text string
        * k (int): Number of examples in k-shot prompting
    '''
    # TODO
    '''
    Function for creating a prompt for zero or few-shot prompting.

    Current behavior:
      - If k <= 0 or no training examples are available, do zero-shot.
      - If k > 0 and TRAIN_X / TRAIN_Y are set, sample up to k examples
        from the training set and include them in the prompt.
    '''
    # Read schema
    schema_path = os.path.join("data", "flight_database.schema")
    schema_text = read_schema(schema_path)

    # Basic instruction for the model
    instruction = (
        "You are an expert data engineer. "
        "Given a natural language question and the database schema, "
        "write a single valid SQL query that answers the question.\n"
        "- Use the table and column names exactly as in the schema.\n"
        "- Do NOT provide any explanation.\n"
        "- Do NOT wrap the query in backticks.\n"
        "- Output only the SQL query."
    )

    # Build few-shot examples if possible
    examples_block = ""
    if k > 0 and TRAIN_X is not None and TRAIN_Y is not None:
        # Number of examples we can actually use
        n_available = len(TRAIN_X)
        n = min(k, n_available)
        # Randomly sample n indices
        indices = random.sample(range(n_available), n)

        parts = []
        for j, idx in enumerate(indices, start=1):
            q = TRAIN_X[idx]
            sql = TRAIN_Y[idx]
            example_txt = (
                f"Example {j}:\n"
                f"Question: {q}\n"
                f"SQL: {sql}"
            )
            parts.append(example_txt)

        examples_block = "\n\n".join(parts) + "\n\n"

    # Put everything together into a single prompt
    prompt = f"{instruction}\n\nDatabase schema:\n{schema_text}\n\n"

    if examples_block:
        prompt += f"Here are {k} example(s):\n{examples_block}"

    prompt += (
        "Now answer the following question.\n"
        f"Question: {sentence}\n"
        "SQL:"
    )

    return prompt


def exp_kshot(tokenizer, model, inputs, k):
    '''
    k-shot prompting experiments using the provided model and tokenizer. 
    This function generates SQL queries from text prompts and evaluates their accuracy.

    Add/modify the arguments and code as needed.

    Inputs:
        * tokenizer
        * model
        * inputs (List[str]): A list of text strings
        * k (int): Number of examples in k-shot prompting
    '''
    raw_outputs = []
    extracted_queries = []

    for i, sentence in tqdm(enumerate(inputs)):
        prompt = create_prompt(sentence, k) # Looking at the prompt may also help

        input_ids = tokenizer(prompt, return_tensors="pt").to(DEVICE)
        outputs = model.generate(**input_ids, max_new_tokens=MAX_NEW_TOKENS) # You should set MAX_NEW_TOKENS
        response = tokenizer.decode(outputs[0], skip_special_tokens=True) # How does the response look like? You may need to parse it
        raw_outputs.append(response)

        # Extract the SQL query
        extracted_query = extract_sql_query(response)
        extracted_queries.append(extracted_query)
    return raw_outputs, extracted_queries


def eval_outputs(eval_x, eval_y, gt_path, model_path,
                 gt_query_records=None, model_query_records=None):
    '''
    Evaluate the outputs of the model by computing the metrics.

    Add/modify the arguments and code as needed.
    '''
    # TODO
    # return sql_em, record_em, record_f1, model_error_msgs, error_rate
    '''
    Evaluate the outputs of the model by computing the metrics.

    Args:
        eval_x, eval_y: not used in this basic implementation, but kept for interface compatibility.
        gt_path (str): path to ground-truth SQL file (e.g. data/dev.sql)
        model_path (str): path to model-generated SQL file
        gt_query_records (str or None): path to pickle with GT records
        model_query_records (str or None): path to pickle with model records

    Returns:
        sql_em, record_em, record_f1, model_error_msgs, error_rate
    '''
    # Use the helper in utils.py to compute EM and F1 based on SQL and records
    sql_em, record_em, record_f1, model_error_msgs = compute_metrics(
        gt_path=gt_path,
        model_path=model_path,
        gt_query_records=gt_query_records,
        model_query_records=model_query_records
    )

    # Compute how many model outputs led to SQL execution errors
    # model_error_msgs is a list of strings ("" if no error, message if error)
    if len(model_error_msgs) > 0:
        num_errors = sum(1 for msg in model_error_msgs if msg is not None and msg != "")
        error_rate = num_errors / len(model_error_msgs)
    else:
        error_rate = 0.0

    return sql_em, record_em, record_f1, model_error_msgs, error_rate


def initialize_model_and_tokenizer(model_name, to_quantize=False):
    '''
    Args:
        * model_name (str): Model name ("gemma" or "codegemma").
        * to_quantize (bool): Use a quantized version of the model (e.g. 4bits)
    
    To access to the model on HuggingFace, you need to log in and review the 
    conditions and access the model's content.
    '''
    if model_name == "gemma":
        model_id = "google/gemma-1.1-2b-it"
        tokenizer = GemmaTokenizerFast.from_pretrained(model_id)
        # Native weights exported in bfloat16 precision, but you can use a different precision if needed
        model = GemmaForCausalLM.from_pretrained(
            model_id,
            torch_dtype=torch.bfloat16, 
        ).to(DEVICE)
    elif model_name == "codegemma":
        model_id = "google/codegemma-7b-it"
        tokenizer = GemmaTokenizer.from_pretrained(model_id)
        if to_quantize:
            nf4_config = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4", # 4-bit quantization
            )
            model = AutoModelForCausalLM.from_pretrained(model_id,
                                                        torch_dtype=torch.bfloat16,
                                                        config=nf4_config).to(DEVICE)
        else:
            model = AutoModelForCausalLM.from_pretrained(model_id,
                                                        torch_dtype=torch.bfloat16).to(DEVICE)
    return tokenizer, model


def main():
    '''
    Note: this code serves as a basic template for the prompting task. You can but 
    are not required to use this pipeline.
    You can design your own pipeline, and you can also modify the code below.
    '''
    args = get_args()
    shot = args.shot
    k = shot
    ptype = args.ptype
    model_name = args.model
    to_quantize = args.quantization
    experiment_name = args.experiment_name

    set_random_seeds(args.seed)

    data_folder = 'data'
    train_x, train_y, dev_x, dev_y, test_x = load_prompting_data(data_folder)
    
    # expose training examples to create_prompt (for k-shot)
    global TRAIN_X, TRAIN_Y
    TRAIN_X, TRAIN_Y = train_x, train_y
    
    # Model and tokenizer
    tokenizer, model = initialize_model_and_tokenizer(model_name, to_quantize)

    for eval_split in ["dev", "test"]:
        eval_x, eval_y = (dev_x, dev_y) if eval_split == "dev" else (test_x, None)
    
        raw_outputs, extracted_queries = exp_kshot(tokenizer, model, eval_x, k)

        # You can add any post-processing if needed
        # You can compute the records with `compute_records``

        # save model-generated SQL and corresponding records
        model_sql_path = os.path.join("results", f"gemma_{experiment_name}_{eval_split}.sql")
        model_record_path = os.path.join("records", f"gemma_{experiment_name}_{eval_split}.pkl")
        save_queries_and_records(extracted_queries, model_sql_path, model_record_path)

        # gt_query_records = f"records/{eval_split}_gt_records.pkl"
        # gt_sql_path = os.path.join(f'data/{eval_split}.sql')
        # gt_record_path = os.path.join(f'records/{eval_split}_gt_records.pkl')
        
        if eval_split == "dev":
            # ---- Only dev has ground truth, so we can compute metrics and save logs ----
            gt_sql_path = os.path.join("data", "dev.sql")
            gt_record_path = os.path.join("records", "ground_truth_dev.pkl")

            sql_em, record_em, record_f1, model_error_msgs, error_rate = eval_outputs(
                eval_x, eval_y,
                gt_path=gt_sql_path,
                model_path=model_sql_path,
                gt_query_records=gt_record_path,
                model_query_records=model_record_path
            )

            print(f"{eval_split} set results: ")
            print(f"Record F1: {record_f1:.4f}, Record EM: {record_em:.4f}, SQL EM: {sql_em:.4f}")
            print(f"{eval_split} set: {error_rate*100:.2f}% of the generated outputs led to SQL errors")

            # Write the metrics and error messages to a log file
            log_path = os.path.join("logs", f"gemma_{experiment_name}_{eval_split}.txt")
            save_logs(log_path, sql_em, record_em, record_f1, model_error_msgs)

        else:
            # ---- The test set has no ground truth, so we only save the predictions ----
            print(f"{eval_split} set: saved predictions to {model_sql_path} and {model_record_path}")
            # Since it is not mandatory to log test set results, we skip save_logs here.
        
        # model_sql_path = os.path.join(f'results/gemma_{experiment_name}_dev.sql')
        # model_record_path = os.path.join(f'records/gemma_{experiment_name}_dev.pkl')
        # save_queries_and_records(extracted_queries, model_sql_path, model_record_path) # Save generated queries and records 
        # sql_em, record_em, record_f1, model_error_msgs, error_rate = eval_outputs(
        #     eval_x, eval_y,
        #     gt_path=gt_sql_path,
        #     model_path=model_sql_path,
        #     gt_query_records=gt_query_records,
        #     model_query_records=model_record_path
        # )
        # print(f"{eval_split} set results: ")
        # print(f"Record F1: {record_f1}, Record EM: {record_em}, SQL EM: {sql_em}")
        # print(f"{eval_split} set results: {error_rate*100:.2f}% of the generated outputs led to SQL errors")

        # # Save results
        # # You can for instance use the `save_queries_and_records` function

        # # Save logs, if needed
        # log_path = "" # to specify
        # save_logs(log_path, sql_em, record_em, record_f1, model_error_msgs)


if __name__ == "__main__":
    main()