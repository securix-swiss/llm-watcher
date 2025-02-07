import os
import sys
import copy
import json
import jinja2
import datetime
import logging
import argparse
import requests
import multiprocessing

def get_elasticsearch_docs(
    elasticsearch_url: str,
    elasticsearch_username: str,
    elasticsearch_password: str,
    index: str,
    batch_size: int,
    retry_errors: bool,
    sort_field: str,
    logger: logging.Logger
):
    non_error =  {
        "bool": {
            "must_not": [
                {"exists": {"field": "_llm_watcher.error"}}
            ]
        }
    }

    sort = [
        {
            sort_field: {
                "order": "asc"
            }
        }
    ]

    query = {
        "query": {
            "match_all": {}
        },

        "size": batch_size
    }

    if retry_errors:
        query['query'] = non_error

    if sort_field:
        query['sort'] = sort

    r = requests.post(
        f"{elasticsearch_url}/{index}/_search",
        auth=(elasticsearch_username, elasticsearch_password),
        json=query
    )
    if r.status_code == 404:
        return []

    if r.raise_for_status():
        raise Exception(f"Failed to get documents from Elasticsearch: {r.text}")

    return r.json().get('hits', {}).get('hits', [])

def write_elasticsearch_doc(
    elasticsearch_url: str,
    elasticsearch_username: str,
    elasticsearch_password: str,
    index: str,
    doc_id: str,
    source: dict,
    logger: logging.Logger
):
    r = requests.post(f"{elasticsearch_url}/{index}/_doc/{doc_id}", auth=(elasticsearch_username, elasticsearch_password), json=source)
    if r.raise_for_status():
        raise Exception(f"Failed to write document to Elasticsearch: {r.text}")

def delete_elasticsearch_doc(
    elasticsearch_url: str,
    elasticsearch_username: str,
    elasticsearch_password: str,
    index: str,
    doc_id: str,
    logger: logging.Logger
):
    r = requests.delete(f"{elasticsearch_url}/{index}/_doc/{doc_id}", auth=(elasticsearch_username, elasticsearch_password))
    if r.raise_for_status():
        raise Exception(f"Failed to delete document from Elasticsearch: {r.text}")

def check_args(args, logger: logging.Logger):
    if not args.elasticsearch:
        logger.error("Elasticsearch URL is required (env: ELASTICSEARCH_URL or --elasticsearch)")
        sys.exit(1)

    if not args.elasticsearch_username:
        logger.error("Elasticsearch username is required (env: ELASTICSEARCH_USERNAME or --elasticsearch-username)")
        sys.exit(1)

    if not args.elasticsearch_password:
        logger.error("Elasticsearch password is required (env: ELASTICSEARCH_PASSWORD or --elasticsearch-password)")
        sys.exit(1)

    if not args.ollama_api and not args.openai_api_key:
        logger.error("Neither Ollama API URL nor OpenAI API Key is set, llm will not work")
        sys.exit(1)

    if not args.watch_index:
        logger.error("Elasticsearch index to watch is required (env: WATCH_INDEX or --watch-index)")
        sys.exit(1)

def ollama_generate(
    args,
    model,
    prompt,
    llm_format,
    logger: logging.Logger
):
    data = {
        "model": model,
        "prompt": prompt,
        "format": llm_format,
        "stream": False
    }
    logger.debug(f"Ollama request: {data}")

    url = f"{args.ollama_api}/api/generate"
    r = requests.post(url, json=data, headers={"Content-Type": "application/json"})

    logger.debug(f"Ollama response: {r.text}")
    if r.raise_for_status():
        raise Exception(f"Failed to generate from Ollama: {r.text} / url: {url}")

    data = r.json()
    logger.debug(f"Ollama response: {data}")
    return json.loads(data.get('response', {}))

def openai_generate(
    args,
    model,
    prompt,
    llm_format,
    logger: logging.Logger
):
    data = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "functions": [{
            "name": "generate_output",
            "description": "Generates structured output based on the given format.",
            "parameters": llm_format
        }],
        "function_call": "auto",
        "stream": False
    }

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {args.openai_api_key}",
        "Content-Type": "application/json"
    }

    logger.debug(f"OpenAI request: {data}")
    r = requests.post(url, json=data, headers=headers)

    if r.raise_for_status():
        raise Exception(f"Failed to generate from OpenAI: {r.text} / url: {url}")

    response_data = r.json()
    logger.debug(f"OpenAI response: {response_data}")
    function_args = response_data.get('choices', [{}])[0].get('message', {}).get('function_call', {}).get('arguments', "{}")

    return json.loads(function_args)

