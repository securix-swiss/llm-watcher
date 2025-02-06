# Elasticsearch LLM Watcher

## Overview

This script is designed to process documents from an Elasticsearch index, run them through an LLM (OpenAI or Ollama), and store the processed output back into Elasticsearch. It uses a painless script and an ingest pipeline to queue documents for processing.

## Features

- Fetches documents from Elasticsearch for processing.
- Supports both OpenAI and Ollama as LLM providers.
- Uses Jinja2 for templating the LLM prompt.
- Writes processed documents back to their original index.
- Logs processing errors and retries failed documents.
- Supports batch processing with configurable interval.

## Prerequisites

### Required Dependencies
Ensure you have the following dependencies installed:

We recommend using a virtual environment to install the dependencies:

```sh
python3 -m venv venv
source venv/bin/activate
```

```sh
pip install requests jinja2
```

### Production installation

Install python dependencies:

```sh
sudo apt-get install python3-requests python3-jinja2
```

Create a user for the service:

```sh
sudo useradd llm-watcher
```

Clone the repository:

```sh
git clone https://github.com/elastic/elasticsearch-llm-watcher.git /opt/llm-watcher
sudo chown -R llm-watcher:llm-watcher /opt/llm-watcher
```

Install the systemd service:

```sh
sudo cp systemd/llm-watcher.service /etc/systemd/system/
```

Edit the service file to set the environment variables:

```sh
sudo vim /etc/systemd/system/llm-watcher.service
```

Reload the systemd daemon:

```sh
sudo systemctl daemon-reload
```

Enable and start the service:

```sh
sudo systemctl enable llm-watcher
sudo systemctl start llm-watcher
```

### Required Elasticsearch Configuration

This script relies on an Elasticsearch painless script and an ingest pipeline.

#### Painless Script

```json
POST _scripts/llm
{
  "script": {
    "lang": "painless",
    "source": """
        ctx['_llm_watcher'] = new HashMap();

        ctx['_llm_watcher']['provider'] = params.containsKey('provider') ? params['provider'] : "ollama";
        ctx['_llm_watcher']['model'] = params.containsKey('model') ? params['model'] : "llama3.3";
        ctx['_llm_watcher']['prompt'] = params.containsKey('prompt') ? params['prompt'] : null;
        ctx['_llm_watcher']['format'] = params.containsKey('format') ? params['format'] : new HashMap(); // Ensure it's an object
        ctx['_llm_watcher']['options'] = params.containsKey('options') ? params['options'] : null;

        ctx['_llm_watcher']['_original_index'] = ctx['_index'];
        ctx['_index'] = params.containsKey('queue_index') ? params['queue_index'] : 'llm-queue';
    """
  }
}
```

#### Ingest Pipeline

```json
PUT _ingest/pipeline/llm
{
  "processors": [
    {
      "script": {
        "id": "llm",
        "params": {
          "provider": "ollama",
          "model": "deepseek-r1:14b",
          "prompt": "From a scale of 1-10 how good is this product review (1 is very bad, 10 is very good): {{ctx.message}}",
          "format": {
            "type": "object",
            "properties": {
              "scale": {
                "type": "integer"
              }
            },
            "required": [
              "scale"
            ]
          }
        }
      }
    }
  ]
}
```

## Usage

### Ingesting a Document

```sh
POST /my-index/_doc/2?pipeline=llm
{
  "message": "I love it!"
}
```

### Running the Script

```sh
python app.py \
--elasticsearch http://localhost:9200 \
--elasticsearch-username user \
--elasticsearch-password pass \
--watch-index llm-queue
--ollama-api http://localhost:11434
```

### Command-line Arguments

| Argument                     | Description                                                                                                        |
|------------------------------|--------------------------------------------------------------------------------------------------------------------|
| `--elasticsearch`            | Elasticsearch URL (default: from environment variable `ELASTICSEARCH_URL`).                                        |
| `--ollama-api`               | Ollama API URL (default: from environment variable `OLLAMA_API_URL`).                                              |
| `--openai-api-key`           | OpenAI API Key (default: from environment variable `OPENAI_API_KEY`).                                              |
| `--elasticsearch-username`   | Username for Elasticsearch authentication (default: from environment variable `ELASTICSEARCH_USERNAME`).           |
| `--elasticsearch-password`   | Password for Elasticsearch authentication (default: from environment variable `ELASTICSEARCH_PASSWORD`).           |
| `--batch-size`               | Number of documents to process in a batch (default: 10) LLM will be called in parallel for each document in batch. |
| `--watch-index`              | Elasticsearch index to monitor for new documents (default: `llm-queue`).                                           |
| `--watch-interval`           | Interval in seconds between index checks (default: 10).                                                            |
| `--retry-errors`             | Retry processing documents that previously encountered errors (default: False).                                    |
| `--sort-field`               | Field to sort the documents by (default: none) for processing.                                                     |
| `--debug`                    | Enable debug mode (default: False).                                                                                |

## Processing Workflow

1. The script retrieves documents from the `llm-queue` index (default or --watch-index).
2. It extracts the necessary fields and processes the document using OpenAI or Ollama.
3. The generated output is merged with the original document and stored back in the original index.
4. Successfully processed documents are deleted from the `llm-queue`.
5. If processing fails, the document is updated with an error message and remains in `llm-queue`.

## Example Output

After processing, the document appears in the original index (`my-index`) as follows:

```json
{
  "_index": "my-index",
  "_id": "2",
  "_version": 1,
  "_seq_no": 1,
  "_primary_term": 1,
  "found": true,
  "_source": {
    "message": "I love it!",
    "scale": 5
  }
}
```

## Using `--retry-errors`

If a document fails to process, it remains in the `llm-queue` index with an `_llm_watcher.error` field. Running the script with `--retry-errors` will attempt to process these documents again.

Example:

```sh
python app.py \
--elasticsearch http://localhost:9200 \
--elasticsearch-username user \
--elasticsearch-password pass \
--watch-index llm-queue
--ollama-api http://localhost:11434
--retry-errors
```

This ensures that failed documents are retried until they succeed or are manually removed.

## Logging

The script logs information and errors:

```log
2025-02-06 12:00:00 - INFO - Found 5 documents in llm-queue to process
2025-02-06 12:00:01 - INFO - Processed 4 documents of 5 total. With 1 error
2025-02-06 12:00:01 - ERROR - Error processing document (id: doc_id: 3): OpenAI API failed.
```

## Contributing

If you encounter issues or want to improve the script, feel free to submit a pull request or report an issue.

## License

This project is licensed under the MIT License.

