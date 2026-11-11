# kwgen

Keyword generator for anime titles. Give it a title; it returns a detailed keyword table (names, hashtags, official account, characters, voice actors, crew, related works, themes, similar titles, merchandise, gaming and retailer terms) and ready search queries in X syntax.

**Demo:** https://eshwarpotturi.github.io/kwgen/

## Web demo
`index.html` runs in the browser. Choose the source:
- **AniList** (free, no key): names, characters, voice actors, crew, related works, themes, similar titles.
- **LLM**: names, arcs, iconic terms, fan slang, hashtags, music, games, collaborations, ambiguity warnings. Needs your own API key (OpenRouter, Anthropic or any OpenAI-compatible endpoint). The page shows a cost estimate and waits for your approval; the key stays in the browser tab.
- **Both**: AniList facts first, then the LLM adds the rest. LLM terms are marked as unverified.

Results are grouped into "Find the show" (safe on their own) and "Context words" (add a title word), with ready search queries and a CSV download.

## Command line
Needs Python 3.9+ and `pip install openpyxl`.

    python kwgen.py build "Kaiju No. 8" "Laid-Back Camp" --out keywords.xlsx
    python kwgen.py validate --keywords keywords.xlsx --posts sample.csv --title "Laid-Back Camp"

`validate` reads a CSV of sample posts (column `text`) and reports which keywords hit, which hits lack a core title term (precision risk), which never hit, and new hashtag candidates.

Optional LLM stage (arcs, fan slang, hashtags, music, games, collaborations, ambiguity warnings). It is a paid call, so it needs an explicit flag:

    python kwgen.py build "Laid-Back Camp" --llm openrouter --model MODEL --estimate        # prints the cost, spends nothing
    python kwgen.py build "Laid-Back Camp" --llm openrouter --model MODEL --approve-spend   # runs it

Keys: `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`, or `LLM_BASE_URL` + `LLM_API_KEY`.

`sample_keywords.xlsx` is the output for four titles.

## Notes
- Ambiguous words (common given names, genre words, voice actors, merchandise words) are flagged "needs title word": use them only together with a core title term.
- Read about 20 rows per category and check Japanese spellings before using a list.
