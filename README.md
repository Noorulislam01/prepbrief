# PrepBrief

A personal UPSC current-affairs tracker: read news feeds, tag articles by subject and
topic with an LLM, highlight key lines, and study them in a clean reader.

## Status (see CLAUDE.md for full project context)
- [x] Phase 1: project setup, database schema, taxonomy
- [x] Phase 2: RSS fetcher
- [x] Phase 3: article text import flow
- [x] Phase 4: Gemini tagging, highlights, takeaways
- [x] Phase 5: FastAPI backend
- [x] Phase 6: frontend list and reader
- [x] Phase 7: notes, saved notebook, study mode, time left
- [x] Phase 8: corrections, private test set export, taxonomy tuning report
- [x] Phase 9: article/note/reference embeddings, related articles, reference library, Ask my notes
- [x] Phase 10: hourly RSS fetching through Windows Task Scheduler
- [x] Phase 11: attempt article text extraction from a public RSS source link
- [x] Phase 12: article section pill filters
- [x] Phase 13: sign-up/login and private API sessions
- [x] Phase 14: multiple accounts with per-account private data

## Setup
1. Create a free Neon project and copy its connection string.
2. `cp .env.example .env` and paste the string into `DATABASE_URL`.
3. Create a virtual environment and install packages:
   ```
   python -m venv .venv
   source .venv/bin/activate      # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
4. Add your Gemini API key to `.env` as `GEMINI_API_KEY`. The default model is
   `gemini-3.5-flash-lite`; set `GEMINI_MODEL` to override it.
5. Create the tables and load topics: `python -m db.init_db`
6. Fetch articles: `python -m pipeline.fetch_rss`

## Personal account sign-in
PrepBrief requires a login before opening article, note, reference, or Ask pages.
People can create separate accounts; article text, tags, reading progress, notes,
corrections, references, and embeddings are isolated by account. Passwords are stored
as scrypt hashes, and browser sessions use HttpOnly cookies. Accounts sign up and log
in with an email address; choosing a username during sign-up is optional. The verified
signup email is also used for password recovery. Existing accounts can add or change
their account email from **Account email** in the app navigation. **Forgot password?**
sends a short-lived one-time code to the verified account email.

After updating an existing checkout, run `python -m db.init_db` once to migrate existing
content to the first account and add per-user ownership and row-level database policies,
then restart the app. For a public HTTPS deployment, set `APP_COOKIE_SECURE=true` in
`.env`. To enable verification and reset emails, set `SMTP_HOST`, `SMTP_PORT`,
`SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_FROM` in `.env` (the example values use Gmail
SMTP; use an app password if your mail provider requires one). Then run
`python -m db.init_db` once to add the email verification tables and restart the app.
RSS items are copied into each account's feed when it signs up. CLI maintenance
commands select the sole account automatically; once there are multiple accounts, pass
`--username` to choose whose private data they should use.

## Schedule RSS fetching on Windows
Run this once from PowerShell in the project folder:

```powershell
.\scripts\install_rss_task.ps1
```

Windows Task Scheduler fetches RSS every hour while your account is signed in and the PC is awake.
Logs are written to `.logs/rss-fetch.log` (the `.logs/` folder is excluded from Git). To remove
the task, run `Unregister-ScheduledTask -TaskName 'PrepBrief-RSS-Fetch' -Confirm:$false`.

## Add article text
After fetching, use the article's database ID and a UTF-8 text file containing
full text that you have legitimate access to:

```
python -m pipeline.import_text 12 article.txt
```

On Windows, you can also paste text into the command:

```
Get-Clipboard | python -m pipeline.import_text 12 -
```

The importer normalizes whitespace, estimates reading time at 200 words per
minute, and marks the article `text_ready`. Replacing text clears AI-generated
tags, highlights, and takeaways so they can be regenerated from the new text.

## Analyze an article
After importing its full text, run:

```
python -m pipeline.tag_article 12
```

Gemini returns structured tags, must-read status, highlights, takeaways, and
confidence. The pipeline checks tags against the configured taxonomy, retries
once if the output fails validation, stores only highlights found in the text,
and marks low-confidence or incomplete results `needs_review`.

## Start the API
With `DATABASE_URL` set and the schema initialized, run:

```
uvicorn app.main:app --reload
```

The API docs are available at `http://127.0.0.1:8000/docs`. It provides article
list/detail/related routes, day summaries, read/save actions, notes, corrections,
highlights, references, Ask my notes, and taxonomy. Article lists accept `date`,
`subject`, `topic`, `source`, `must_read`, `saved`, and `q` filters, with `limit`
and `offset` pagination.

