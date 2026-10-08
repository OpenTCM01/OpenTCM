# Preparing local knowledge indexes

This public repository provides index builders and application code. Source corpora, full-text PDFs, derived SQLite databases, model outputs, private research, and conversation logs are not included in this code update. Use texts and PDFs that you have permission to process.

## Classical passages

The default classical builder reads two JSONL inputs:

| File | Purpose |
| --- | --- |
| `data/deepseek_success_160k_index.jsonl` | Original paragraphs with `paragraph_id`, `relative_path` (or `book_path`), and `text`. |
| `data/deepseek_outputs_normalized.jsonl` | Normalized extractions keyed by paragraph ID, including entities and triplets. |

Run:

```bash
python scripts/build_paragraph_kg_index.py --rebuild
```

Use `--help` for custom input and output paths. `--rebuild` replaces the builder's output files, so retain backups of any indexes you need.

The merged archive stores the passage identifier, relative path, corrected book title, original text, entities, triplets, and symptom groups. The SQLite database stores passages, entity mentions, and relations with supporting indexes and a full-text search table.

Book titles are inferred from the first unnumbered path segment after the numbered category directories. The dictionary name `中医辞典` is normalized to `中医词典`.

SQLite's FTS5 trigram tokenizer is used when supported, with a unicode61 fallback. FTS means **full-text search**: it creates an index over searchable text for fast candidate retrieval. It is not an LLM or a separate cloud service.

## Modern literature

Place permitted PDFs in `data/modern_literature/`, preserving helpful subject/category subdirectories, then run:

```bash
python scripts/build_modern_literature_index.py
```

The script outputs:

- `data/opentcm_modern.sqlite`: article, entity, relationship, and full-text search tables.
- `data/modern_literature_index.jsonl`: article metadata, available abstracts/conclusions, text excerpts, entities, and relations.
- `data/modern_literature_schema_summary.json`: extraction counts, types, tokenizer, and text-quality summaries.

The builder uses PDF text extraction and local extraction heuristics. Available metadata includes title, authors, publication year, journal, keywords, abstract, conclusion, and evaluation data. Entity and relationship extraction is best-effort; it does not guarantee a complete representation of every paper.

Image-only scans need a separate OCR step. Broken font encodings, incomplete metadata, and poor-quality PDF text can limit retrieval and citation details. Review extracted records against the original papers before using them for research statistics or clinical interpretation.

## Using prepared databases

Instead of rebuilding, point a local `.env` to authorized indexes:

```dotenv
OPENTCM_KG_DB=data/opentcm_kg.sqlite
OPENTCM_MODERN_DB=data/opentcm_modern.sqlite
```

Modern literature is optional. The classical index is needed for the GraphRAG service to initialize. Corpus size alone does not establish answer accuracy; verify individual source passages and assess retrieval quality for your collection.