def process_document(
    args,
    doc
):
    logger = multiprocessing.get_logger()
    if not logger.hasHandlers():
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    if args.debug:
        logger.setLevel(logging.DEBUG)

    logger.debug(f"Processing document (id: {doc.get('_id', 'unknown')})")
    orig_doc = copy.deepcopy(doc)
    try:
        ctx = doc.get('_source', {})
        doc_id = doc.get('_id', None)
        llm_watcher = ctx.get('_llm_watcher', {})
        original_index = llm_watcher.get('_original_index', None)
        llm_format = llm_watcher.get('format', None)
        provider = llm_watcher.get('provider', None)
        model = llm_watcher.get('model', None)
        prompt = llm_watcher.get('prompt', None)

        prompt_template = jinja2.Template(prompt)
        prompt_rendered = prompt_template.render(ctx=ctx)

        if provider == 'openai':
            logger.debug(f"document {doc_id} using OpenAI to generate")
            response = openai_generate(args, model, prompt_rendered, llm_format, logger)
        elif provider == 'ollama':
            logger.debug(f"document {doc_id} using Ollama to generate")
            response = ollama_generate(args, model, prompt_rendered, llm_format, logger)
        else:
            raise Exception(f"Unknown llm provider: {provider}")

        new_doc = ctx
        new_doc['_llm_watcher']['processed'] = True
        new_doc['_llm_watcher']['output'] = response
        write_elasticsearch_doc(
            args.elasticsearch,
            args.elasticsearch_username,
            args.elasticsearch_password,
            original_index,
            doc_id,
            new_doc,
            logger
        )

        delete_elasticsearch_doc(
            args.elasticsearch,
            args.elasticsearch_username,
            args.elasticsearch_password,
            args.watch_index,
            doc_id,
            logger
        )
    except Exception as e:
        logger.error(f"Error processing document (id: doc_id: {doc.get('_id', 'unknown')}): {e}")
        orig_doc['_source']['_llm_watcher']['error'] = str(e)
        write_elasticsearch_doc(
            args.elasticsearch,
            args.elasticsearch_username,
            args.elasticsearch_password,
            args.watch_index,
            orig_doc['_id'],
            orig_doc['_source'],
            logger
        )
        return False
    return True

def worker_loop(args, logger):
    docs = get_elasticsearch_docs(
        args.elasticsearch,
        args.elasticsearch_username,
        args.elasticsearch_password,
        args.watch_index,
        args.batch_size,
        args.retry_errors,
        args.sort_field,
        logger
    )

    logger.info(f"Found {len(docs)} documents in {args.watch_index} to process")

    errors = 0
    with multiprocessing.Pool() as pool:
        results = pool.starmap(process_document, [(args, doc) for doc in docs])
        pool.close()
        pool.join()

        errors = sum(1 for result in results if not result)

    if len(docs) != 0:
        logger.info(f"Processed {len(docs) - errors} documents of {len(docs)} total. With {errors} errors")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--elasticsearch", type=str, default=os.getenv("ELASTICSEARCH_URL"), help="Elasticsearch URL (env: ELASTICSEARCH_URL)")
    parser.add_argument("--ollama-api", type=str, default=os.getenv("OLLAMA_API_URL"), help="Ollama API URL (env: OLLAMA_API_URL)")
    parser.add_argument("--openai-api-key", type=str, default=os.getenv("OPENAI_API_KEY"), help="OpenAI API Key (env: OPENAI_API_KEY) if set openai will be used instead of ollama")
    parser.add_argument("--elasticsearch-username", type=str, default=os.getenv("ELASTICSEARCH_USERNAME"), help="Username for Elasticsearch authentication (env: ELASTICSEARCH_USERNAME)")
    parser.add_argument("--elasticsearch-password", type=str, default=os.getenv("ELASTICSEARCH_PASSWORD"), help="Password for Elasticsearch authentication (env: ELASTICSEARCH_PASSWORD)")
    parser.add_argument("--batch-size", type=int, default=10, help="Number of documents to process in a single batch")
    parser.add_argument("--watch-index", type=str, default="llm-queue", help="Name of the Elasticsearch index to watch for new documents (default: llm-queue)")
    parser.add_argument("--watch-interval", type=int, default=10, help="Interval in seconds between index checks (default: 10)")
    parser.add_argument("--retry-errors", default=False, action="store_true", help="Retry documents which had errors before (default: False)")
    parser.add_argument("--sort-field", type=str, help="Field to sort the documents by (default: none)")
    parser.add_argument("--debug", default=False, action="store_true", help="Enable debug mode (default: False)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)

    if args.debug:
        logger.setLevel(logging.DEBUG)
        logger.debug(f"Debug mode enabled")
    check_args(args, logger)

    last_run_time = datetime.datetime.now() - datetime.timedelta(seconds=args.watch_interval)
    while True:
        if datetime.datetime.now() - last_run_time > datetime.timedelta(seconds=args.watch_interval):
            last_run_time = datetime.datetime.now()
            worker_loop(args, logger)

if __name__ == "__main__":
    main()