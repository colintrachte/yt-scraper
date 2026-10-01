"""Optional RAG generation step. Retrieval is the engine; this is the mouth.

Kept deliberately separate and optional so the search index has no LLM in the
query path (fast, deterministic). When you do want a synthesized answer, this
calls a LOCAL Ollama instance so nothing leaves your machine. Point it at any
model you have pulled (llama3.1, qwen2.5, etc.).
"""
import json
import urllib.request

OLLAMA_URL = "http://localhost:11434/api/generate"


def synthesize(query: str, results: list[dict], model: str = "llama3.1") -> str:
    context = "\n\n".join(
        f"[{i+1}] ({r['title']} @ {int(r['start'])}s)\n{r['text']}"
        for i, r in enumerate(results)
    )
    prompt = (
        "Answer the question using ONLY the transcript excerpts below. "
        "Cite sources inline as [n]. If the answer is not present, say so.\n\n"
        f"QUESTION: {query}\n\nEXCERPTS:\n{context}\n\nANSWER:"
    )
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=payload,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read())["response"].strip()
