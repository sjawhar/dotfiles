# Markdown tables


- Escape every `|` in a table cell, even inside backticks (`` head \| wc ``). GitHub splits the row before it parses code spans.
- Check tables in GitHub's rendered view. Compare each row's field count to the header's: `sed 's/\\|//g' FILE | awk -F'|' '{print NR": "NF}'`.