Open `http://127.0.0.1:8000/` for the reader UI. It starts with today's
articles and supports search, date/subject/topic/must-read filters, pagination,
article highlights, takeaways, and marking an article as read.
The notebook tab shows saved articles. The reader supports personal notes and
saved status, and Study mode moves through articles published on the selected
day. The daily progress panel shows unread reading time for the selected date.

## Correct an analysis and tune tags
In an article reader, open **Correct this classification**, update relevance,
must-read status, or subject/topic pairs, then save. Corrections are applied to
the article and the before/after values are kept as private examples.

Review repeated tag changes with:

```
python -m pipeline.correction_report
```

Export examples, including article text, to a local JSONL test set with:

```
python -m pipeline.export_corrections
```

The default export is `data/corrections_test_set.jsonl`; the `data/` folder is
ignored by Git because it contains private article text. Use recurring errors
to refine descriptions in `config/taxonomy.yaml`, then run
`python -m db.init_db` to reload the taxonomy. The correction report does not
change the taxonomy automatically.

## References and Ask my notes
Open **References** in the app to add your own concise summaries of schemes,
judgements, committees, reports, and other exam references. Tag each entry with
the configured taxonomy. New references and article notes are embedded with
Gemini for semantic search. The reader ranks related articles by passage
similarity and shared taxonomy tags.

**Ask my notes** retrieves passages from imported articles, personal notes, and
reference summaries, then asks Gemini to answer with citations. It uses only
full text that you added to PrepBrief yourself; RSS fetching continues to save
feed metadata only. New notes and references are indexed when saved. To index
existing notes and references, run:

```
python -m pipeline.index_personal_notes
```

To embed imported article text for related-article search and Ask my notes, run:

```
python -m pipeline.index_articles
```

Article text is split into overlapping passages for retrieval. Replacing an
article's text clears its previous embeddings; run the indexing command again.

The local database stores 768-dimension embeddings as JSONB, so pgvector is not
required. The Gemini embedding model can be changed with `GEMINI_EMBEDDING_MODEL`
in `.env` (default `gemini-embedding-2`).

## Notes
- Confirm the feed URLs in `config/feeds.yaml` against https://indianexpress.com/rss.
- Keep imported article text in the private local database. Article text is sent to Gemini
  for personal tagging, embeddings, and Ask my notes when you run those features.
- Full article text must be pasted/imported by you; the app does not scrape article pages
  or bypass paywalls.

### Add article text and analyze automatically
Open an article without body text, paste text you can access into the reader, and choose **Save & analyze with Gemini**. PrepBrief saves the text, requests Gemini tags and takeaways, and creates semantic embedding chunks automatically. If either Gemini step fails, the text remains saved and the reader reports which step failed; retry by replacing/re-submitting the text. RSS continues to provide metadata only.

### Fetch text from an article link
In the article reader, choose **Fetch text from source link** to try extracting readable text from its public page. PrepBrief accepts HTTPS links from the configured RSS source domains, follows only same-source redirects, and rejects oversized or very short extractions that look like previews. When extraction succeeds, it saves the text and runs Gemini tagging and embeddings. Pages requiring sign-in, blocking requests, or returning only a preview will need the existing paste workflow. The fetcher does not bypass access controls or paywalls.

The article list includes section pills for Opinion, Explained, India, World, Business, Technology, and Other. Select a pill to filter the selected day's feed or saved notebook by section.
