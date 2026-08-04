"""Input-version-2 payload construction shared by both encode paths.

Documents: `topic.replace("-", " ") + "\\n" + summary.strip()` (no prompt).
Queries: `QUERY_PROMPT + query.strip()`. Byte-exactness here is what makes the
q4/fp32 parity measurement meaningful.
"""

QUERY_PROMPT = (
    "Instruct: Given a web search query, retrieve relevant passages that answer the query"
    "\nQuery: "
)


def document_payload(topic: str, summary: str) -> str:
    return topic.replace("-", " ") + "\n" + summary.strip()


def query_payload(query: str) -> str:
    return QUERY_PROMPT + query.strip()
